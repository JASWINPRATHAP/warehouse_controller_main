"""
MSME 4.0 - IoT Warehouse Security Master Configuration
Centralized settings for IPs, GPIO pins, thresholds, audio mappings, and RFID time rules.
"""

import os
import socket

# Load local .env if present
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    try:
        with open(_env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass

def get_lan_ip() -> str:
    """Dynamically detects the actual local network IP of this device."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"

# ==========================================
# 🌐 NETWORK CONFIGURATION
# ==========================================
PI_IP = os.environ.get("PI_IP", get_lan_ip())
ESP32_IP = os.environ.get("ESP32_IP", "192.168.1.101")
ESP8266_IP = os.environ.get("ESP8266_IP", "192.168.1.102")
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5000))

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
TEMP_HIGH_THRESHOLD = 45.0   # °C (Alarm only on fire/overheating)
TEMP_LOW_THRESHOLD = 5.0     # °C (Alarm only on severe freezing)
HUMIDITY_HIGH_THRESHOLD = 85.0  # % (High humidity warning)
HUMIDITY_LOW_THRESHOLD = 15.0   # % (Dry air warning)
SENSOR_OFFLINE_TIMEOUT_SEC = 8.0 # Mark ESP32 offline if no POST received within 8s

# ==========================================
# 📷 VISION & FACE RECOGNITION
# ==========================================
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30
MIRROR_CAM1_OVERVIEW = True    # When True, mirrors working Camera 0 feed onto /video1 with surveillance HUD overlay

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
    "E2801191A504007870E42DDB": {
        "product_name": "Heavy Industrial Gearbox Pallet",
        "category": "Machinery",
        "unit_weight_kg": 28.5,
        "target_zone": "Zone-A",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "GB-BATCH-101"
    },
    "E2801191A504007870E3E8EB": {
        "product_name": "High-Precision Roller Bearings Box",
        "category": "Hardware",
        "unit_weight_kg": 14.2,
        "target_zone": "Zone-B",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "RB-BATCH-102"
    },
    "E2801191A504007870E3E8FB": {
        "product_name": "Pure Copper Motor Winding Coil",
        "category": "Electrical",
        "unit_weight_kg": 19.8,
        "target_zone": "Zone-A",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "CW-BATCH-103"
    },
    "E20001020304050607080912": {
        "product_name": "STM32 Industrial Controller PCBs",
        "category": "Electronics",
        "unit_weight_kg": 5.6,
        "target_zone": "Zone-C",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "PCB-BATCH-104"
    },
    "E2801191A504007870E42D1B": {
        "product_name": "Hydraulic High-Pressure Seal Rings",
        "category": "Hydraulics",
        "unit_weight_kg": 4.2,
        "target_zone": "Zone-B",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "HS-BATCH-105"
    },
    "E2801191A504007870E42D2B": {
        "product_name": "Digital Clamp Multimeter Kit",
        "category": "Instrumentation",
        "unit_weight_kg": 2.1,
        "target_zone": "Zone-C",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "MM-BATCH-106"
    },
    "E2000102030C050607080917": {
        "product_name": "CNC Tungsten Carbide End Mills Pack",
        "category": "Tooling",
        "unit_weight_kg": 6.8,
        "target_zone": "Zone-D",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "EM-BATCH-107"
    },
    "E2801191A504007870E42D4B": {
        "product_name": "Three-Phase Induction Motor 5HP",
        "category": "Machinery",
        "unit_weight_kg": 38.0,
        "target_zone": "Zone-A",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "MT-BATCH-108"
    },
    "E2801191A504007870E42DBB": {
        "product_name": "Industrial Pneumatic Solenoid Valves",
        "category": "Pneumatics",
        "unit_weight_kg": 7.5,
        "target_zone": "Zone-B",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "SV-BATCH-109"
    },
    "E2801191A504007870E42D6B": {
        "product_name": "Automated Optical Sensor Modules",
        "category": "Electronics",
        "unit_weight_kg": 3.4,
        "target_zone": "Zone-C",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "OS-BATCH-110"
    },
    "E2801191A504007870E42D7B": {
        "product_name": "Stainless Steel Flange Set DN50",
        "category": "Hardware",
        "unit_weight_kg": 16.5,
        "target_zone": "Zone-D",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "FL-BATCH-111"
    },
    "E2801191A504007870E42D8B": {
        "product_name": "Thermal Imaging Safety Sensor",
        "category": "Safety Equipment",
        "unit_weight_kg": 2.8,
        "target_zone": "Zone-C",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "TI-BATCH-112"
    },
    "E2801191A504007870E42D9B": {
        "product_name": "High-Torque Stepper Motor NEMA 34",
        "category": "Electrical",
        "unit_weight_kg": 9.2,
        "target_zone": "Zone-A",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "SM-BATCH-113"
    },
    "E2801191A504007870E3E8DB": {
        "product_name": "Chemical Storage Anti-Corrosive Drum",
        "category": "Chemicals",
        "unit_weight_kg": 45.0,
        "target_zone": "Zone-D",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "CD-BATCH-114"
    },
    "E2801191A504007870E42DAB": {
        "product_name": "Variable Frequency Drive Inverter 7.5kW",
        "category": "Electrical",
        "unit_weight_kg": 11.4,
        "target_zone": "Zone-B",
        "mfg_date": "2026-01-15",
        "exp_date": "2030-01-15",
        "batch_no": "VFD-BATCH-115"
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
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZhcHRyd3F2Y25vemJhamJpaGloIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODkxODE0MzUsImV4cCI6MjEwNDc1NzQzNX0.0je94XVltdzs5kniPaGGw5b7Y5FmyDoDgLSlyy4JtGE")

RFID_INVENTORY_DB_FILE = os.path.join(os.path.dirname(__file__), "warehouse_inventory.json")
EVENTS_LOG_FILE = os.path.join(os.path.dirname(__file__), "events.jsonl")
