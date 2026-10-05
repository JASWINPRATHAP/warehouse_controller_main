"""
Non-blocking HTTP Audio Subsystem Manager.
Communicates with ESP8266 NodeMCU (192.168.1.102) -> DFPlayer Mini (Tracks 1-12).
"""

import time
import threading
import requests
from config import AUDIO_BASE_URL, AUDIO_REQUEST_TIMEOUT, AUDIO_ALERT_COOLDOWN_SEC

class AudioManager:
    def __init__(self):
        self._last_played_times = {} # { audio_num: timestamp }
        self._lock = threading.Lock()
        self.last_audio_requested = None
        self.last_audio_status = "idle"
        self.last_request_time = None

    def play(self, audio_number: int, force: bool = False) -> bool:
        """
        Triggers an audio alert asynchronously.
        If force=False, checks cooldown to prevent continuous repeating alarms.
        """
        now = time.time()
        with self._lock:
            last_time = self._last_played_times.get(audio_number, 0)
            if not force and (now - last_time < AUDIO_ALERT_COOLDOWN_SEC):
                # Cooldown active, ignore repetitive spam
                return False
            self._last_played_times[audio_number] = now
            self.last_audio_requested = audio_number
            self.last_audio_status = "dispatching"
            self.last_request_time = now

        # Run HTTP request in a background thread so it never blocks the main pipeline
        thread = threading.Thread(target=self._send_http_request, args=(audio_number,), daemon=True)
        thread.start()
        return True

    def _send_http_request(self, audio_number: int):
        url = f"{AUDIO_BASE_URL}?number={audio_number}"
        try:
            resp = requests.get(url, timeout=AUDIO_REQUEST_TIMEOUT)
            if resp.status_code == 200:
                self.last_audio_status = "success"
                print(f"[AUDIO] -> Played Track #{audio_number} on ESP8266 (HTTP 200)")
            else:
                self.last_audio_status = f"http_error_{resp.status_code}"
                print(f"[AUDIO WARN] ESP8266 returned status {resp.status_code} for audio #{audio_number}")
        except requests.exceptions.RequestException as e:
            self.last_audio_status = "offline"
            print(f"[AUDIO ERROR] ESP8266 unreachable for audio #{audio_number}: {e}")

# Global singleton
audio = AudioManager()
