"""
Dual UHF RFID Subsystem & Warehouse Gate Logistics Engine.
Features:
- Automatic Plug-and-Play Serial COM Port Detection using serial.tools.list_ports
- Dynamically assigns:
  * Port 1 -> Check-In (Entry Gate)
  * Port 2 -> Check-Out (Exit Gate)
- Reconnection Watchdog: Automatically detects plugged/unplugged readers without restarting
- SRK-UDR6 Protocol Parser (0x52 0x46 header, 12-byte / 24-char hex EPC)
- Predefined tag metadata lookup and Dwell Time calculation
- Realtime persistence to Supabase Cloud DB
"""

import os
import sys
import time
import serial
import serial.tools.list_ports
import threading
from datetime import datetime
from typing import Dict, List, Optional, Callable

from config import (
    RFID_CHECKIN_PORT, RFID_CHECKOUT_PORT, RFID_BAUDRATE,
    RFID_HEADER, RFID_FRAME_MIN_LEN, RFID_EPC_OFFSET, RFID_EPC_LENGTH,
    RFID_DEBOUNCE_SEC, get_tag_metadata
)
from audio_manager import audio
from db_manager import db

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

            # Ignore onboard Linux serial pins and bluetooth (/dev/ttyAMA*, /dev/ttyS*)
            if "ttyama" in dev.lower() or "ttys" in dev.lower() or "rfcomm" in dev.lower():
                continue

            # Accept genuine USB adapters: /dev/ttyUSB*, /dev/ttyACM*, or USB in description/hwid
            if dev.startswith("/dev/ttyUSB") or dev.startswith("/dev/ttyACM"):
                detected.append(dev)
            elif "usb" in desc or "usb" in hwid or "vid" in hwid or dev.upper().startswith("COM"):
                if dev.upper() != "COM1": # Exclude motherboard legacy COM1 on Windows
                    detected.append(dev)
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
                    print(f" -> Protocol: SRK-UDR6 @ {self.baudrate} baud")
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
                print(f"✅ [IN GATE ONLINE] Listening on {target_checkin} @ {self.baudrate} baud.")

        # Connect Gate 2 (EXIT GATE)
        if len(discovered_order) >= 2 and not self.ser_checkout:
            target_checkout = discovered_order[1]
            self.ser_checkout = self._open_serial(target_checkout, "EXIT GATE (Dispatch / Check-Out)")
            if self.ser_checkout:
                self.checkout_port = target_checkout
                print(f"✅ [EXIT GATE ONLINE] Listening on {target_checkout} @ {self.baudrate} baud.")

        if not self.ser_checkin and not self.ser_checkout:
            print(" ℹ️ [RFID STANDBY] No USB reader active during initial 10s. Hotplug watchdog active.\n")

    def _is_port_valid(self, p: Optional[str]) -> bool:
        return bool(p and (os.path.exists(p) or p.upper().startswith("COM")))

    def _open_serial(self, port_name: str, label: str) -> Optional[serial.Serial]:
        try:
            ser = serial.Serial(
                port=port_name,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.2
            )
            print(f"✅ [RFID CONNECTED] {label} online on '{port_name}' @ {self.baudrate} baud.")
            return ser
        except Exception as e:
            print(f"[RFID ERROR] Could not open {label} on '{port_name}': {e}")
            return None

    def start_background_scanning(self):
        """Starts background reader threads and the hotplug watchdog."""
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

        print("[RFID] Gate monitoring loops and auto-reconnect watchdog active.")

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
        """High-speed parsing loop for SRK-UDR6 frames."""
        port_name = ser_conn.port
        print(f"[RFID LOOP] Listening on {gate_type} ({port_name})...")

        buffer = bytearray()
        while self._running:
            try:
                if ser_conn.in_waiting > 0:
                    chunk = ser_conn.read(ser_conn.in_waiting)
                    buffer.extend(chunk)

                    # Look for 0x52 0x46 frame header
                    while len(buffer) >= RFID_FRAME_MIN_LEN:
                        idx = buffer.find(RFID_HEADER)
                        if idx == -1:
                            buffer = buffer[-1:]
                            break

                        if idx > 0:
                            buffer = buffer[idx:]

                        if len(buffer) < RFID_FRAME_MIN_LEN:
                            break

                        epc_end = RFID_EPC_OFFSET + RFID_EPC_LENGTH
                        if len(buffer) >= epc_end:
                            epc_bytes = buffer[RFID_EPC_OFFSET:epc_end]
                            epc = epc_bytes.hex().upper()
                            self._handle_tag_scanned(epc, gate_type, port_name)
                            buffer = buffer[epc_end:]
                        else:
                            break

                time.sleep(0.02)
            except Exception as e:
                time.sleep(1.0)

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

                print(f"📦 [ENTRY GATE - CHECK IN] {meta['product_name']} ({epc}) Scanned!")
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

                print(f"🚚 [EXIT GATE - CHECK OUT] {meta['product_name']} ({epc}) Exited! Dwell: {dwell}s")
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
