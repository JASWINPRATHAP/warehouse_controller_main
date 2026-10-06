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
# 🏷️ DUAL UHF RFID READERS (SRK-UDR6 Protocol)
# ==========================================
RFID_CHECKIN_PORT = os.environ.get("RFID_CHECKIN_PORT", "/dev/ttyUSB0")   # Check-In Gate
RFID_CHECKOUT_PORT = os.environ.get("RFID_CHECKOUT_PORT", "/dev/ttyUSB1") # Check-Out Gate
RFID_BAUDRATE = 115200
RFID_HEADER = bytes([0x52, 0x46]) # 52 46 frame header
RFID_FRAME_MIN_LEN = 15
RFID_EPC_OFFSET = 12
RFID_EPC_LENGTH = 12 # 12 bytes = 24 hex characters
RFID_DEBOUNCE_SEC = 2.0 # Minimum seconds between scans of the same tag at the same gate

# 📦 Warehouse Predefined Tag Catalog (Hex EPC -> Product Details)
PREDEFINED_TAG_CATALOG = {
    "E28011700000020A12345601": {
        "product_name": "Industrial Gearbox Pallet",
        "category": "Machinery",
        "unit_weight_kg": 25.5,
        "target_zone": "Zone-A",
        "mfg_date": "2026-01-10",
        "exp_date": "2030-01-10",
        "batch_no": "GB-BATCH-01"
    },
    "E28011700000020A12345602": {
        "product_name": "Precision Roller Bearings Box",
        "category": "Hardware",
        "unit_weight_kg": 12.0,
        "target_zone": "Zone-B",
        "mfg_date": "2026-02-15",
        "exp_date": "2029-02-15",
        "batch_no": "RB-BATCH-02"
    },
    "E28011700000020A12345603": {
        "product_name": "Heavy Duty Copper Wire Coil",
        "category": "Electrical",
        "unit_weight_kg": 18.2,
        "target_zone": "Zone-C",
        "mfg_date": "2026-03-01",
        "exp_date": "2031-03-01",
        "batch_no": "CW-BATCH-03"
    },
    "E28011700000020A12345604": {
        "product_name": "Microcontroller Circuit Boards",
        "category": "Electronics",
        "unit_weight_kg": 6.4,
        "target_zone": "Zone-D",
        "mfg_date": "2026-04-12",
        "exp_date": "2028-04-12",
        "batch_no": "MC-BATCH-04"
    }
}

def get_tag_metadata(epc: str) -> dict:
    """Returns predefined metadata for an EPC, or a structured default."""
    epc_clean = epc.strip().upper()
    if epc_clean in PREDEFINED_TAG_CATALOG:
        data = dict(PREDEFINED_TAG_CATALOG[epc_clean])
        data["epc"] = epc_clean
        return data
    # Fallback for dynamic/new tag
    short_id = epc_clean[-8:] if len(epc_clean) >= 8 else epc_clean
    return {
        "epc": epc_clean,
        "product_name": f"Material Pallet #{short_id}",
        "category": "General Stock",
        "unit_weight_kg": 10.0,
        "target_zone": "Zone-A",
        "mfg_date": "2026-01-01",
        "exp_date": "2029-01-01",
        "batch_no": f"BATCH-{short_id}"
    }

# ==========================================
# ☁️ SUPABASE CLOUD DATABASE CONFIGURATION
# ==========================================
SUPABASE_HOST = os.environ.get("SUPABASE_HOST", "aws-0-ap-south-1.pooler.supabase.com")
SUPABASE_PORT = int(os.environ.get("SUPABASE_PORT", 5432))
SUPABASE_DB = os.environ.get("SUPABASE_DB", "postgres")
SUPABASE_USER = os.environ.get("SUPABASE_USER", "postgres.faptrwqvcnozbajbihih")
SUPABASE_PASSWORD = os.environ.get("SUPABASE_PASSWORD", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://faptrwqvcnozbajbihih.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

RFID_INVENTORY_DB_FILE = os.path.join(os.path.dirname(__file__), "warehouse_inventory.json")
EVENTS_LOG_FILE = os.path.join(os.path.dirname(__file__), "events.jsonl")
