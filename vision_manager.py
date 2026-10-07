"""
Vision Subsystem & Background AI Face Recognition Manager.
Controls Camera 0 (AI Face Recognition) and Camera 1 (Live Surveillance Stream).
Features:
- Dual-Backend Support: Picamera2 (CSI) with automatic fallback to OpenCV (USB V4L2)
- Biometric verification against known_faces/ folder
- Automatic Unauthorized Intruder detection (even when known_faces is empty)
- 3-frame confirmation triggers:
  1. Cropped snapshot saved to unknown_faces/
  2. Dispatches Audio Track 7 (Unauthorized Face Alert)
  3. Initiates motorized security door closing sequence
  4. Realtime alert logged to Supabase Cloud DB
- Live visual bounding box annotations on /video0 stream
- Synthetic standby HUD when physical cameras are offline
"""

import os
import sys
import time
import glob
import threading
import cv2
import numpy as np
from datetime import datetime
from typing import Optional, List, Tuple

from config import (
    CAMERA_WIDTH, CAMERA_HEIGHT, FACE_TOLERANCE, CONFIRMATION_FRAMES,
    FACE_COOLDOWN_SEC, KNOWN_FACES_DIR, UNKNOWN_FACES_DIR
)
from audio_manager import audio
from door_controller import door
from db_manager import db

try:
    import face_recognition
    FACE_REC_AVAILABLE = True
except ImportError:
    FACE_REC_AVAILABLE = False
    print("[WARN] face_recognition library not installed. Face recognition running in simulated mode.")

try:
    from picamera2 import Picamera2
    PICAMERA2_AVAILABLE = True
except (ImportError, Exception):
    PICAMERA2_AVAILABLE = False
    print("[WARN] Picamera2 not available. Using OpenCV webcam or synthetic video.")

