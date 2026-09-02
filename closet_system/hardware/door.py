from __future__ import annotations

import time


class GpioDoorSensor:
    """Polling-only GPIO door sensor.

    This deliberately avoids gpiozero edge detection/lgpio so it also works on the
    Raspberry Pi setup where edge registration previously failed. With pull_up=True,
    a common 2-wire dry-contact wiring is GPIO -> sensor -> GND. In that wiring,
    open_when_active=True means HIGH=open, LOW=closed.
    """

    def __init__(self, gpio: int, pull_up: bool = True, open_when_active: bool = True):
        try:
            import RPi.GPIO as GPIO
        except ImportError as e:
            raise RuntimeError("RPi.GPIO가 필요합니다.") from e

        self.GPIO = GPIO
        self.gpio = int(gpio)
        self.open_when_active = bool(open_when_active)

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        pud = GPIO.PUD_UP if pull_up else GPIO.PUD_DOWN
        GPIO.setup(self.gpio, GPIO.IN, pull_up_down=pud)

    def is_open(self) -> bool:
        high = self.GPIO.input(self.gpio) == self.GPIO.HIGH
        return high if self.open_when_active else not high

    def close(self) -> None:
        try:
            self.GPIO.cleanup(self.gpio)
        except Exception:
            pass


class DoorStateDebouncer:
    def __init__(self, debounce_s: float = 0.03):
        self.debounce_s = float(debounce_s)
        self.stable: bool | None = None
        self.candidate: bool | None = None
        self.candidate_since: float | None = None

    def initialize(self, raw_open: bool, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        self.stable = bool(raw_open)
        self.candidate = bool(raw_open)
        self.candidate_since = now
        return bool(raw_open)

    def update(self, raw_open: bool, now: float | None = None) -> tuple[bool, bool] | None:
        if now is None:
            now = time.monotonic()
        raw_open = bool(raw_open)

        if self.stable is None:
            self.initialize(raw_open, now)
            return None

        if raw_open == self.stable:
            self.candidate = raw_open
            self.candidate_since = now
            return None

        if self.candidate != raw_open:
            self.candidate = raw_open
            self.candidate_since = now
            return None

        since = self.candidate_since if self.candidate_since is not None else now
        if now - since >= self.debounce_s:
            old = bool(self.stable)
            self.stable = raw_open
            self.candidate = raw_open
            self.candidate_since = now
            return old, raw_open

        return None
