"""
MSME 4.0 Raspberry Pi Headless Edge Controller & Streaming Server.
Features:
- Headless API architecture (No heavy local web UI rendered on Pi)
- High-Performance MJPEG Video Streaming (/video0 AI Biometric & /video1 Live Feed)
- ESP32 Sensor Telemetry Ingress (/sensor)
- Full Motorized Door Actuation APIs (/api/door/open, /close, /stop, /status)
- Centralized Data Sync to Supabase Cloud DB
"""

import time
from flask import Flask, request, jsonify, Response
from config import FLASK_PORT
from sensor_evaluator import sensors
from vision_manager import vision
from door_controller import door
from rfid_manager import rfid
from audio_manager import audio
from event_logger import logger
from db_manager import db

app = Flask(__name__)

# Register RFID event logger callback
def on_rfid_event(payload):
    logger.log_event(payload.get("event", "RFID_SCAN"), payload)
rfid.register_callback(on_rfid_event)

# ==========================================
# 🌐 CORS MIDDLEWARE (Allow Laptop Web App)
# ==========================================
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, PUT, DELETE'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    return response

@app.route("/api/ping")
def ping_check():
    return jsonify({"status": "pong", "time": time.time()}), 200

# ==========================================
# 🏠 HEADLESS ROOT & HEALTH CHECK
# ==========================================
@app.route("/")
def headless_index():
    """Returns pure JSON endpoint directory for remote laptop web app."""
    return jsonify({
        "status": "online",
        "system": "MSME 4.0 - Raspberry Pi Headless Edge Controller",
        "streams": {
            "camera0_ai_biometric": "/video0",
            "camera1_live_surveillance": "/video1"
        },
        "endpoints": {
            "sensor_ingress": "POST /sensor",
            "sensor_status": "GET /sensor",
            "door_close": "POST /api/door/close",
            "door_open": "POST /api/door/open",
            "door_stop": "POST /api/door/stop",
            "door_status": "GET /api/door/status",
            "system_status": "GET /api/status",
            "inventory_cache": "GET /api/inventory"
        },
        "door_state": door.get_status_dict(),
        "database_sync": db.last_sync_status
    }), 200

# ==========================================
# 📡 SENSOR TELEMETRY INGRESS (ESP32 -> Pi)
# ==========================================
@app.route("/sensor", methods=["POST"])
def receive_sensor_data():
    """Receives JSON payload from ESP32 every ~2s."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload"}), 400

        # Evaluate states, trigger audio alerts, and push to Supabase
        alerts = sensors.evaluate(data)
        if alerts:
            logger.log_event("SENSOR_ALARM", {"active_alerts": alerts, "raw": data})

        return jsonify({"status": "received"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/sensor", methods=["GET"])
def sensor_get():
    """Returns latest sensor readings cached on Pi."""
    return jsonify({"status": "online", "last_sensor": sensors.latest_sensor_data}), 200

# ==========================================
# 📷 VIDEO STREAMING ENDPOINTS
# ==========================================
@app.route("/video0")
def video_feed_cam0():
    """Camera 0: AI Face Recognition stream with visual bounding boxes."""
    return Response(vision.generate_mjpeg_stream(0),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/video1")
def video_feed_cam1():
    """Camera 1: Live Surveillance stream."""
    return Response(vision.generate_mjpeg_stream(1),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ==========================================
# 🚪 MOTORIZED DOOR CONTROL APIS
# ==========================================
@app.route("/api/door/close", methods=["POST"])
def api_door_close():
    success = door.close_door_async()
    logger.log_event("DOOR_REMOTE_CLOSE", {})
    return jsonify({"status": "closing_initiated" if success else "already_moving"}), 200

@app.route("/api/door/open", methods=["POST"])
def api_door_open():
    duration = 4.0
    try:
        req = request.get_json(silent=True) or {}
        duration = float(req.get("duration", 4.0))
    except Exception:
        pass
    success = door.open_door_async(duration=duration)
    logger.log_event("DOOR_REMOTE_OPEN", {"duration": duration})
    return jsonify({"status": "opening_initiated" if success else "already_moving"}), 200

@app.route("/api/door/stop", methods=["POST"])
def api_door_stop():
    door.stop_motor()
    logger.log_event("DOOR_EMERGENCY_STOP", {})
    return jsonify({"status": "stopped"}), 200

@app.route("/api/door/invert", methods=["POST"])
def api_door_invert():
    new_state = door.toggle_polarity()
    return jsonify({"status": "polarity_toggled", "polarity_inverted": new_state}), 200

@app.route("/api/door/status", methods=["GET"])
def api_door_status():
    return jsonify(door.get_status_dict()), 200

# ==========================================
# 🗄️ TELEMETRY & INVENTORY APIS
# ==========================================
@app.route("/status")
@app.route("/api/status")
def api_status():
    """Returns complete real-time edge telemetry."""
    return jsonify({
        "system": "MSME 4.0 IoT Warehouse Security Master",
        "sensors": sensors.get_status_dict(),
        "door": door.get_status_dict(),
        "vision": {
            "last_person": vision.last_detected_person,
            "status": vision.last_recognition_status
        },
        "audio": {
            "last_track": audio.last_audio_requested,
            "status": audio.last_audio_status
        },
        "rfid": {
            "checkin_port": rfid.checkin_port,
            "checkout_port": rfid.checkout_port,
            "active_items_count": len(rfid.active_inventory)
        },
        "database": {
            "sync_status": db.last_sync_status,
            "last_sync": db.last_sync_time
        }
    }), 200

@app.route("/api/inventory")
def api_inventory():
    """Returns active warehouse inventory cache."""
    return jsonify({
        "count": len(rfid.active_inventory),
        "items": [rec.to_dict() for rec in rfid.active_inventory.values()]
    }), 200

@app.route("/api/events")
def api_events():
    return jsonify({"events": logger.get_recent_events(30)}), 200

@app.route("/api/audio/test/<int:num>", methods=["POST"])
def api_audio_test(num):
    audio.play(num, force=True)
    return jsonify({"status": "audio_triggered", "track": num}), 200

def run_server():
    print(f"\n[SERVER] Headless Edge Streaming Server running on port {FLASK_PORT}")
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True)

if __name__ == "__main__":
    run_server()
