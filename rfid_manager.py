"""
UHF RFID Subsystem & Warehouse Inventory State Machine.
Communicates with Industrial UHF RFID Reader over RS232 / USB-Serial (/dev/ttyUSB0).
Implements Time-Based Entry / Exit Logic (Check-In vs Leaving Warehouse).
"""

import os
import sys
import glob
import json
import time
import threading
import serial
from datetime import datetime
from typing import Dict, List, Optional, Callable
from config import (
    RFID_DEVICE, RFID_BAUDRATE, RFID_ADDRESS, RFID_SCAN_TIME_MS,
    RFID_POLL_INTERVAL_SEC, RFID_MIN_DWELL_TIME_SEC, RFID_TAG_EXPIRY_HOURS,
    RFID_INVENTORY_DB_FILE
)

class RFIDTagRecord:
    def __init__(self, epc: str, length: int, first_seen: float, last_seen: float, status: str = "IN_WAREHOUSE"):
        self.epc = epc.upper()
        self.length = length
        self.first_seen = first_seen
        self.last_seen = last_seen
        self.read_count = 1
        self.status = status # "IN_WAREHOUSE", "LEAVING", "EXITED"
        self.exit_timestamp: Optional[float] = None

    def to_dict(self):
        return {
            "epc": self.epc,
            "length": self.length,
            "first_seen": datetime.fromtimestamp(self.first_seen).strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen": datetime.fromtimestamp(self.last_seen).strftime("%Y-%m-%d %H:%M:%S"),
            "read_count": self.read_count,
            "status": self.status,
            "dwell_seconds": round((self.exit_timestamp or self.last_seen) - self.first_seen, 1)
        }

