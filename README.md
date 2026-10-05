# MSME 4.0 - IoT Based Warehouse Security Master Controller

A production-grade, multi-threaded master controller application designed for **Raspberry Pi 5** integrating:
1. **ESP32 Sensor Telemetry Ingestion** (`POST /sensor`) with edge-detection and per-alarm debouncing.
2. **ESP8266 + DFPlayer Mini Audio Subsystem** (`GET /audio?number=N` for tracks 1–12).
3. **Dual Picamera2 Video Pipeline**:
   - **Camera 0**: Dedicated background AI Face Recognition against `known_faces/` (with 3-frame confirmation, snapshot logging, and automatic door closing).
   - **Camera 1**: Live MJPEG video feed (`GET /video1`).
4. **H-Bridge Door Motor (GPIO 17 & 27) + Magnetic Sensor (GPIO 22)** with 10-second safety cutoff.
5. **UHF RFID Reader Driver (`/dev/ttyUSB0`)**:
   - Time-based warehouse check-in and check-out state machine.
   - Tag arrival (`ENTERED`), dwell tracking, and exit (`LEAVING WAREHOUSE`).
6. **Real-time Web Dashboard** on `http://192.168.1.103:5000/`.

---

## 📁 Project Structure

```text
warehouse_controller/
├── config.py             # Central configuration (IPs, GPIOs, audio mappings, thresholds)
├── audio_manager.py      # Non-blocking async audio player to ESP8266
├── door_controller.py    # Motor and magnetic sensor controller with safety watchdog
├── rfid_manager.py       # Threaded UHF RFID reader driver & warehouse entry/exit tracker
├── vision_manager.py     # Camera 0 AI Face thread & Camera 1 live video streaming
├── sensor_evaluator.py   # State machine for ESP32 sensor ingestion & edge-detection
├── event_logger.py       # Standardized JSON event logger (events.jsonl)
├── server.py             # Flask Web Server (:5000) & Command Center Dashboard
├── main.py               # Master entrypoint orchestrator
├── setup_master.sh       # One-click Raspberry Pi OS setup script
├── warehouse.service     # Systemd 24/7 background service file
├── requirements.txt      # Python dependencies
├── known_faces/          # Folder for reference authorized face images (e.g. logavel1.jpeg)
└── unknown_faces/        # Auto-saved intruder face snapshots (YYYYMMDD_HHMMSS.jpg)
```

---

## 🚀 Quick Setup on Fresh Raspberry Pi OS

### 1. Run the Setup Script
```bash
cd warehouse_controller
chmod +x setup_master.sh
./setup_master.sh
```

### 2. Add Authorized Face Images
Copy photos of authorized personnel into `known_faces/`:
```bash
cp /path/to/logavel1.jpeg known_faces/
```

### 3. Start the Master Controller
```bash
python3 main.py
```

Open your browser at:
👉 **`http://192.168.1.103:5000/`** to view the live dashboard, AI camera stream, active warehouse RFID tags, and environmental telemetry!

---

## 🔄 Auto-Start on Boot (Systemd Daemon)
```bash
sudo cp warehouse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable warehouse.service
sudo systemctl start warehouse.service

# View live logs:
journalctl -u warehouse.service -f
```
