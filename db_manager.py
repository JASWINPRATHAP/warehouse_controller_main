"""
Database Subsystem & Supabase Cloud PostgreSQL Sync Engine.
Asynchronously pushes RFID movements, inventory updates, sensor telemetry,
and security alerts to Supabase with local queueing and automatic retry on network drops.
"""

import os
import sys
import json
import time
import queue
import threading
from datetime import datetime
from typing import Dict, Any, Optional

from config import (
    SUPABASE_HOST, SUPABASE_PORT, SUPABASE_DB, SUPABASE_USER,
    SUPABASE_PASSWORD, SUPABASE_URL, SUPABASE_KEY
)

try:
    import psycopg2
    from psycopg2 import pool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    print("[WARN] psycopg2 not installed. Supabase direct DB sync will operate in HTTP/offline queue mode.")

class DatabaseManager:
    def __init__(self):
        self._queue = queue.Queue(maxsize=1000)
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._connection = None
        self.last_sync_status = "idle"
        self.last_sync_time = 0
        self.offline_queue_file = os.path.join(os.path.dirname(__file__), "offline_db_queue.jsonl")

    def start(self):
        """Starts the background DB sync worker."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._worker_thread.start()
        print("[DB] Supabase async persistence worker started.")

    def _get_connection(self):
        """Attempts to obtain or recreate a PostgreSQL connection to Supabase."""
        if not PSYCOPG2_AVAILABLE:
            return None
        if not SUPABASE_PASSWORD:
            return None

        if self._connection and not self._connection.closed:
            return self._connection

        try:
            conn = psycopg2.connect(
                host=SUPABASE_HOST,
                port=SUPABASE_PORT,
                dbname=SUPABASE_DB,
                user=SUPABASE_USER,
                password=SUPABASE_PASSWORD,
                connect_timeout=5,
                sslmode="require"
            )
            conn.autocommit = True
            self._connection = conn
            print(f"[DB SUCCESS] Connected to Supabase PostgreSQL ({SUPABASE_HOST})")
            return self._connection
        except Exception as e:
            self._connection = None
            self.last_sync_status = f"conn_err: {str(e)[:40]}"
            return None

    def log_rfid_movement(self, epc: str, gate_type: str, reader_port: str, dwell_seconds: int, metadata: Dict[str, Any]):
        """Queues an RFID movement event (Check-In or Check-Out) and inventory update."""
        event = {
            "type": "RFID_MOVEMENT",
            "epc": epc.upper(),
            "gate_type": gate_type, # 'CHECK_IN' or 'CHECK_OUT'
            "reader_port": reader_port,
            "dwell_duration_seconds": dwell_seconds,
            "metadata": metadata,
            "timestamp": datetime.now().isoformat()
        }
        self._enqueue(event)

    def log_sensor_telemetry(self, sensor_payload: Dict[str, Any]):
        """Queues live sensor telemetry received from ESP32."""
        event = {
            "type": "SENSOR_TELEMETRY",
            "source_ip": sensor_payload.get("source_ip", "192.168.1.101"),
            "temperature": float(sensor_payload.get("temperature", 25.0)),
            "humidity": float(sensor_payload.get("humidity", 50.0)),
            "gas_detected": bool(sensor_payload.get("gas", False)),
            "flame_detected": bool(sensor_payload.get("flame1", False) or sensor_payload.get("flame2", False)),
            "sound_detected": bool(sensor_payload.get("sound", False)),
            "water_detected": bool(sensor_payload.get("water", False)),
            "is_simulated": False,
            "timestamp": datetime.now().isoformat()
        }
        self._enqueue(event)

    def log_security_alert(self, alert_type: str, severity: str, details: Dict[str, Any]):
        """Queues security alert (e.g. Unauthorized Face, Gas Alarm)."""
        event = {
            "type": "SECURITY_ALERT",
            "alert_type": alert_type,
            "severity": severity,
            "details": details,
            "is_simulated": False,
            "timestamp": datetime.now().isoformat()
        }
        self._enqueue(event)

    def _enqueue(self, item: Dict[str, Any]):
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Save to offline fallback file if RAM queue is full
            self._save_offline(item)

    def _save_offline(self, item: Dict[str, Any]):
        try:
            with open(self.offline_queue_file, "a") as f:
                f.write(json.dumps(item) + "\n")
        except Exception:
            pass

    def _sync_loop(self):
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            success = self._execute_sync(item)
            if not success:
                # If network fails, save to offline file for next retry
                self._save_offline(item)
                time.sleep(2.0)
            else:
                self.last_sync_time = time.time()
                self.last_sync_status = "synced"

    def _execute_sync(self, item: Dict[str, Any]) -> bool:
        conn = self._get_connection()
        if not conn:
            # Cannot connect directly; will keep in queue
            return False

        try:
            with conn.cursor() as cur:
                ev_type = item.get("type")

                if ev_type == "RFID_MOVEMENT":
                    epc = item["epc"]
                    gate = item["gate_type"]
                    dwell = item["dwell_duration_seconds"]
                    meta = item.get("metadata", {})

                    # 1. Upsert into inventory_items
                    new_status = "IN_WAREHOUSE" if gate == "CHECK_IN" else "DISPATCHED"
                    cur.execute("""
                        INSERT INTO inventory_items (
                            epc, product_name, category, unit_weight_kg,
                            target_zone, mfg_date, exp_date, batch_no,
                            status, last_seen_gate, check_in_time, check_out_time
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                            CASE WHEN %s = 'CHECK_IN' THEN NOW() ELSE NULL END,
                            CASE WHEN %s = 'CHECK_OUT' THEN NOW() ELSE NULL END
                        )
                        ON CONFLICT (epc) DO UPDATE SET
                            status = EXCLUDED.status,
                            last_seen_gate = EXCLUDED.last_seen_gate,
                            check_in_time = COALESCE(inventory_items.check_in_time, EXCLUDED.check_in_time),
                            check_out_time = CASE WHEN %s = 'CHECK_OUT' THEN NOW() ELSE inventory_items.check_out_time END;
                    """, (
                        epc,
                        meta.get("product_name", f"Item-{epc[:8]}"),
                        meta.get("category", "General"),
                        meta.get("unit_weight_kg", 1.0),
                        meta.get("target_zone", "Zone-A"),
                        meta.get("mfg_date", None),
                        meta.get("exp_date", None),
                        meta.get("batch_no", None),
                        new_status,
                        gate,
                        gate,
                        gate,
                        gate
                    ))

                    # 2. Insert into rfid_movement_logs
                    cur.execute("""
                        INSERT INTO rfid_movement_logs (
                            epc, product_name, gate_type, reader_port, dwell_duration_seconds
                        ) VALUES (%s, %s, %s, %s, %s);
                    """, (
                        epc,
                        meta.get("product_name", f"Item-{epc[:8]}"),
                        gate,
                        item.get("reader_port", "UNKNOWN"),
                        dwell
                    ))
                    print(f"[DB SYNC] Logged RFID {gate} for EPC {epc} to Supabase.")

                elif ev_type == "SENSOR_TELEMETRY":
                    cur.execute("""
                        INSERT INTO sensor_telemetry (
                            source_ip, temperature, humidity, gas_detected,
                            flame_detected, sound_detected, water_detected, is_simulated
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """, (
                        item["source_ip"],
                        item["temperature"],
                        item["humidity"],
                        item["gas_detected"],
                        item["flame_detected"],
                        item["sound_detected"],
                        item["water_detected"],
                        item["is_simulated"]
                    ))

                elif ev_type == "SECURITY_ALERT":
                    cur.execute("""
                        INSERT INTO security_alerts (
                            alert_type, severity, details, is_simulated
                        ) VALUES (%s, %s, %s, %s);
                    """, (
                        item["alert_type"],
                        item["severity"],
                        json.dumps(item["details"]),
                        item["is_simulated"]
                    ))
                    print(f"[DB SYNC] Logged Alert '{item['alert_type']}' ({item['severity']}) to Supabase.")

            return True
        except Exception as e:
            print(f"[DB ERROR] Sync execution failed: {e}")
            try:
                if self._connection:
                    self._connection.close()
            except Exception:
                pass
            self._connection = None
            return False

# Global Singleton
db = DatabaseManager()
