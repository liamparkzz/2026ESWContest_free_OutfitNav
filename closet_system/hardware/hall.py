from __future__ import annotations

import statistics
import time
from dataclasses import dataclass


class MuxAdsHallArray:
    """8 Hall sensors -> CD74HC4067 -> ADS1115 A0.

    Uses RPi.GPIO directly for MUX selection to avoid gpiozero/lgpio edge/backend
    issues. Slot 1..8 maps to MUX C0..C7.
    """

    def __init__(
        self,
        select_pins: list[int],
        adc_channel: int = 0,
        slot_count: int = 8,
        settle_s: float = 0.001,
        gain: int = 1,
    ):
        if len(select_pins) != 4:
            raise ValueError("MUX select pin은 S0~S3 총 4개여야 합니다.")
        if not 1 <= slot_count <= 16:
            raise ValueError("slot_count는 1~16이어야 합니다.")

        try:
            import board
            import busio
            import RPi.GPIO as GPIO
            import adafruit_ads1x15.ads1115 as ADS
            from adafruit_ads1x15.analog_in import AnalogIn
        except ImportError as e:
            raise RuntimeError(
                "Hall HW 라이브러리가 없습니다. RPi.GPIO와 adafruit-circuitpython-ads1x15가 필요합니다."
            ) from e

        self.GPIO = GPIO
        self._ADS = ADS
        self.slot_count = int(slot_count)
        self.settle_s = float(settle_s)
        self.select_pins = [int(p) for p in select_pins]

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        for pin in self.select_pins:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

        i2c = busio.I2C(board.SCL, board.SDA)
        self._ads = ADS.ADS1115(i2c)
        self._ads.gain = gain
        try:
            self._ads.data_rate = 860
        except Exception:
            pass

        ads_pin = getattr(ADS, f"P{adc_channel}", adc_channel)
        self._adc = AnalogIn(self._ads, ads_pin)

    def _select_channel(self, channel: int) -> None:
        for bit, pin in enumerate(self.select_pins):
            self.GPIO.output(pin, self.GPIO.HIGH if channel & (1 << bit) else self.GPIO.LOW)
        if self.settle_s > 0:
            time.sleep(self.settle_s)

    def read_voltage(self, slot: int, samples: int = 1) -> float:
        if not 1 <= slot <= self.slot_count:
            raise ValueError(f"slot은 1~{self.slot_count}이어야 합니다.")
        self._select_channel(slot - 1)
        values = [float(self._adc.voltage) for _ in range(max(1, int(samples)))]
        return statistics.mean(values)

    def read_all(self, samples_per_slot: int = 1) -> list[float]:
        return [self.read_voltage(slot, samples=samples_per_slot) for slot in range(1, self.slot_count + 1)]

    def close(self) -> None:
        try:
            for pin in self.select_pins:
                self.GPIO.output(pin, self.GPIO.LOW)
            self.GPIO.cleanup(self.select_pins)
        except Exception:
            pass


@dataclass
class HallDebounceConfig:
    occupied_on_v: float = 2.15
    empty_off_v: float = 2.05
    debounce_s: float = 0.05
    occupied_low: bool = False


class HallStateDebouncer:
    """Hysteresis + time debounce for each Hall slot."""

    def __init__(self, slot_count: int, cfg: HallDebounceConfig):
        self.slot_count = slot_count
        self.cfg = cfg
        self.states: list[bool | None] = [None] * slot_count
        self.candidates: list[bool | None] = [None] * slot_count
        self.candidate_since: list[float | None] = [None] * slot_count

    def _raw_state(self, voltage: float, previous: bool | None) -> bool:
        if not self.cfg.occupied_low:
            if previous is None:
                midpoint = (self.cfg.occupied_on_v + self.cfg.empty_off_v) / 2.0
                return voltage >= midpoint
            if previous:
                return not (voltage <= self.cfg.empty_off_v)
            return voltage >= self.cfg.occupied_on_v

        # Reverse-polarity Hall: lower voltage means occupied.
        if previous is None:
            midpoint = (self.cfg.occupied_on_v + self.cfg.empty_off_v) / 2.0
            return voltage <= midpoint
        if previous:
            return not (voltage >= self.cfg.occupied_on_v)
        return voltage <= self.cfg.empty_off_v

    def initialize(self, voltages: list[float]) -> list[bool]:
        now = time.monotonic()
        for i, voltage in enumerate(voltages):
            state = self._raw_state(voltage, None)
            self.states[i] = state
            self.candidates[i] = state
            self.candidate_since[i] = now
        return [bool(x) for x in self.states]

    def update(self, voltages: list[float], now: float | None = None) -> list[tuple[int, bool, bool, float]]:
        if now is None:
            now = time.monotonic()
        if self.states[0] is None:
            self.initialize(voltages)
            return []

        events: list[tuple[int, bool, bool, float]] = []
        for i, voltage in enumerate(voltages):
            previous = bool(self.states[i])
            candidate = self._raw_state(voltage, previous)
            if candidate == previous:
                self.candidates[i] = previous
                self.candidate_since[i] = now
                continue

            if self.candidates[i] != candidate:
                self.candidates[i] = candidate
                self.candidate_since[i] = now
                continue

            since = self.candidate_since[i] if self.candidate_since[i] is not None else now
            if now - since >= self.cfg.debounce_s:
                self.states[i] = candidate
                self.candidates[i] = candidate
                self.candidate_since[i] = now
                events.append((i + 1, previous, candidate, voltage))

        return events

    def snapshot(self) -> list[bool]:
        return [bool(x) for x in self.states]