class RFIDManager:
    def __init__(self, port: str = RFID_DEVICE, baudrate: int = RFID_BAUDRATE, address: int = RFID_ADDRESS):
        self.port = port
        self.baudrate = baudrate
        self.address = address
        self.ser: Optional[serial.Serial] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # In-memory inventory registry: { epc: RFIDTagRecord }
        self.active_inventory: Dict[str, RFIDTagRecord] = {}
        self.history_records: List[Dict] = []
        self._event_callbacks: List[Callable] = []

        self._load_persisted_inventory()

    @staticmethod
    def calculate_crc(data: bytes) -> bytes:
        """CRC-16 CCITT (Poly: 0x8408, Init: 0xFFFF, LSB first)."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0x8408
                else:
                    crc = crc >> 1
        return bytes([crc & 0xFF, (crc >> 8) & 0xFF])

    def auto_detect_port(self) -> Optional[str]:
        """Automatically scans for USB serial adapters."""
        if sys.platform.startswith("linux"):
            candidates = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*") + ["/dev/serial0"]
        elif sys.platform.startswith("win"):
            candidates = [f"COM{i}" for i in range(1, 32)]
        else:
            candidates = glob.glob("/dev/tty.usbserial*")

        for p in candidates:
            try:
                s = serial.Serial(p, baudrate=self.baudrate, timeout=0.3)
                s.close()
                return p
            except Exception:
                continue
        return None

    def connect(self) -> bool:
        """Opens serial port to UHF RFID reader."""
        target_port = self.port
        if not os.path.exists(target_port) and not target_port.startswith("COM"):
            detected = self.auto_detect_port()
            if detected:
                target_port = detected
                self.port = target_port

        try:
            self.ser = serial.Serial(target_port, baudrate=self.baudrate, timeout=1.5)
            time.sleep(0.05)
            self._configure_reader()
            print(f"[RFID] -> Connected successfully to Reader on {self.port}")
            return True
        except Exception as e:
            print(f"[RFID ERROR] Could not connect to {self.port}: {e}")
            return False

    def _configure_reader(self):
        """Sets fast 200ms scan duration and maximum RF power."""
        if not self.ser:
            return
        # Set Scan Time: Opcode 0x25 (Units of 100ms: 2 = 200ms)
        self.send_frame(0x25, bytes([max(1, RFID_SCAN_TIME_MS // 100)]), wait_sec=0.1)
        # Set Power: Opcode 0x2F (30 dBm)
        self.send_frame(0x2F, bytes([30]), wait_sec=0.1)

    def send_frame(self, cmd: int, payload: bytes = b"", wait_sec: float = 0.25) -> Optional[bytes]:
        """Builds and transmits packet frame, validates CRC on response."""
        if not self.ser or not self.ser.is_open:
            return None

        length = len(payload) + 4
        frame_body = bytes([length, self.address, cmd]) + payload
        crc = self.calculate_crc(frame_body)
        full_frame = frame_body + crc

        try:
            self.ser.reset_input_buffer()
            self.ser.write(full_frame)
            time.sleep(wait_sec)

            header = self.ser.read(1)
            if not header and self.ser.in_waiting > 0:
                header = self.ser.read(1)
            if not header:
                return None

            resp_len = header[0]
            if resp_len < 4 or resp_len > 255:
                return None

            remaining = self.ser.read(resp_len)
            full_resp = header + remaining

            if len(full_resp) < resp_len + 1:
                return None

            # Verify CRC
            if full_resp[-2:] != self.calculate_crc(full_resp[:-2]):
                return None

            return full_resp
        except Exception as e:
            print(f"[RFID WARN] Serial communication error: {e}")
            return None

    def scan_inventory(self) -> List[tuple]:
        """Performs EPC Gen2 scan and parses all detected tag EPCs."""
        tags = []
        resp = self.send_frame(0x01, wait_sec=max(0.2, (RFID_SCAN_TIME_MS / 1000.0) + 0.05))
        if not resp or len(resp) < 5:
            return tags

        status = resp[3]
        if status in [0x00, 0x01, 0x02, 0x03, 0x04] and len(resp) > 5:
            tag_count = resp[4]
            idx = 5
            for _ in range(tag_count):
                if idx < len(resp) - 2:
                    epc_len = resp[idx]
                    idx += 1
                    epc_bytes = resp[idx:idx + epc_len]
                    idx += epc_len
                    tags.append((epc_bytes.hex().upper(), epc_len))
        return tags

    def register_callback(self, callback: Callable):
        """Registers a listener callback for RFID events."""
        self._event_callbacks.append(callback)

    def _notify_event(self, event_type: str, record: RFIDTagRecord):
        payload = {
            "event_type": event_type, # "RFID_ENTERED", "RFID_LEAVING", "RFID_SEEN"
            "tag": record.to_dict(),
            "timestamp": datetime.now().isoformat()
        }
        for cb in self._event_callbacks:
            try:
                cb(payload)
            except Exception as e:
                print(f"[RFID CALLBACK ERROR] {e}")

    def process_detected_tags(self, tags: List[tuple]):
        """
        Time-Based Warehouse State Machine:
        1. If tag is NOT in warehouse -> Mark as ENTERED (Check-in).
        2. If tag is in warehouse and time since first seen > RFID_MIN_DWELL_TIME_SEC -> Mark as LEAVING (Check-out).
        3. If tag is in warehouse and time < threshold -> Update last seen (Debounce).
        """
        now = time.time()
        with self._lock:
            for epc, epc_len in tags:
                if epc not in self.active_inventory:
                    # 🟢 NEW ITEM ENTERING WAREHOUSE
                    record = RFIDTagRecord(epc, epc_len, first_seen=now, last_seen=now, status="IN_WAREHOUSE")
                    self.active_inventory[epc] = record
                    print(f"📦 [RFID CHECK-IN] New Item Entered Warehouse: EPC {epc} ({epc_len} bytes)")
                    self._notify_event("RFID_ENTERED", record)
                else:
                    record = self.active_inventory[epc]
                    record.read_count += 1
                    record.last_seen = now
                    dwell = now - record.first_seen

                    if record.status == "IN_WAREHOUSE" and dwell >= RFID_MIN_DWELL_TIME_SEC:
                        # 🔴 ITEM LEAVING WAREHOUSE (Check-Out Rule Triggered)
                        record.status = "LEAVING"
                        record.exit_timestamp = now
                        print(f"🚚 [RFID CHECK-OUT] Item Leaving Warehouse: EPC {epc} (Dwell time: {dwell:.1f}s)")
                        self._notify_event("RFID_LEAVING", record)
                    else:
                        # 🔄 Continuous scan update (debounce)
                        pass

            self._save_persisted_inventory()

    def start_background_scanning(self):
        """Starts continuous non-blocking background scanning thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()

    def _scan_loop(self):
        while self._running:
            if not self.ser or not self.ser.is_open:
                time.sleep(1.0)
                self.connect()
                continue

            try:
                tags = self.scan_inventory()
                if tags:
                    self.process_detected_tags(tags)
            except Exception as e:
                print(f"[RFID LOOP ERROR] {e}")
                time.sleep(0.5)

            time.sleep(RFID_POLL_INTERVAL_SEC)

    def stop(self):
        """Stops scanner cleanly."""
        self._running = False
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self._save_persisted_inventory()

    def _load_persisted_inventory(self):
        if os.path.exists(RFID_INVENTORY_DB_FILE):
            try:
                with open(RFID_INVENTORY_DB_FILE, "r") as f:
                    data = json.load(f)
                    for item in data.get("active", []):
                        epc = item["epc"]
                        self.active_inventory[epc] = RFIDTagRecord(
                            epc=epc,
                            length=item["length"],
                            first_seen=datetime.strptime(item["first_seen"], "%Y-%m-%d %H:%M:%S").timestamp(),
                            last_seen=datetime.strptime(item["last_seen"], "%Y-%m-%d %H:%M:%S").timestamp(),
                            status=item.get("status", "IN_WAREHOUSE")
                        )
            except Exception as e:
                print(f"[RFID PERSIST WARN] Could not load persisted inventory: {e}")

    def _save_persisted_inventory(self):
        try:
            with open(RFID_INVENTORY_DB_FILE, "w") as f:
                json.dump({
                    "active": [rec.to_dict() for rec in self.active_inventory.values()],
                    "updated_at": datetime.now().isoformat()
                }, f, indent=2)
        except Exception:
            pass

# Global singleton
rfid = RFIDManager()
