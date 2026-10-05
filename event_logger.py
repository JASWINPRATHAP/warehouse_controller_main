"""
Standardized Event Logger & Database Dispatcher.
Generates structured JSON events and persists them to events.jsonl.
"""

import os
import json
import uuid
from datetime import datetime
from config import EVENTS_LOG_FILE

class EventLogger:
    def __init__(self, log_path: str = EVENTS_LOG_FILE):
        self.log_path = log_path
        os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)

    def log_event(self, event_type: str, details: dict) -> dict:
        event = {
            "device_id": "PI-001",
            "event_id": str(uuid.uuid4())[:8],
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "details": details
        }

        # Write to JSONL
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            print(f"[LOGGER ERROR] Could not persist event: {e}")

        return event

    def get_recent_events(self, limit: int = 50) -> list:
        events = []
        if not os.path.exists(self.log_path):
            return events
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in reversed(lines[-limit:]):
                    if line.strip():
                        events.append(json.loads(line.strip()))
        except Exception:
            pass
        return events

# Global singleton
logger = EventLogger()
