"""
Dual UHF RFID Subsystem & Warehouse Gate Logistics Engine.
Features:
- Active EPC Class 1 Gen 2 Inventory Polling ([0x04, 0x00, 0x01, 0xDB, 0x4B])
- Multi-Baudrate Auto-Negotiation (115200, 57600, 9600)
- Support for /dev/ttyUSB*, /dev/ttyACM*, and Linux USB HID Desktop Readers (/dev/hidraw*)
- Dynamically assigns:
  * Port 1 -> Check-In (Entry Gate)
  * Port 2 / HID -> Check-Out (Exit Gate)
- Realtime persistence to Supabase Cloud DB
- Debouncing and dwell time calculation
"""

import os
import sys
import glob
import time
import serial
import serial.tools.list_ports
import threading
from datetime import datetime
from typing import Dict, List, Optional, Callable

from config import (
    RFID_CHECKIN_PORT, RFID_CHECKOUT_PORT, RFID_BAUDRATE,
    RFID_DEBOUNCE_SEC, get_tag_metadata
)
from audio_manager import audio
from db_manager import db

def calculate_crc(data: bytes) -> bytes:
    """Calculates CRC-16 CCITT checksum (Poly: 0x8408, Init: 0xFFFF, LSB first)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc = crc >> 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])

# Pre-calculated EPC Gen2 Inventory Command Frame
# [Length: 0x04, Address: 0x00, Command: 0x01] + CRC: [0xDB, 0x4B]
INV_POLL_PACKET = bytes([0x04, 0x00, 0x01, 0xDB, 0x4B])

class TagRecord:
    def __init__(self, epc: str, metadata: dict, gate: str):
        self.epc = epc.upper()
        self.metadata = metadata
        self.first_seen = time.time()
        self.last_seen = self.first_seen
        self.check_in_time = self.first_seen if gate == "CHECK_IN" else None
        self.check_out_time = self.first_seen if gate == "CHECK_OUT" else None
        self.status = "IN_WAREHOUSE" if gate == "CHECK_IN" else "DISPATCHED"
        self.dwell_seconds = 0
        self.read_count = 1

    def to_dict(self):
        return {
            "epc": self.epc,
            "product_name": self.metadata.get("product_name", f"Item-{self.epc[:8]}"),
            "category": self.metadata.get("category", "General"),
            "unit_weight_kg": self.metadata.get("unit_weight_kg", 1.0),
            "target_zone": self.metadata.get("target_zone", "Zone-A"),
            "status": self.status,
            "first_seen": datetime.fromtimestamp(self.first_seen).strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen": datetime.fromtimestamp(self.last_seen).strftime("%Y-%m-%d %H:%M:%S"),
            "dwell_seconds": int(self.dwell_seconds),
            "read_count": self.read_count
        }

class DualRFIDManager:
    def __init__(self):
        self.checkin_port: Optional[str] = RFID_CHECKIN_PORT
        self.checkout_port: Optional[str] = RFID_CHECKOUT_PORT
        self.baudrate = RFID_BAUDRATE

        self.ser_checkin: Optional[serial.Serial] = None
        self.ser_checkout: Optional[serial.Serial] = None

        self._running = False
        self._threads: List[threading.Thread] = []
        self._watchdog_thread: Optional[threading.Thread] = None
        self._hid_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # In-memory warehouse registry { epc: TagRecord }
        self.active_inventory: Dict[str, TagRecord] = {}
        self._last_scan_times: Dict[str, float] = {}
        self._callbacks: List[Callable] = []

    def register_callback(self, cb: Callable):
        self._callbacks.append(cb)

    @staticmethod
    def get_available_com_ports() -> List[str]:
        """Automatically detects plugged-in USB serial COM ports, strictly ignoring onboard UARTs."""
        ports = serial.tools.list_ports.comports()
        detected = []
        for p in ports:
            dev = p.device
            desc = (p.description or "").lower()
            hwid = (p.hwid or "").lower()

            # Ignore onboard Linux serial pins and bluetooth (/dev/ttyAMA*, /dev/ttyS*, /dev/rfcomm*)
            if any(k in dev.lower() for k in ["ttyama", "ttys", "rfcomm"]):
                continue

            # Accept genuine USB adapters: /dev/ttyUSB*, /dev/ttyACM*, or USB in description/hwid
            if dev.startswith("/dev/ttyUSB") or dev.startswith("/dev/ttyACM"):
                detected.append(dev)
            elif "usb" in desc or "usb" in hwid or "vid" in hwid or dev.upper().startswith("COM"):
                if dev.upper() != "COM1": # Exclude motherboard legacy COM1 on Windows
                    detected.append(dev)

        # Also inspect /dev/serial/by-id/* symlinks on Linux
        if sys.platform.startswith("linux") and os.path.exists("/dev/serial/by-id"):
            try:
                for s in glob.glob("/dev/serial/by-id/*"):
                    real_path = os.path.realpath(s)
                    if real_path not in detected:
                        detected.append(real_path)
            except Exception:
                pass

        return sorted(list(set(detected)))

    def connect(self):
        """Monitors for USB RFID readers during initial 10-second window and assigns In Gate and Exit Gate."""
        print("\n" + "=" * 65)
        print(" 🔍 [RFID AUTO-DETECT] 10-SECOND DISCOVERY WINDOW STARTED")
        print(" -> Waiting for USB RFID Readers to be plugged in / initialized...")
        print(" -> 1st USB Port Detected ===> Assigned to IN GATE (Check-In)")
        print(" -> 2nd USB Port Detected ===> Assigned to EXIT GATE (Check-Out)")
        print("=" * 65)

        start_time = time.time()
        discovered_order: List[str] = []

        # Countdown loop for 10 seconds, polling every second
        for remaining in range(10, 0, -1):
            current_usb = self.get_available_com_ports()
            for port in current_usb:
                if port not in discovered_order:
                    discovered_order.append(port)
                    gate_num = len(discovered_order)
                    gate_name = "IN GATE (Entry / Check-In)" if gate_num == 1 else "EXIT GATE (Dispatch / Check-Out)"
                    print("\n" + "#" * 65)
                    print(f" 🚪 [PORT DETECTED #{gate_num}] {port}")
                    print(f" -> Assigned to: {gate_name}")
                    print(f" -> Active EPC Gen2 Inventory Poller Ready")
                    print("#" * 65 + "\n")

            if len(discovered_order) >= 2:
                print(" ✅ [RFID AUTO-DETECT] Both In Gate and Exit Gate discovered! Finalizing setup...\n")
                break

            print(f" [RFID DISCOVERY] Scanning USB ports... ({remaining}s remaining)")
            time.sleep(1.0)

        # Final check after countdown
        for port in self.get_available_com_ports():
            if port not in discovered_order:
                discovered_order.append(port)

        print(f"\n[RFID SUMMARY] Discovered USB Hardware Ports in sequence: {discovered_order}")

        # Connect Gate 1 (IN GATE)
        if len(discovered_order) >= 1 and not self.ser_checkin:
            target_checkin = discovered_order[0]
            self.ser_checkin = self._open_serial(target_checkin, "IN GATE (Entry / Check-In)")
            if self.ser_checkin:
                self.checkin_port = target_checkin

        # Connect Gate 2 (EXIT GATE)
        if len(discovered_order) >= 2 and not self.ser_checkout:
            target_checkout = discovered_order[1]
            self.ser_checkout = self._open_serial(target_checkout, "EXIT GATE (Dispatch / Check-Out)")
            if self.ser_checkout:
                self.checkout_port = target_checkout

        if not self.ser_checkin and not self.ser_checkout:
            print(" ℹ️ [RFID STANDBY] No USB serial reader active yet. Hotplug watchdog and HID scanner active.\n")

    def _open_serial(self, port_name: str, label: str) -> Optional[serial.Serial]:
        """Opens serial port with baudrate auto-detection (115200, 57600, 9600)."""
        candidate_bauds = [self.baudrate, 57600, 9600]
        # Remove duplicate
        candidate_bauds = list(dict.fromkeys(candidate_bauds))

        for baud in candidate_bauds:
            try:
                ser = serial.Serial(
                    port=port_name,
                    baudrate=baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.15
                )
                print(f"✅ [RFID CONNECTED] {label} online on '{port_name}' @ {baud} baud.")
                return ser
            except Exception as e:
                pass

        print(f"[RFID ERROR] Could not open {label} on '{port_name}' with tested baudrates.")
        return None

    def start_background_scanning(self):
        """Starts background reader threads, hotplug watchdog, and HID desktop reader scanner."""
        if self._running:
            return
        self._running = True

        if self.ser_checkin:
            t1 = threading.Thread(target=self._reader_loop, args=(self.ser_checkin, "CHECK_IN"), daemon=True)
            self._threads.append(t1)
            t1.start()

        if self.ser_checkout:
            t2 = threading.Thread(target=self._reader_loop, args=(self.ser_checkout, "CHECK_OUT"), daemon=True)
            self._threads.append(t2)
            t2.start()

        # Launch background watchdog to reconnect if cables are plugged/unplugged
        self._watchdog_thread = threading.Thread(target=self._hotplug_watchdog, daemon=True)
        self._watchdog_thread.start()

        # Launch USB HID Desktop Reader worker (handles small black desktop readers)
        self._hid_thread = threading.Thread(target=self._hid_keyboard_loop, daemon=True)
        self._hid_thread.start()

        print("[RFID] Active EPC Gen2 inventory polling & auto-reconnect watchdog active.")

    def _hotplug_watchdog(self):
        """Periodically checks if readers were plugged in or need reconnection."""
        while self._running:
            time.sleep(3.0)
            if not self.ser_checkin or not self.ser_checkout:
                try:
                    detected = self.get_available_com_ports()
                    used = [s.port for s in [self.ser_checkin, self.ser_checkout] if s]
                    available = [p for p in detected if p not in used]

                    if not self.ser_checkin and len(available) > 0:
                        port = available.pop(0)
                        print("\n" + "#" * 65)
                        print(f" 🚪 [HOTPLUG DETECTED] {port} -> Assigned to: IN GATE (Entry / Check-In)")
                        print("#" * 65 + "\n")
                        self.ser_checkin = self._open_serial(port, "IN GATE (Entry / Check-In)")
                        if self.ser_checkin:
                            self.checkin_port = port
                            t = threading.Thread(target=self._reader_loop, args=(self.ser_checkin, "CHECK_IN"), daemon=True)
                            self._threads.append(t)
                            t.start()

                    if not self.ser_checkout and len(available) > 0:
                        port = available.pop(0)
                        print("\n" + "#" * 65)
                        print(f" 🚪 [HOTPLUG DETECTED] {port} -> Assigned to: EXIT GATE (Dispatch / Check-Out)")
                        print("#" * 65 + "\n")
                        self.ser_checkout = self._open_serial(port, "EXIT GATE (Dispatch / Check-Out)")
                        if self.ser_checkout:
                            self.checkout_port = port
                            t = threading.Thread(target=self._reader_loop, args=(self.ser_checkout, "CHECK_OUT"), daemon=True)
                            self._threads.append(t)
                            t.start()
                except Exception:
                    pass

    def _reader_loop(self, ser_conn: serial.Serial, gate_type: str):
        """Active EPC Class 1 Gen 2 Inventory polling loop for industrial UHF readers."""
        port_name = ser_conn.port
        print(f"📡 [RFID LOOP] Active Inventory Polling started on {gate_type} ({port_name})...")

        buffer = bytearray()
        scan_count = 0

        while self._running:
            try:
                scan_count += 1

                # 1. Transmit EPC Gen2 Inventory Poll Command
                try:
                    ser_conn.reset_input_buffer()
                    ser_conn.write(INV_POLL_PACKET)
                except Exception:
                    time.sleep(0.5)
                    continue

                # 2. Read Response Frame (1st byte is length)
                header = ser_conn.read(1)
                if header:
                    resp_len = header[0]
                    if 4 <= resp_len <= 128:
                        rest = ser_conn.read(resp_len)
                        if len(rest) == resp_len:
                            full_resp = header + rest
                            resp_cmd = full_resp[2]
                            resp_status = full_resp[3]

                            # Status 0x01, 0x02, 0x03, 0x04 = Tags Found!
                            # Status 0xFB, 0xFE = No Tag in Field
                            if resp_status not in [0xFB, 0xFE] and len(full_resp) > 4:
                                tag_count = full_resp[4]
                                idx = 5
                                for _ in range(tag_count):
                                    if idx < len(full_resp) - 2:
                                        epc_len = full_resp[idx]
                                        idx += 1
                                        if idx + epc_len <= len(full_resp) - 2:
                                            epc_bytes = full_resp[idx : idx + epc_len]
                                            idx += epc_len
                                            epc = epc_bytes.hex().upper()
                                            if len(epc) >= 8:
                                                self._handle_tag_scanned(epc, gate_type, port_name)

                # 3. Fallback: Parse continuous stream or auto-reporting (0x52 0x46 header)
                if ser_conn.in_waiting > 0:
                    raw_chunk = ser_conn.read(ser_conn.in_waiting)
                    buffer.extend(raw_chunk)
                    while len(buffer) >= 15:
                        idx_hdr = buffer.find(b'\x52\x46')
                        if idx_hdr == -1:
                            buffer = buffer[-2:]
                            break
                        if idx_hdr > 0:
                            buffer = buffer[idx_hdr:]
                        if len(buffer) < 15:
                            break
                        epc_bytes = buffer[12:24]
                        epc = epc_bytes.hex().upper()
                        if len(epc) >= 8:
                            self._handle_tag_scanned(epc, gate_type, port_name)
                        buffer = buffer[24:]

                time.sleep(0.08) # 80ms scan interval
            except Exception as e:
                time.sleep(0.5)

    def _hid_keyboard_loop(self):
        """Background listener for USB HID Desktop RFID readers (e.g. small black USB reader)."""
        if not sys.platform.startswith("linux"):
            return

        hid_key_map = {
            0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4', 0x22: '5',
            0x23: '6', 0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
            0x04: 'A', 0x05: 'B', 0x06: 'C', 0x07: 'D', 0x08: 'E',
            0x09: 'F', 0x0A: 'G', 0x0B: 'H', 0x0C: 'I', 0x0D: 'J',
            0x0E: 'K', 0x0F: 'L', 0x10: 'M', 0x11: 'N', 0x12: 'O',
            0x13: 'P', 0x14: 'Q', 0x15: 'R', 0x16: 'S', 0x17: 'T',
            0x18: 'U', 0x19: 'V', 0x1A: 'W', 0x1B: 'X', 0x1C: 'Y',
            0x1D: 'Z', 0x28: '\n'
        }

        while self._running:
            try:
                hid_nodes = sorted(glob.glob("/dev/hidraw*"))
                if not hid_nodes:
                    time.sleep(3.0)
                    continue

                for node in hid_nodes:
                    try:
                        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
                        epc_chars = []
                        last_active = time.time()

                        # Read from hidraw node
                        while self._running and (time.time() - last_active < 10.0):
                            try:
                                report = os.read(fd, 16)
                                if report and len(report) >= 3:
                                    last_active = time.time()
                                    # Standard HID report byte 2 is the keycode
                                    keycode = report[2]
                                    if keycode in hid_key_map:
                                        ch = hid_key_map[keycode]
                                        if ch == '\n':
                                            scanned = "".join(epc_chars).strip().upper()
                                            if len(scanned) >= 6:
                                                assigned_gate = "CHECK_OUT" if self.ser_checkin else "CHECK_IN"
                                                print(f"🏷️ [USB-HID READER] Scanned: {scanned} on {node}")
                                                self._handle_tag_scanned(scanned, assigned_gate, f"USB-HID({node})")
                                            epc_chars = []
                                        else:
                                            epc_chars.append(ch)
                            except BlockingIOError:
                                time.sleep(0.04)
                            except Exception:
                                break

                        os.close(fd)
                    except Exception:
                        pass
                time.sleep(2.0)
            except Exception:
                time.sleep(3.0)

    def _handle_tag_scanned(self, epc: str, gate_type: str, port_name: str):
        now = time.time()
        debounce_key = f"{gate_type}:{epc}"

        with self._lock:
            last_seen = self._last_scan_times.get(debounce_key, 0)
            if now - last_seen < RFID_DEBOUNCE_SEC:
                return
            self._last_scan_times[debounce_key] = now

            meta = get_tag_metadata(epc)
            record = self.active_inventory.get(epc)

            dwell = 0
            if gate_type == "CHECK_IN":
                if not record:
                    record = TagRecord(epc, meta, gate="CHECK_IN")
                    self.active_inventory[epc] = record
                else:
                    record.status = "IN_WAREHOUSE"
                    record.read_count += 1
                    record.last_seen = now
                    record.check_in_time = now

                print(f"📦 [ENTRY GATE - CHECK IN] {meta['product_name']} ({epc}) Scanned on {port_name}!")
                db.log_rfid_movement(epc, "CHECK_IN", port_name, 0, meta)

            elif gate_type == "CHECK_OUT":
                if record and record.check_in_time:
                    dwell = int(now - record.check_in_time)
                    record.dwell_seconds = dwell
                    record.status = "DISPATCHED"
                    record.last_seen = now
                    record.check_out_time = now
                    record.read_count += 1
                else:
                    record = TagRecord(epc, meta, gate="CHECK_OUT")
                    record.status = "DISPATCHED"
                    self.active_inventory[epc] = record
                    db.log_security_alert("TAG_UNEXPECTED_EXIT", "WARNING", {
                        "epc": epc,
                        "product_name": meta["product_name"],
                        "gate": port_name
                    })

                print(f"🚚 [EXIT GATE - CHECK OUT] {meta['product_name']} ({epc}) Exited on {port_name}! Dwell: {dwell}s")
                db.log_rfid_movement(epc, "CHECK_OUT", port_name, dwell, meta)

        for cb in self._callbacks:
            try:
                cb({"event": gate_type, "epc": epc, "metadata": meta, "dwell": dwell})
            except Exception:
                pass

    def stop(self):
        """Stops scanner threads and closes serial connections."""
        self._running = False
        if self.ser_checkin:
            try: self.ser_checkin.close()
            except Exception: pass
        if self.ser_checkout:
            try: self.ser_checkout.close()
            except Exception: pass

# Global Singleton
rfid = DualRFIDManager()