class VisionManager:
    def __init__(self):
        self.cam0 = None
        self.cam0_backend = None  # "picamera2", "cv2", or None
        self.cam1 = None
        self.cam1_backend = None  # "picamera2", "cv2", or None

        self.known_face_encodings = []
        self.known_face_names = []

        self.latest_frame_cam0 = None
        self.latest_frame_cam1 = None
        self._annotated_cam0_frame = None
        self._lock = threading.Lock()

        self._running = False
        self._ai_thread = None
        self.last_unauthorized_time = 0
        self.unknown_consecutive_count = 0
        self.last_detected_person = "None"
        self.last_recognition_status = "idle"

        # Ensure directories exist
        os.makedirs(KNOWN_FACES_DIR, exist_ok=True)
        os.makedirs(UNKNOWN_FACES_DIR, exist_ok=True)

        self._load_known_faces()
        self._init_cameras()

    def _load_known_faces(self):
        """Loads all reference images from known_faces/ folder."""
        if not FACE_REC_AVAILABLE:
            return

        image_files = glob.glob(os.path.join(KNOWN_FACES_DIR, "*.*"))
        self.known_face_encodings.clear()
        self.known_face_names.clear()

        for img_path in image_files:
            try:
                name = os.path.splitext(os.path.basename(img_path))[0]
                image = face_recognition.load_image_file(img_path)
                encodings = face_recognition.face_encodings(image)
                if encodings:
                    self.known_face_encodings.append(encodings[0])
                    self.known_face_names.append(name)
                    print(f"[VISION] Loaded authorized reference face: '{name}'")
                else:
                    print(f"[VISION WARN] No face found in {img_path}")
            except Exception as e:
                print(f"[VISION ERROR] Error loading {img_path}: {e}")

        print(f"[VISION] Total authorized people loaded: {len(self.known_face_names)}")

    @staticmethod
    def _find_opencv_capture_devices() -> list:
        """Scans for real video capture devices on Linux/Windows, filtering out hardware codec nodes."""
        valid_devices = []

        # 1. On Linux, check /dev/v4l/by-id/ symlinks first (guaranteed USB webcams)
        if sys.platform.startswith("linux") and os.path.exists("/dev/v4l/by-id"):
            try:
                import glob
                for path in sorted(glob.glob("/dev/v4l/by-id/*")):
                    try:
                        cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
                        if cap.isOpened():
                            ret, frame = cap.read()
                            if ret and frame is not None and frame.size > 0:
                                valid_devices.append(path)
                            cap.release()
                    except Exception:
                        pass
            except Exception:
                pass

        # 2. Check numeric indices with CAP_V4L2 on Linux or default backend on Windows
        candidates = [0, 2, 4, 10, 11, 12, 14, 16, 1, 3]
        for idx in candidates:
            if idx in valid_devices:
                continue
            try:
                cap = cv2.VideoCapture(idx, cv2.CAP_V4L2) if sys.platform.startswith("linux") else cv2.VideoCapture(idx)
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret and frame is not None and frame.size > 0:
                        valid_devices.append(idx)
                    cap.release()
            except Exception:
                continue

        return valid_devices

    def _init_cameras(self):
        """Initializes Picamera2 CSI instances or scans for active USB webcams."""
        # Camera 0 Init (AI Recognition Cam)
        if PICAMERA2_AVAILABLE:
            try:
                self.cam0 = Picamera2(0)
                self.cam0.configure(self.cam0.create_preview_configuration(
                    main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"}
                ))
                self.cam0.start()
                self.cam0_backend = "picamera2"
                print("✅ [VISION] Camera 0 (Picamera2 CSI) initialized for AI recognition.")
            except Exception as e:
                self.cam0 = None

        # Camera 1 Init (Live Surveillance Feed Cam)
        if PICAMERA2_AVAILABLE:
            try:
                self.cam1 = Picamera2(1)
                self.cam1.configure(self.cam1.create_preview_configuration(
                    main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"}
                ))
                self.cam1.start()
                self.cam1_backend = "picamera2"
                print("✅ [VISION] Camera 1 (Picamera2 CSI) initialized for Live stream.")
            except Exception:
                self.cam1 = None

        # If CSI cameras not active, scan for USB capture devices
        if self.cam0 is None or self.cam1 is None:
            active_usb_indices = self._find_opencv_capture_devices()
            print(f"[VISION] Active USB Camera Indices Discovered: {active_usb_indices}")

            if self.cam0 is None and len(active_usb_indices) > 0:
                idx0 = active_usb_indices.pop(0)
                try:
                    cap0 = cv2.VideoCapture(idx0)
                    if cap0.isOpened():
                        cap0.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
                        cap0.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
                        self.cam0 = cap0
                        self.cam0_backend = "cv2"
                        print(f"✅ [VISION] Camera 0 (USB Webcam index {idx0}) initialized for AI.")
                except Exception as e:
                    print(f"[VISION WARN] Failed to open USB Camera 0: {e}")

            if self.cam1 is None and len(active_usb_indices) > 0:
                idx1 = active_usb_indices.pop(0)
                try:
                    cap1 = cv2.VideoCapture(idx1)
                    if cap1.isOpened():
                        cap1.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
                        cap1.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
                        self.cam1 = cap1
                        self.cam1_backend = "cv2"
                        print(f"✅ [VISION] Camera 1 (USB Webcam index {idx1}) initialized for Live stream.")
                except Exception as e:
                    print(f"[VISION WARN] Failed to open USB Camera 1: {e}")

        if self.cam0 is None:
            print("[VISION INFO] Camera 0 running in HUD standby mode.")
        if self.cam1 is None:
            print("[VISION INFO] Camera 1 running in HUD standby mode.")

    def start(self):
        """Starts background AI face recognition thread."""
        if self._running:
            return
        self._running = True
        self._ai_thread = threading.Thread(target=self._ai_face_loop, daemon=True)
        self._ai_thread.start()

    def _grab_cam0_frame(self):
        if self.cam0:
            try:
                if self.cam0_backend == "picamera2":
                    return self.cam0.capture_array()
                elif self.cam0_backend == "cv2":
                    ret, frame = self.cam0.read()
                    if ret and frame is not None:
                        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            except Exception:
                return None
        return None

    def _grab_cam1_frame(self):
        if self.cam1:
            try:
                if self.cam1_backend == "picamera2":
                    return self.cam1.capture_array()
                elif self.cam1_backend == "cv2":
                    ret, frame = self.cam1.read()
                    if ret and frame is not None:
                        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            except Exception:
                return None
        return None

    def _generate_standby_frame(self, title: str):
        """Generates a clean synthetic standby HUD frame when physical feed is offline."""
        frame = np.zeros((CAMERA_HEIGHT, CAMERA_WIDTH, 3), dtype=np.uint8)
        frame[:] = (24, 28, 36)

        cv2.rectangle(frame, (12, 12), (CAMERA_WIDTH - 12, CAMERA_HEIGHT - 12), (70, 85, 105), 2)
        cv2.line(frame, (12, 50), (CAMERA_WIDTH - 12, 50), (70, 85, 105), 1)

        cv2.putText(frame, title.upper(), (30, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 210, 255), 2)
        cv2.putText(frame, "STANDBY FEED - WAITING FOR CAMERA", (50, CAMERA_HEIGHT // 2 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 190, 205), 2)

        now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        cv2.putText(frame, f"TIMESTAMP: {now_str}", (50, CAMERA_HEIGHT // 2 + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 140, 160), 1)
        return frame

    def _ai_face_loop(self):
        """Dedicated background AI face recognition loop."""
        print("[VISION] Dedicated AI Face Recognition background worker running.")
        while self._running:
            frame = self._grab_cam0_frame()
            if frame is None:
                time.sleep(0.15)
                continue

            with self._lock:
                self.latest_frame_cam0 = frame

            if not FACE_REC_AVAILABLE:
                time.sleep(0.2)
                continue

            # Scale down frame to 0.5x for high-speed AI processing
            small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            # Ensure RGB format for face_recognition
            rgb_small = small_frame

            face_locations = face_recognition.face_locations(rgb_small)
            if not face_locations:
                self.unknown_consecutive_count = 0
                self.last_recognition_status = "idle_no_face"
                with self._lock:
                    self._annotated_cam0_frame = frame.copy()
                time.sleep(0.1)
                continue

            # A face IS detected! Encode it
            face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

            annotated = frame.copy()
            has_unknown_intruder = False
            primary_detected_name = "Unknown"

            for face_encoding, face_loc in zip(face_encodings, face_locations):
                is_authorized = False
                person_name = "Unknown"

                # Check against known faces if available
                if self.known_face_encodings:
                    matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=FACE_TOLERANCE)
                    face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)

                    if True in matches:
                        best_match_idx = face_distances.argmin()
                        if face_distances[best_match_idx] <= FACE_TOLERANCE:
                            is_authorized = True
                            person_name = self.known_face_names[best_match_idx]

                # Scale back coordinates (0.5x -> 2x)
                top, right, bottom, left = [c * 2 for c in face_loc]

                if is_authorized:
                    box_color = (0, 255, 0) # Green for Authorized
                    label = f"AUTHORIZED: {person_name.upper()}"
                    primary_detected_name = person_name
                else:
                    box_color = (255, 0, 0) # Red for Intruder in RGB
                    label = "UNAUTHORIZED INTRUDER"
                    has_unknown_intruder = True

                # Draw bounding box and label on annotated frame
                cv2.rectangle(annotated, (left, top), (right, bottom), box_color, 2)
                cv2.rectangle(annotated, (left, bottom - 26), (right, bottom), box_color, cv2.FILLED)
                cv2.putText(annotated, label, (left + 6, bottom - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            with self._lock:
                self._annotated_cam0_frame = annotated

            # Evaluate state logic
            if has_unknown_intruder:
                self.unknown_consecutive_count += 1
                self.last_detected_person = "Unauthorized Intruder"
                self.last_recognition_status = f"INTRUDER_DETECTED (Frame {self.unknown_consecutive_count}/{CONFIRMATION_FRAMES})"
                print(f"⚠️ [VISION ALERT] Unknown face detected: Frame {self.unknown_consecutive_count}/{CONFIRMATION_FRAMES}")

                now = time.time()
                if self.unknown_consecutive_count >= CONFIRMATION_FRAMES and (now - self.last_unauthorized_time > FACE_COOLDOWN_SEC):
                    self.last_unauthorized_time = now
                    self.unknown_consecutive_count = 0
                    self._trigger_unauthorized_protocol(frame, face_locations[0])
            else:
                self.unknown_consecutive_count = 0
                self.last_detected_person = primary_detected_name
                self.last_recognition_status = f"Authorized ({primary_detected_name})"

            time.sleep(0.08)

    def _trigger_unauthorized_protocol(self, full_frame, face_loc_scaled):
        """Executes the Unauthorized Person Security Protocol."""
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_filename = f"{now_str}.jpg"
        img_path = os.path.join(UNKNOWN_FACES_DIR, img_filename)

        top, right, bottom, left = [c * 2 for c in face_loc_scaled]
        h, w = full_frame.shape[:2]
        top = max(0, top - 20)
        left = max(0, left - 20)
        bottom = min(h, bottom + 20)
        right = min(w, right + 20)

        face_crop = full_frame[top:bottom, left:right]
        try:
            cv2.imwrite(img_path, cv2.cvtColor(face_crop, cv2.COLOR_RGB2BGR))
            print(f"\n🚨 [SECURITY BREACH] Unauthorized intruder confirmed! Snapshot saved to: {img_path}")
        except Exception as e:
            print(f"[VISION ERROR] Could not save snapshot: {e}")

        # 1. Trigger Audio Track 7 (Unauthorized Face Alert)
        audio.play(7, force=True)

        # 2. Trigger Motorized Door Closing Sequence
        print("[SECURITY ACTION] Closing warehouse motorized security door immediately!")
        door.close_door_async()

        # 3. Log Critical Alert to Supabase
        db.log_security_alert("UNAUTHORIZED_FACE", "CRITICAL", {
            "snapshot_file": img_filename,
            "detected_at": now_str,
            "camera": "Camera 0 (AI Gate)",
            "door_action": "AUTOMATIC_CLOSE_TRIGGERED"
        })

    def generate_mjpeg_stream(self, camera_index: int):
        """Yields multipart MJPEG stream for Flask streaming endpoint. Never exits or drops connection."""
        if not self._running:
            self.start()

        while True:
            try:
                frame = None
                if camera_index == 0:
                    with self._lock:
                        frame = self._annotated_cam0_frame or self.latest_frame_cam0
                    if frame is None:
                        frame = self._grab_cam0_frame()
                    if frame is None:
                        frame = self._generate_standby_frame("Camera 0 (AI Face Biometric)")
                else:
                    frame = self._grab_cam1_frame()
                    if frame is None:
                        frame = self._generate_standby_frame("Camera 1 (Live Surveillance)")

                bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if (frame is not None and len(frame.shape) == 3) else frame
                if bgr_frame is None:
                    bgr_frame = self._generate_standby_frame(f"Camera {camera_index}")

                ret, jpeg = cv2.imencode('.jpg', bgr_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                if not ret:
                    time.sleep(0.05)
                    continue

                raw_bytes = jpeg.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n'
                       b'Content-Length: ' + str(len(raw_bytes)).encode() + b'\r\n\r\n' +
                       raw_bytes + b'\r\n')
                time.sleep(0.04)
            except Exception as e:
                # Fallback to standby on unexpected frame capture error so stream never breaks
                try:
                    fallback = self._generate_standby_frame(f"Camera {camera_index} Stream")
                    ret, jpeg = cv2.imencode('.jpg', fallback, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    if ret:
                        raw_bytes = jpeg.tobytes()
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n'
                               b'Content-Length: ' + str(len(raw_bytes)).encode() + b'\r\n\r\n' +
                               raw_bytes + b'\r\n')
                except Exception:
                    pass
                time.sleep(0.1)

    def stop(self):
        """Stops cameras cleanly."""
        self._running = False
        if self.cam0:
            try:
                if self.cam0_backend == "picamera2":
                    self.cam0.stop()
                elif self.cam0_backend == "cv2":
                    self.cam0.release()
            except Exception:
                pass
        if self.cam1:
            try:
                if self.cam1_backend == "picamera2":
                    self.cam1.stop()
                elif self.cam1_backend == "cv2":
                    self.cam1.release()
            except Exception:
                pass

# Global singleton
vision = VisionManager()
