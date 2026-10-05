"""
MSME 4.0 - IoT Warehouse Security Master Configuration
Centralized settings for IPs, GPIO pins, thresholds, audio mappings, and RFID time rules.
"""

import os

# ==========================================
# 🌐 NETWORK CONFIGURATION
# ==========================================
PI_IP = "192.168.1.103"
ESP32_IP = "192.168.1.101"
ESP8266_IP = "192.168.1.102"
FLASK_PORT = 5000

# Base URL for ESP8266 Audio Subsystem
AUDIO_BASE_URL = f"http://{ESP8266_IP}/audio"
AUDIO_REQUEST_TIMEOUT = 2.0  # seconds

# ==========================================
# 🔌 RASPBERRY PI GPIO PINOUT
# ==========================================
MOTOR_IN1 = 17       # H-Bridge IN1 (Door Motor)
MOTOR_IN2 = 27       # H-Bridge IN2 (Door Motor)
DOOR_SENSOR_PIN = 22 # Magnetic Door Reed Switch (Pull-up, Pressed/LOW = Closed)

# ==========================================
# 🚪 DOOR & MOTOR SETTINGS
# ==========================================
MAX_DOOR_CLOSE_TIME = 10.0  # Max safety timeout in seconds to prevent motor burnout
DOOR_CHECK_INTERVAL = 0.1   # Polling interval during motor operation

# ==========================================
# 🔊 AUDIO MAPPINGS (DFPlayer Mini Tracks 1-12)
# ==========================================
AUDIO_MAP = {
    "HUMIDITY_LOW": 1,
    "FLAME_DETECTED": 2,
    "GAS_DETECTED": 3,
    "PRODUCTS_MISSING_IN_LOAD": 4,
    "TAG_MISSING": 5,
    "GOODS_MISSING": 6,
    "UNAUTHORIZED_FACE": 7,
    "UNAUTHORIZED_SOUND": 8,
    "WATER_LEAK": 9,
    "TEMP_HIGH": 10,
    "TEMP_LOW": 11,
    "HUMIDITY_HIGH": 12,
}
UNAUTHORIZED_AUDIO = AUDIO_MAP["UNAUTHORIZED_FACE"] # 7
AUDIO_ALERT_COOLDOWN_SEC = 10.0  # Prevent repeating the same audio alert continuously

# ==========================================
# 🌡️ SENSOR THRESHOLDS
# ==========================================
TEMP_HIGH_THRESHOLD = 40.0   # °C
TEMP_LOW_THRESHOLD = 20.0    # °C
HUMIDITY_HIGH_THRESHOLD = 70.0  # %
HUMIDITY_LOW_THRESHOLD = 30.0   # %
SENSOR_OFFLINE_TIMEOUT_SEC = 6.0 # Mark ESP32 offline if no POST received within 6s

# ==========================================
# 📷 VISION & FACE RECOGNITION
# ==========================================
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30

FACE_TOLERANCE = 0.50          # Distance <= 0.50 is Authorized, > 0.50 is Unauthorized
CONFIRMATION_FRAMES = 3        # Number of consecutive unknown frames required to trigger alarm
FACE_COOLDOWN_SEC = 5.0        # Cooldown between unauthorized alarm triggers

KNOWN_FACES_DIR = os.path.join(os.path.dirname(__file__), "known_faces")
UNKNOWN_FACES_DIR = os.path.join(os.path.dirname(__file__), "unknown_faces")

# ==========================================
# 🏷️ UHF RFID READER & WAREHOUSE TIME RULES
# ==========================================
RFID_DEVICE = "/dev/ttyUSB0"   # Default USB-to-RS232 adapter
RFID_BAUDRATE = 115200
RFID_ADDRESS = 0x00
RFID_INVENTORY_OPCODE = 0x01
RFID_INFO_OPCODE = 0x21
RFID_CRC_POLY = 0x8408
RFID_CRC_INIT = 0xFFFF
RFID_SCAN_TIME_MS = 200        # Fast 200ms sweep
RFID_POLL_INTERVAL_SEC = 0.05

# 📦 Warehouse Movement & State Rules:
# - If tag is scanned for the first time -> Status = "ENTERED" (Check-In)
# - Scans within MIN_DWELL_TIME_SEC are debounced (same pass event)
# - If tag is scanned again after MIN_DWELL_TIME_SEC -> Status = "LEAVING" (Check-Out)
RFID_MIN_DWELL_TIME_SEC = 10.0      # Minimum seconds before a tag can be marked as 'Leaving'
RFID_TAG_EXPIRY_HOURS = 24.0        # Auto-archive tags from active warehouse memory after 24h
RFID_INVENTORY_DB_FILE = os.path.join(os.path.dirname(__file__), "warehouse_inventory.json")
EVENTS_LOG_FILE = os.path.join(os.path.dirname(__file__), "events.jsonl")
