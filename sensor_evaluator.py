"""
Sensor Payload Evaluator & Edge Detection State Machine.
Processes JSON from ESP32 (POST /sensor) every 2 seconds.
Dispatches Audio 1-12 alerts with edge-detection to avoid continuous alert spamming.
"""

import time
from datetime import datetime
from config import (
    TEMP_HIGH_THRESHOLD, TEMP_LOW_THRESHOLD, HUMIDITY_HIGH_THRESHOLD,
    HUMIDITY_LOW_THRESHOLD, SENSOR_OFFLINE_TIMEOUT_SEC, AUDIO_MAP
)
from audio_manager import audio

class SensorEvaluator:
    def __init__(self):
        self.latest_sensor_data = {}
        self.last_seen_time = 0
        self._prev_active_states = {
            "gas": False,
            "flame": False,
            "water": False,
            "sound": False,
            "temp_high": False,
            "temp_low": False,
            "humidity_high": False,
            "humidity_low": False,
        }

    @property
    def is_esp32_online(self) -> bool:
        """Returns True if ESP32 sent a packet within the last SENSOR_OFFLINE_TIMEOUT_SEC."""
        return (time.time() - self.last_seen_time) < SENSOR_OFFLINE_TIMEOUT_SEC

    def evaluate(self, payload: dict) -> list:
        """
        Evaluates incoming sensor reading, checks for rising edges, and triggers audio.
        Returns list of triggered alert event names.
        """
        self.latest_sensor_data = payload
        self.last_seen_time = time.time()
        triggered_events = []

        gas = bool(payload.get("gas", False))
        flame1 = bool(payload.get("flame1", False))
        flame2 = bool(payload.get("flame2", False))
        flame = flame1 or flame2
        water = bool(payload.get("water", False))
        sound = bool(payload.get("sound", False))
        temp = float(payload.get("temperature", 25.0))
        humidity = float(payload.get("humidity", 50.0))

        temp_high = (temp > TEMP_HIGH_THRESHOLD)
        temp_low = (temp < TEMP_LOW_THRESHOLD)
        humidity_high = (humidity > HUMIDITY_HIGH_THRESHOLD)
        humidity_low = (humidity < HUMIDITY_LOW_THRESHOLD)
        product_missing = bool(payload.get("product_missing", False))
        tag_missing = bool(payload.get("tag_missing", False))
        goods_missing = bool(payload.get("goods_missing", False))

        # Helper to check for rising edge or persistent alert
        def check_alert(state_key: str, is_active: bool, audio_num: int, event_name: str):
            prev = self._prev_active_states.get(state_key, False)
            if is_active:
                if not prev:
                    # Rising edge: New alarm condition!
                    print(f"🚨 [SENSOR ALERT] {event_name}! Triggering Audio #{audio_num}")
                    audio.play(audio_num, force=True)
                else:
                    # Continuous condition: Play with cooldown
                    audio.play(audio_num, force=False)
                triggered_events.append(event_name)
            self._prev_active_states[state_key] = is_active

        # Check all conditions against Audio Mapping:
        check_alert("flame", flame, AUDIO_MAP["FLAME_DETECTED"], "FLAME_DETECTED")
        check_alert("gas", gas, AUDIO_MAP["GAS_DETECTED"], "GAS_DETECTED")
        check_alert("water", water, AUDIO_MAP["WATER_LEAK"], "WATER_LEAK")
        check_alert("sound", sound, AUDIO_MAP["UNAUTHORIZED_SOUND"], "UNAUTHORIZED_SOUND")
        check_alert("temp_high", temp_high, AUDIO_MAP["TEMP_HIGH"], f"TEMP_HIGH ({temp}°C)")
        check_alert("temp_low", temp_low, AUDIO_MAP["TEMP_LOW"], f"TEMP_LOW ({temp}°C)")
        check_alert("humidity_low", humidity_low, AUDIO_MAP["HUMIDITY_LOW"], f"HUMIDITY_LOW ({humidity}%)")
        check_alert("humidity_high", humidity_high, AUDIO_MAP["HUMIDITY_HIGH"], f"HUMIDITY_HIGH ({humidity}%)")
        check_alert("product_missing", product_missing, AUDIO_MAP["PRODUCTS_MISSING_IN_LOAD"], "PRODUCTS_MISSING_IN_LOAD")
        check_alert("tag_missing", tag_missing, AUDIO_MAP["TAG_MISSING"], "TAG_MISSING")
        check_alert("goods_missing", goods_missing, AUDIO_MAP["GOODS_MISSING"], "GOODS_MISSING")

        return triggered_events

    def get_status_dict(self) -> dict:
        return {
            "online": self.is_esp32_online,
            "last_seen": datetime.fromtimestamp(self.last_seen_time).isoformat() if self.last_seen_time else "never",
            "data": self.latest_sensor_data,
            "active_alarms": [k for k, v in self._prev_active_states.items() if v]
        }

# Global singleton
sensors = SensorEvaluator()
