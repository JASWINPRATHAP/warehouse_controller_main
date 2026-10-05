"""
MSME 4.0 - IoT Warehouse Security Master Controller.
Main Entrypoint orchestrating:
1. UHF RFID Reader (Threaded Serial)
2. Vision & AI Face Recognition (Threaded Camera 0 & Camera 1)
3. ESP32 Sensor Receiver & ESP8266 Audio Dispatcher (Flask Server :5000)
4. H-Bridge Door Motor & Magnetic Sensor Controller
"""

import sys
import time
import signal
from config import PI_IP, FLASK_PORT
from rfid_manager import rfid
from vision_manager import vision
from door_controller import door
from server import run_server

def shutdown_handler(signum, frame):
    print("\n[SYSTEM] Received shutdown signal. Stopping all subsystems cleanly...")
    try:
        rfid.stop()
        vision.stop()
        door.stop_motor()
    except Exception as e:
        print(f"[SHUTDOWN ERROR] {e}")
    print("[SYSTEM] All subsystems stopped. Goodbye.")
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print("=" * 70)
    print("      🏭 MSME 4.0 - IOT WAREHOUSE SECURITY MASTER CONTROLLER      ")
    print("=" * 70)
    print(f" Master Controller IP : {PI_IP}")
    print(f" HTTP Endpoint Port   : {FLASK_PORT}")
    print(f" RFID Port Target     : {rfid.port}")
    print("=" * 70)

    # 1. Start UHF RFID Reader Background Worker
    print("\n[1/3] Initializing UHF RFID Reader Subsystem...")
    rfid.connect()
    rfid.start_background_scanning()

    # 2. Start Vision & Background AI Face Recognition Worker
    print("\n[2/3] Initializing Dual Camera & AI Face Recognition Subsystem...")
    vision.start()

    # 3. Start Flask Web Server & Dashboard
    print(f"\n[3/3] Starting Central Flask Server on http://0.0.0.0:{FLASK_PORT}...")
    print(f"  -> ESP32 Sensor Endpoint : http://{PI_IP}:{FLASK_PORT}/sensor")
    print(f"  -> Camera 0 AI Stream    : http://{PI_IP}:{FLASK_PORT}/video0")
    print(f"  -> Camera 1 Live Stream  : http://{PI_IP}:{FLASK_PORT}/video1")
    print(f"  -> Web Dashboard         : http://{PI_IP}:{FLASK_PORT}/")
    print("\n[SYSTEM READY] Listening for sensor telemetry, RFID tags, and video feeds.\n")

    run_server()

if __name__ == "__main__":
    main()
