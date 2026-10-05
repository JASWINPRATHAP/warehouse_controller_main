"""
Vision Subsystem & Background AI Face Recognition Manager.
Controls Camera 0 (Picamera2 AI Face Recognition) and Camera 1 (Live Video Feed).
Triggers Audio 7 and Door Close sequence upon 3-frame confirmation of unknown face.
"""

import os
import time
import glob
import threading
import cv2
from datetime import datetime
from typing import Optional, List, Tuple
from config import (
    CAMERA_WIDTH, CAMERA_HEIGHT, FACE_TOLERANCE, CONFIRMATION_FRAMES,
    FACE_COOLDOWN_SEC, KNOWN_FACES_DIR, UNKNOWN_FACES_DIR
)
from audio_manager import audio
from door_controller import door

import numpy as np

try:
    import face_recognition
    FACE_REC_AVAILABLE = True
except ImportError:
    FACE_REC_AVAILABLE = False
    print("[WARN] face_recognition library not installed. Face recognition will run in simulation mode.")

try:
    from picamera2 import Picamera2
    PICAMERA2_AVAILABLE = True
except (ImportError, Exception):
    PICAMERA2_AVAILABLE = False
    print("[WARN] Picamera2 not available. Using OpenCV webcam or simulated video.")

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
                    print(f"[VISION] Loaded authorized face reference: '{name}'")
                else:
                    print(f"[VISION WARN] No face found in {img_path}")
            except Exception as e:
                print(f"[VISION ERROR] Error loading {img_path}: {e}")

    def _init_cameras(self):
        """Initializes Picamera2 instances or falls back to OpenCV V4L2 USB cameras."""
        # Camera 0 Init (AI Recognition Cam)
        if PICAMERA2_AVAILABLE:
            try:
                self.cam0 = Picamera2(0)
                self.cam0.configure(self.cam0.create_preview_configuration(main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"}))
                self.cam0.start()
                self.cam0_backend = "picamera2"
                print("[VISION] Camera 0 (Picamera2 CSI) initialized for AI recognition.")
            except Exception as e:
                print(f"[VISION INFO] Picamera2(0) unavailable ({e}). Trying OpenCV VideoCapture(0)...")
                self.cam0 = None

        if self.cam0 is None:
            try:
                cap0 = cv2.VideoCapture(0)
                if cap0.isOpened():
                    cap0.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
                    cap0.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
                    self.cam0 = cap0
                    self.cam0_backend = "cv2"
                    print("[VISION] Camera 0 (OpenCV USB V4L2) initialized for AI recognition.")
                else:
                    print("[VISION WARN] No camera device found for Camera 0.")
            except Exception as e:
                print(f"[VISION WARN] OpenCV Camera 0 failed: {e}")

        # Camera 1 Init (Live Feed Cam)
        if PICAMERA2_AVAILABLE:
            try:
                self.cam1 = Picamera2(1)
                self.cam1.configure(self.cam1.create_preview_configuration(main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"}))
                self.cam1.start()
                self.cam1_backend = "picamera2"
                print("[VISION] Camera 1 (Picamera2 CSI) initialized for Live stream.")
            except Exception as e:
                self.cam1 = None

        if self.cam1 is None:
            try:
                # Try index 1, or if cam0 was picamera2, try index 0 for USB
                cam1_idx = 1 if self.cam0_backend == "cv2" else 0
                cap1 = cv2.VideoCapture(cam1_idx)
                if cap1.isOpened():
                    cap1.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
                    cap1.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
                    self.cam1 = cap1
                    self.cam1_backend = "cv2"
                    print(f"[VISION] Camera 1 (OpenCV USB dev {cam1_idx}) initialized for Live stream.")
                else:
                    print("[VISION INFO] Camera 1 not connected. Standby placeholder active.")
            except Exception as e:
                print(f"[VISION INFO] Camera 1 fallback unavailable: {e}")

    def start(self):
        """Starts background frame acquisition and AI face recognition thread."""
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
            except Exception as e:
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
            except Exception as e:
                return None
        return None

    def _generate_standby_frame(self, title: str):
        """Generates a high-tech synthetic standby HUD frame when physical feed is unavailable."""
        frame = np.zeros((CAMERA_HEIGHT, CAMERA_WIDTH, 3), dtype=np.uint8)
        frame[:] = (24, 28, 36)  # Dark sleek slate RGB

        # Outer bounding box
        cv2.rectangle(frame, (12, 12), (CAMERA_WIDTH - 12, CAMERA_HEIGHT - 12), (70, 85, 105), 2)
        cv2.line(frame, (12, 50), (CAMERA_WIDTH - 12, 50), (70, 85, 105), 1)

        # Header Title
        cv2.putText(frame, title.upper(), (30, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 210, 255), 2)

        # Status text
        cv2.putText(frame, "STANDBY FEED - WAITING FOR INPUT", (50, CAMERA_HEIGHT // 2 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 190, 205), 2)

        now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        cv2.putText(frame, f"TIMESTAMP: {now_str}", (50, CAMERA_HEIGHT // 2 + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 140, 160), 1)

        return frame

    def _ai_face_loop(self):
        """Dedicated background AI face recognition loop (never blocks HTTP server)."""
        print("[VISION] AI Face Recognition background worker started.")
        while self._running:
            frame = self._grab_cam0_frame()
            if frame is None:
                time.sleep(0.15)
                continue

            with self._lock:
                self.latest_frame_cam0 = frame

            if not FACE_REC_AVAILABLE or not self.known_face_encodings:
                time.sleep(0.2)
                continue

            # Scale down frame slightly for high-speed AI processing
            small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            rgb_small = small_frame if self.cam0_backend == "picamera2" else small_frame

            face_locations = face_recognition.face_locations(rgb_small)
            if not face_locations:
                self.unknown_consecutive_count = 0
                self.last_recognition_status = "no_face"
                time.sleep(0.1)
                continue

            face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

            is_authorized = False
            detected_name = "Unknown"

            for face_encoding, face_loc in zip(face_encodings, face_locations):
                matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=FACE_TOLERANCE)
                face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)

                if True in matches:
                    best_match_index = face_distances.argmin()
                    if face_distances[best_match_index] <= FACE_TOLERANCE:
                        is_authorized = True
                        detected_name = self.known_face_names[best_match_index]
                        break

            if is_authorized:
                self.unknown_consecutive_count = 0
                self.last_detected_person = detected_name
                self.last_recognition_status = f"Authorized ({detected_name})"
            else:
                self.unknown_consecutive_count += 1
                self.last_detected_person = "Unknown"
                self.last_recognition_status = f"Unknown (Frame {self.unknown_consecutive_count}/{CONFIRMATION_FRAMES})"

                # Check if 3 confirmation frames reached and cooldown passed
                now = time.time()
                if self.unknown_consecutive_count >= CONFIRMATION_FRAMES and (now - self.last_unauthorized_time > FACE_COOLDOWN_SEC):
                    self.last_unauthorized_time = now
                    self.unknown_consecutive_count = 0
                    self._trigger_unauthorized_protocol(frame, face_locations[0])

            time.sleep(0.08)

    def _trigger_unauthorized_protocol(self, full_frame, face_loc_scaled):
        """Executes the Unauthorized Person Security Protocol."""
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_filename = f"{now_str}.jpg"
        img_path = os.path.join(UNKNOWN_FACES_DIR, img_filename)

        # Scale up bounding box back to original frame coordinates (fx=0.5 -> 2x)
        top, right, bottom, left = [coord * 2 for coord in face_loc_scaled]
        h, w = full_frame.shape[:2]
        top = max(0, top - 20)
        left = max(0, left - 20)
        bottom = min(h, bottom + 20)
        right = min(w, right + 20)

        face_crop = full_frame[top:bottom, left:right]
        try:
            cv2.imwrite(img_path, cv2.cvtColor(face_crop, cv2.COLOR_RGB2BGR))
            print(f"\n🚨 [SECURITY ALERT] Unauthorized face confirmed! Saved snapshot to: {img_path}")
        except Exception as e:
            print(f"[VISION ERROR] Could not save face snapshot: {e}")

        # 1. Trigger Audio 7 (Unauthorized face detected)
        audio.play(7, force=True)

        # 2. Trigger Door Closing sequence
        door.close_door_async()

    def generate_mjpeg_stream(self, camera_index: int):
        """Yields multipart MJPEG stream for Flask streaming endpoint."""
        while self._running:
            if camera_index == 0:
                frame = self.latest_frame_cam0
                if frame is None:
                    # Attempt a direct grab if background AI loop is initializing
                    frame = self._grab_cam0_frame()
                if frame is None:
                    frame = self._generate_standby_frame("Camera 0 (AI Face Recognition)")
                else:
                    frame = frame.copy()
                    # Overlay status box on Camera 0
                    overlay_text = f"AI: {self.last_recognition_status} | Person: {self.last_detected_person}"
                    color = (0, 255, 0) if "Authorized" in self.last_recognition_status else (0, 0, 255)
                    cv2.putText(frame, overlay_text, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            else:
                frame = self._grab_cam1_frame()
                if frame is None:
                    frame = self._generate_standby_frame("Camera 1 (Live Feed)")
                else:
                    frame = frame.copy()

            bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if len(frame.shape) == 3 else frame
            ret, jpeg = cv2.imencode('.jpg', bgr_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ret:
                time.sleep(0.05)
                continue

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            time.sleep(0.04)

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

