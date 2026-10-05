"""
Door & Motor Controller Subsystem.
Manages H-Bridge Motor (GPIO 17 & 27) and Magnetic Reed Switch (GPIO 22).
Enforces 10-second safety timeout to protect motor from burnout.
"""

import time
import threading
from config import MOTOR_IN1, MOTOR_IN2, DOOR_SENSOR_PIN, MAX_DOOR_CLOSE_TIME, DOOR_CHECK_INTERVAL

try:
    from gpiozero import OutputDevice, Button
    GPIO_AVAILABLE = True
except (ImportError, Exception):
    GPIO_AVAILABLE = False
    print("[WARN] gpiozero not available. Running DoorController in simulation mode.")

class DoorController:
    def __init__(self):
        self.motor_in1 = None
        self.motor_in2 = None
        self.sensor = None
        self._lock = threading.Lock()
        self.is_closing = False
        self.last_action_status = "idle"
        self._simulated_door_closed = True

        if GPIO_AVAILABLE:
            try:
                self.motor_in1 = OutputDevice(MOTOR_IN1, initial_value=False)
                self.motor_in2 = OutputDevice(MOTOR_IN2, initial_value=False)
                self.sensor = Button(DOOR_SENSOR_PIN, pull_up=True)
                print("[DOOR] Hardware GPIO initialized successfully.")
            except Exception as e:
                print(f"[DOOR ERROR] Failed to initialize GPIO: {e}")

    @property
    def is_closed(self) -> bool:
        """Returns True if the magnetic sensor reports door closed."""
        if self.sensor:
            try:
                return self.sensor.is_pressed
            except Exception:
                return False
        return self._simulated_door_closed

    def stop_motor(self):
        """Immediately de-energizes both H-bridge motor pins."""
        if self.motor_in1 and self.motor_in2:
            self.motor_in1.off()
            self.motor_in2.off()

    def close_door_async(self, callback=None):
        """Launches threaded door closing sequence."""
        with self._lock:
            if self.is_closing:
                print("[DOOR] Close operation already in progress.")
                return False
            self.is_closing = True

        thread = threading.Thread(target=self._close_sequence, args=(callback,), daemon=True)
        thread.start()
        return True

    def _close_sequence(self, callback):
        print("[DOOR] -> Initiating door close sequence...")
        self.last_action_status = "closing"
        start_time = time.time()

        if self.motor_in1 and self.motor_in2:
            # IN1 = ON, IN2 = OFF to close door
            self.motor_in1.on()
            self.motor_in2.off()

        door_closed_confirmed = False

        while (time.time() - start_time) < MAX_DOOR_CLOSE_TIME:
            if self.is_closed:
                door_closed_confirmed = True
                break
            time.sleep(DOOR_CHECK_INTERVAL)

        # Stop motor immediately
        self.stop_motor()
        self.is_closing = False

        if door_closed_confirmed:
            self.last_action_status = "closed_confirmed"
            print(f"[DOOR SUCCESS] Door successfully closed and confirmed in {time.time() - start_time:.2f}s.")
        else:
            self.last_action_status = "timeout_safety_stop"
            print(f"[DOOR WARN] Safety timeout ({MAX_DOOR_CLOSE_TIME}s) reached. Motor stopped for protection.")

        if callback:
            callback(door_closed_confirmed)

    def manual_open(self):
        """Manual open pulse for testing."""
        if self.motor_in1 and self.motor_in2:
            self.motor_in1.off()
            self.motor_in2.on()
            time.sleep(1.0)
            self.stop_motor()
        self._simulated_door_closed = False

# Global singleton
door = DoorController()
