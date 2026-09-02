from __future__ import annotations

import threading
import time
from serial.tools import list_ports
import serial


ARDUINO_VIDS = {0x2341, 0x2A03}


def find_arduino() -> str:
    ports = list(list_ports.comports())
    for port in ports:
        if port.vid in ARDUINO_VIDS:
            return port.device
    for port in ports:
        if "ttyACM" in port.device:
            return port.device
    # Deliberately do NOT pick an arbitrary ttyUSB device: /dev/ttyUSB0 may be the R200 reader.
    raise RuntimeError(
        "Arduino 포트를 자동으로 찾지 못했습니다. CH340 계열이면 config의 solenoid.port에 /dev/ttyUSBx를 직접 지정하세요."
    )


class ArduinoSolenoidController:
    """Raspberry Pi -> USB Serial -> Arduino -> 8 solenoids.

    Protocol is the user's existing protocol:
      PING
      ON 1
      OFF 1
      PULSE 1 500
      ALL_OFF
      STATUS
    """

    def __init__(self, port: str = "auto", baudrate: int = 115200, timeout: float = 3.0, slot_count: int = 8):
        if port == "auto":
            port = find_arduino()
        self.port = port
        self.slot_count = slot_count
        self._lock = threading.RLock()
        self.serial = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        time.sleep(2.0)
        self.serial.reset_input_buffer()
        response = self.send_command("PING")
        print(f"[SOLENOID] Arduino 연결: {port} / {response}")

    def send_command(self, command: str) -> str:
        with self._lock:
            self.serial.write((command + "\n").encode("ascii"))
            self.serial.flush()
            response = self.serial.readline().decode("ascii", errors="replace").strip()
            if not response:
                raise TimeoutError(f"Arduino 응답 없음: {command}")
            if response.startswith("ERR"):
                raise RuntimeError(response)
            return response

    def _check(self, slot: int) -> None:
        if not 1 <= slot <= self.slot_count:
            raise ValueError(f"솔레노이드 번호는 1~{self.slot_count}이어야 합니다.")

    def on(self, slot: int) -> str:
        self._check(slot)
        return self.send_command(f"ON {slot}")

    def off(self, slot: int) -> str:
        self._check(slot)
        return self.send_command(f"OFF {slot}")

    def pulse(self, slot: int, duration_ms: int = 500) -> str:
        self._check(slot)
        if not 1 <= duration_ms <= 60000:
            raise ValueError("duration_ms는 1~60000이어야 합니다.")
        return self.send_command(f"PULSE {slot} {duration_ms}")

    def all_off(self) -> str:
        return self.send_command("ALL_OFF")

    def status(self) -> str:
        return self.send_command("STATUS")

    def close(self) -> None:
        try:
            self.all_off()
        except Exception:
            pass
        self.serial.close()
