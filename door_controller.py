"""
Industrial Door & H-Bridge Motor Controller Subsystem.
Manages H-Bridge Motor (GPIO 17 & 27) and Magnetic Reed Switch (GPIO 22).
Features:
- Threaded non-blocking close_door_async() with safety timeout
- Threaded open_door_async() with configurable travel duration
- Emergency instant stop
- Hardware polarity toggle support
"""

import time
import threading
from typing import Optional, Callable
from config import (
    MOTOR_IN1, MOTOR_IN2, DOOR_SENSOR_PIN,
    MAX_DOOR_CLOSE_TIME, DOOR_CHECK_INTERVAL
)

try:
    from gpiozero import OutputDevice, Button
    GPIO_AVAILABLE = True
except (ImportError, Exception):
    GPIO_AVAILABLE = False
    print("[WARN] gpiozero not available. Running DoorController in simulated hardware mode.")

class DoorController:
    def __init__(self):
        self.in1_pin = MOTOR_IN1
        self.in2_pin = MOTOR_IN2
        self.sensor_pin = DOOR_SENSOR_PIN

        self.motor_in1 = None
        self.motor_in2 = None
        self.sensor = None
        self._lock = threading.Lock()

        self.is_moving = False
        self.current_direction = "IDLE" # "CLOSING", "OPENING", "IDLE"
        self.last_action_status = "idle"
        self.invert_motor_polarity = False
        self.invert_sensor_logic = False
        self._simulated_door_closed = True

        self._init_gpio()

    def _init_gpio(self):
        """Initializes or reinitializes GPIO devices."""
        if not GPIO_AVAILABLE:
            return

        try:
            self.motor_in1 = OutputDevice(self.in1_pin, initial_value=False)
            self.motor_in2 = OutputDevice(self.in2_pin, initial_value=False)
            self.sensor = Button(self.sensor_pin, pull_up=True)
            print(f"[DOOR] GPIO Initialized: Motor Pins({self.in1_pin}, {self.in2_pin}), Reed Sensor({self.sensor_pin})")
        except Exception as e:
            print(f"[DOOR ERROR] Failed to initialize GPIO: {e}")

    @property
    def is_closed(self) -> bool:
        """Returns True if the magnetic reed switch confirms door is closed."""
        if self.sensor and GPIO_AVAILABLE:
            try:
                # With pull_up=True, magnet near switch pulls pin to GND -> is_pressed = True
                pressed = self.sensor.is_pressed
                return not pressed if self.invert_sensor_logic else pressed
            except Exception:
                return False
        return self._simulated_door_closed

    def stop_motor(self):
        """Immediately de-energizes both H-bridge motor control lines."""
        if self.motor_in1 and self.motor_in2:
            self.motor_in1.off()
            self.motor_in2.off()
        self.is_moving = False
        self.current_direction = "IDLE"
        print("[DOOR] Motor STOPPED.")

    def close_door_async(self, callback: Optional[Callable] = None) -> bool:
        """Launches threaded non-blocking door closing sequence."""
        with self._lock:
            if self.is_moving:
                print(f"[DOOR WARN] Operation already in progress ({self.current_direction}).")
                return False
            self.is_moving = True
            self.current_direction = "CLOSING"

        thread = threading.Thread(target=self._close_worker, args=(callback,), daemon=True)
        thread.start()
        return True

    def _close_worker(self, callback: Optional[Callable]):
        print("\n==============================================")
        print(" [DOOR MOTOR] INITIATING CLOSING SEQUENCE")
        print("==============================================")
        self.last_action_status = "closing"
        start_time = time.time()

        # Energize H-bridge in close direction
        pin_a_val = not self.invert_motor_polarity
        pin_b_val = self.invert_motor_polarity

        if self.motor_in1 and self.motor_in2:
            if pin_a_val: self.motor_in1.on()
            else: self.motor_in1.off()

            if pin_b_val: self.motor_in2.on()
            else: self.motor_in2.off()

        door_closed_confirmed = False
        # Small grace period (0.5s) to let motor pull away if it was on edge
        time.sleep(0.5)

        while (time.time() - start_time) < MAX_DOOR_CLOSE_TIME:
            if self.is_closed:
                door_closed_confirmed = True
                break
            time.sleep(DOOR_CHECK_INTERVAL)

        # De-energize motor immediately
        self.stop_motor()
        elapsed = round(time.time() - start_time, 2)

        if door_closed_confirmed:
            self.last_action_status = f"closed_confirmed ({elapsed}s)"
            self._simulated_door_closed = True
            print(f"[DOOR SUCCESS] Door closed and confirmed by reed sensor in {elapsed}s.")
        else:
            self.last_action_status = f"timeout_safety_stop ({elapsed}s)"
            print(f"[DOOR WARN] Safety timeout ({MAX_DOOR_CLOSE_TIME}s) reached. Motor halted.")

        if callback:
            try:
                callback(door_closed_confirmed)
            except Exception:
                pass

    def open_door_async(self, duration: float = 4.0, callback: Optional[Callable] = None) -> bool:
        """Launches threaded non-blocking door opening sequence for given duration."""
        with self._lock:
            if self.is_moving:
                print(f"[DOOR WARN] Operation already in progress ({self.current_direction}).")
                return False
            self.is_moving = True
            self.current_direction = "OPENING"

        thread = threading.Thread(target=self._open_worker, args=(duration, callback), daemon=True)
        thread.start()
        return True

    def _open_worker(self, duration: float, callback: Optional[Callable]):
        print("\n==============================================")
        print(f" [DOOR MOTOR] INITIATING OPENING SEQUENCE ({duration}s)")
        print("==============================================")
        self.last_action_status = "opening"
        start_time = time.time()

        # Energize H-bridge in open direction (reverse of close)
        pin_a_val = self.invert_motor_polarity
        pin_b_val = not self.invert_motor_polarity

        if self.motor_in1 and self.motor_in2:
            if pin_a_val: self.motor_in1.on()
            else: self.motor_in1.off()

            if pin_b_val: self.motor_in2.on()
            else: self.motor_in2.off()

        # Run motor for requested opening duration
        time.sleep(max(0.5, duration))

        self.stop_motor()
        self._simulated_door_closed = False
        elapsed = round(time.time() - start_time, 2)
        self.last_action_status = f"opened ({elapsed}s)"
        print(f"[DOOR SUCCESS] Door opening pulse completed in {elapsed}s.")

        if callback:
            try:
                callback(True)
            except Exception:
                pass

    def toggle_polarity(self) -> bool:
        """Flips H-Bridge directional polarity in case wiring is reversed."""
        self.invert_motor_polarity = not self.invert_motor_polarity
        print(f"[DOOR] Motor Polarity Inverted: {self.invert_motor_polarity}")
        return self.invert_motor_polarity

    def get_status_dict(self) -> dict:
        return {
            "closed": self.is_closed,
            "is_moving": self.is_moving,
            "direction": self.current_direction,
            "status": self.last_action_status,
            "pins": {"in1": self.in1_pin, "in2": self.in2_pin, "sensor": self.sensor_pin},
            "polarity_inverted": self.invert_motor_polarity
        }

# Global singleton
door = DoorController()
