"""
Flask Server & Central Web Dashboard.
Provides:
- POST /sensor: Ingests ESP32 sensor telemetry (returns {"status":"received"})
- GET /video0 & /video1: Live MJPEG camera video streams
- REST APIs for Telemetry, Warehouse RFID Inventory, and Door Controls
- Responsive Web Dashboard UI
"""

from flask import Flask, request, jsonify, Response, render_template_string
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
    logger.log_event(payload["event_type"], payload["tag"])
rfid.register_callback(on_rfid_event)

# ==========================================
# 📡 SENSOR ENDPOINT (ESP32 -> Pi)
# ==========================================
@app.route("/sensor", methods=["POST"])
def receive_sensor_data():
    """Receives JSON payload from ESP32 every ~2s."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload"}), 400

        # Evaluate states and trigger audio alerts
        alerts = sensors.evaluate(data)
        if alerts:
            logger.log_event("SENSOR_ALARM", {"active_alerts": alerts, "raw": data})

        # Match exact verified HTTP response
        return jsonify({"status": "received"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/sensor", methods=["GET"])
def sensor_get():
    return jsonify({"status": "online", "last_sensor": sensors.latest_sensor_data}), 200


# ==========================================
# 📷 VIDEO STREAMING ENDPOINTS
# ==========================================
@app.route("/video0")
def video_feed_cam0():
    """Camera 0 (AI Face Recognition stream)."""
    return Response(vision.generate_mjpeg_stream(0),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/video1")
def video_feed_cam1():
    """Camera 1 (Live Video stream)."""
    return Response(vision.generate_mjpeg_stream(1),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# ==========================================
# 🗄️ REST API ENDPOINTS
# ==========================================
@app.route("/status")
@app.route("/api/status")
def api_status():
    """Returns complete system telemetry."""
    return jsonify({
        "system": "MSME 4.0 IoT Warehouse Security Master",
        "sensors": sensors.get_status_dict(),
        "door": {
            "closed": door.is_closed,
            "status": door.last_action_status,
            "is_closing": door.is_closing
        },
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
    })

@app.route("/sensor-status")
def api_sensor_status():
    """Returns latest sensor readings and active alarms."""
    return jsonify(sensors.get_status_dict())

@app.route("/api/inventory")
def api_inventory():
    """Returns active warehouse inventory and recent RFID check-ins/outs."""
    return jsonify({
        "count": len(rfid.active_inventory),
        "items": [rec.to_dict() for rec in rfid.active_inventory.values()]
    })

@app.route("/api/events")
def api_events():
    """Returns recent security and RFID events."""
    return jsonify({"events": logger.get_recent_events(30)})

@app.route("/api/door/close", methods=["POST"])
def api_door_close():
    door.close_door_async()
    logger.log_event("DOOR_MANUAL_CLOSE", {})
    return jsonify({"status": "closing_initiated"})

@app.route("/api/door/open", methods=["POST"])
def api_door_open():
    door.manual_open()
    logger.log_event("DOOR_MANUAL_OPEN", {})
    return jsonify({"status": "open_pulse_triggered"})

@app.route("/api/audio/test/<int:num>", methods=["POST"])
def api_audio_test(num):
    audio.play(num, force=True)
    return jsonify({"status": "audio_triggered", "track": num})


# ==========================================
# 🖥️ MASTER WEB DASHBOARD UI
# ==========================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MSME 4.0 - Warehouse Security Command Center</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0d1117; --card-bg: #161b22; --border: #30363d;
      --accent: #58a6ff; --green: #238636; --red: #da3633; --text: #c9d1d9;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
    body { background: var(--bg); color: var(--text); padding: 20px; }
    .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 15px; margin-bottom: 20px; }
    .header h1 { font-size: 1.5rem; color: #fff; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 18px; }
    .card h2 { font-size: 1.1rem; color: var(--accent); margin-bottom: 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
    .video-container { width: 100%; height: 240px; background: #000; border-radius: 6px; overflow: hidden; display: flex; align-items: center; justify-content: center; }
    .video-container img { width: 100%; height: 100%; object-fit: cover; }
    .stat-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #21262d; }
    .badge { padding: 3px 8px; border-radius: 12px; font-size: 0.8rem; font-weight: 600; }
    .badge-ok { background: rgba(35,134,54,0.2); color: #3fb950; }
    .badge-alert { background: rgba(218,54,51,0.2); color: #f85149; }
    .btn { background: var(--accent); color: #fff; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: 500; margin-right: 8px; }
    .btn-danger { background: var(--red); }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.85rem; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); }
    th { color: var(--accent); }
  </style>
</head>
<body>
  <div class="header">
    <h1>🏭 MSME 4.0 - Warehouse Security Command Center</h1>
    <div>
      <span class="badge badge-ok" id="sys-status">SYSTEM ONLINE</span>
    </div>
  </div>

  <div class="grid">
    <!-- Camera 0 -->
    <div class="card">
      <h2>📷 Camera 0 - AI Face Recognition</h2>
      <div class="video-container">
        <img src="/video0" alt="Camera 0 Stream">
      </div>
      <div class="stat-row" style="margin-top: 10px;">
        <span>Recognized Person:</span>
        <strong id="ai-person">Scanning...</strong>
      </div>
      <div class="stat-row">
        <span>AI State:</span>
        <span id="ai-state">Idle</span>
      </div>
    </div>

    <!-- Camera 1 -->
    <div class="card">
      <h2>📷 Camera 1 - Live Warehouse View</h2>
      <div class="video-container">
        <img src="/video1" alt="Camera 1 Stream">
      </div>
      <div class="stat-row" style="margin-top: 10px;">
        <span>Stream Status:</span>
        <span class="badge badge-ok">ACTIVE</span>
      </div>
    </div>

    <!-- Telemetry & Door Controls -->
    <div class="card">
      <h2>🚪 Door & Environmental Telemetry</h2>
      <div class="stat-row">
        <span>Door Status:</span>
        <strong id="door-status">Checking...</strong>
      </div>
      <div class="stat-row">
        <span>ESP32 Sensor Link:</span>
        <span id="esp32-status" class="badge badge-ok">ONLINE</span>
      </div>
      <div class="stat-row">
        <span>Temperature:</span>
        <strong id="sensor-temp">-- °C</strong>
      </div>
      <div class="stat-row">
        <span>Humidity:</span>
        <strong id="sensor-hum">-- %</strong>
      </div>
      <div class="stat-row">
        <span>Active Alarms:</span>
        <span id="active-alarms" class="badge badge-ok">NONE</span>
      </div>
      <div style="margin-top: 15px;">
        <button class="btn btn-danger" onclick="closeDoor()">Close Door</button>
        <button class="btn" onclick="openDoor()">Open Door</button>
      </div>
    </div>

    <!-- RFID Warehouse Inventory -->
    <div class="card" style="grid-column: 1 / -1;">
      <h2>🏷️ Active Warehouse RFID Inventory & Movement</h2>
      <div style="max-height: 220px; overflow-y: auto;">
        <table>
          <thead>
            <tr>
              <th>EPC Identifier</th>
              <th>Status</th>
              <th>Check-In Time</th>
              <th>Last Seen</th>
              <th>Dwell Time</th>
              <th>Scans</th>
            </tr>
          </thead>
          <tbody id="inventory-table">
            <tr><td colspan="6">No items currently in warehouse.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    async function updateDashboard() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        // Update AI & Door
        document.getElementById('ai-person').innerText = data.vision.last_person;
        document.getElementById('ai-state').innerText = data.vision.status;
        document.getElementById('door-status').innerText = data.door.closed ? '🟢 Closed' : '🔴 Open';
        
        // Update Sensors
        const s = data.sensors;
        document.getElementById('esp32-status').className = s.online ? 'badge badge-ok' : 'badge badge-alert';
        document.getElementById('esp32-status').innerText = s.online ? 'ONLINE' : 'OFFLINE';
        if (s.data && s.data.temperature !== undefined) {
          document.getElementById('sensor-temp').innerText = s.data.temperature + ' °C';
          document.getElementById('sensor-hum').innerText = s.data.humidity + ' %';
        }
        document.getElementById('active-alarms').innerText = s.active_alarms.length > 0 ? s.active_alarms.join(', ') : 'NONE';
        document.getElementById('active-alarms').className = s.active_alarms.length > 0 ? 'badge badge-alert' : 'badge badge-ok';

        // Update Inventory Table
        const invRes = await fetch('/api/inventory');
        const invData = await invRes.json();
        const tbody = document.getElementById('inventory-table');
        if (invData.items.length > 0) {
          tbody.innerHTML = invData.items.map(item => `
            <tr>
              <td><code>${item.epc}</code></td>
              <td><span class="badge ${item.status === 'IN_WAREHOUSE' ? 'badge-ok' : 'badge-alert'}">${item.status}</span></td>
              <td>${item.first_seen}</td>
              <td>${item.last_seen}</td>
              <td>${item.dwell_seconds}s</td>
              <td>${item.read_count}</td>
            </tr>
          `).join('');
        }
      } catch (e) { console.error(e); }
    }

    async function closeDoor() { await fetch('/api/door/close', { method: 'POST' }); }
    async function openDoor() { await fetch('/api/door/open', { method: 'POST' }); }

    setInterval(updateDashboard, 1500);
    updateDashboard();
  </script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

def run_server():
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True)

if __name__ == "__main__":
    run_server()
