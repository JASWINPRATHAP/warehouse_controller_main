"""
Dual UHF RFID Subsystem & Warehouse Gate Logistics Engine.
Controls Reader 1 (Check-In Gate) and Reader 2 (Check-Out Gate) using the SRK-UDR6 serial protocol:
- Frame header: 0x52 0x46
- EPC Offset: Byte 12, Length: 12 bytes (24 Hex characters)
Syncs real-time tag movement, dwell time, and inventory status into Supabase Cloud DB.
"""

import os
import sys
import glob
import time
import serial
import threading
from datetime import datetime
from typing import Dict, List, Optional, Callable

from config import (
    RFID_CHECKIN_PORT, RFID_CHECKOUT_PORT, RFID_BAUDRATE,
    RFID_HEADER, RFID_FRAME_MIN_LEN, RFID_EPC_OFFSET, RFID_EPC_LENGTH,
    RFID_DEBOUNCE_SEC, get_tag_metadata, AUDIO_MAP
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
        self.checkin_port = RFID_CHECKIN_PORT
        self.checkout_port = RFID_CHECKOUT_PORT
        self.baudrate = RFID_BAUDRATE

        self.ser_checkin: Optional[serial.Serial] = None
        self.ser_checkout: Optional[serial.Serial] = None

        self._running = False
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()

        # In-memory warehouse registry { epc: TagRecord }
        self.active_inventory: Dict[str, TagRecord] = {}
        self._last_scan_times: Dict[str, float] = {}
        self._callbacks: List[Callable] = []

    def register_callback(self, cb: Callable):
        self._callbacks.append(cb)

    @staticmethod
    def find_serial_ports() -> List[str]:
        """Discovers available serial ports on Linux/Windows/Mac."""
        if sys.platform.startswith("linux"):
            return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
        elif sys.platform.startswith("win"):
            return [f"COM{i}" for i in range(1, 32)]
        return glob.glob("/dev/tty.usbserial*")

    def connect(self):
        """Initializes serial connections to Check-In and Check-Out RFID readers."""
        available_ports = self.find_serial_ports()
        print(f"[RFID] Discovered system serial ports: {available_ports}")

        # Connect Check-In Reader
        self.ser_checkin = self._open_port(self.checkin_port, "Check-In (Gate 1)", available_ports)

        # Connect Check-Out Reader (ensure different port from Check-In)
        remaining = [p for p in available_ports if p != (self.ser_checkin.port if self.ser_checkin else None)]
        self.ser_checkout = self._open_port(self.checkout_port, "Check-Out (Gate 2)", remaining)

    def _open_port(self, target_port: str, label: str, fallback_list: List[str]) -> Optional[serial.Serial]:
        # Try configured target first
        ports_to_try = [target_port] + [p for p in fallback_list if p != target_port]
        for p in ports_to_try:
            try:
                s = serial.Serial(
                    port=p,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.2
                )
                print(f"[RFID SUCCESS] {label} connected on {p} @ {self.baudrate} baud.")
                return s
            except Exception:
                continue
        print(f"[RFID WARN] {label} not connected. Reader standby active.")
        return None

    def start_background_scanning(self):
        """Launches dedicated background scanner threads for both readers."""
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

        print("[RFID] Background gate monitoring threads running.")

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
                        # Find header start
                        idx = buffer.find(RFID_HEADER)
                        if idx == -1:
                            # Keep only the last byte in case header is split across reads
                            buffer = buffer[-1:]
                            break

                        if idx > 0:
                            # Drop garbage bytes prior to header
                            buffer = buffer[idx:]

                        if len(buffer) < RFID_FRAME_MIN_LEN:
                            break # Await full packet

                        # Extract EPC
                        epc_end = RFID_EPC_OFFSET + RFID_EPC_LENGTH
                        if len(buffer) >= epc_end:
                            epc_bytes = buffer[RFID_EPC_OFFSET:epc_end]
                            epc = epc_bytes.hex().upper()
                            self._handle_tag_scanned(epc, gate_type, port_name)

                            # Advance past this frame
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
                return # Debounce rapid re-reads
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

                print(f"📦 [RFID CHECK-IN] {meta['product_name']} ({epc}) Entered Warehouse Gate!")
                # Queue to Supabase
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
                    # Item scanned at exit without previous check-in!
                    record = TagRecord(epc, meta, gate="CHECK_OUT")
                    record.status = "DISPATCHED"
                    self.active_inventory[epc] = record
                    # Alert potential discrepancy
                    db.log_security_alert("TAG_UNEXPECTED_EXIT", "WARNING", {
                        "epc": epc,
                        "product_name": meta["product_name"],
                        "gate": port_name
                    })

                print(f"🚚 [RFID CHECK-OUT] {meta['product_name']} ({epc}) Exited! Dwell: {dwell}s")
                # Queue to Supabase
                db.log_rfid_movement(epc, "CHECK_OUT", port_name, dwell, meta)

        # Notify any event callbacks
        for cb in self._callbacks:
            try:
                cb({"event": gate_type, "epc": epc, "metadata": meta, "dwell": dwell})
            except Exception:
                pass

    def stop(self):
        """Stops scanner threads and closes serial ports."""
        self._running = False
        if self.ser_checkin:
            try:
                self.ser_checkin.close()
            except Exception:
                pass
        if self.ser_checkout:
            try:
                self.ser_checkout.close()
            except Exception:
                pass

# Global Singleton
rfid = DualRFIDManager()
