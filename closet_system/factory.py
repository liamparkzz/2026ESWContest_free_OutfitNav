from __future__ import annotations

from .database import ClosetDatabase
from .hardware.door import DoorStateDebouncer
from .hardware.hall import HallDebounceConfig, HallStateDebouncer
from .hardware.mock import MockDoorSensor, MockHallArray, MockR200Scanner, MockSolenoidController
from .services.controller import SmartClosetController
from .services.session_engine import ClosetSessionEngine, SessionConfig


def build_controller(cfg: dict, mock: bool = False) -> SmartClosetController:
    slot_count = int(cfg.get("slot_count", 8))
    if slot_count != 8:
        raise ValueError("현재 실제 옷봉 구성은 Hall 8개 + Solenoid 8개이므로 slot_count는 8이어야 합니다.")

    db = ClosetDatabase(cfg.get("database", {}).get("path", "smart_closet.db"))
    hcfg = cfg.get("hall", {}) or {}
    dcfg = cfg.get("door", {}) or {}
    rcfg = cfg.get("rfid", {}) or {}
    scfg = cfg.get("solenoid", {}) or {}
    sess_cfg = cfg.get("session", {}) or {}

    if mock:
        hall = MockHallArray(slot_count)
        door = MockDoorSensor()
        rfid = MockR200Scanner()
        solenoid = MockSolenoidController(slot_count)
        door_enabled = True
    else:
        from .hardware.hall import MuxAdsHallArray
        from .hardware.rfid_r200 import R200Scanner
        from .hardware.solenoid_arduino import ArduinoSolenoidController
        from .hardware.door import GpioDoorSensor

        hall = MuxAdsHallArray(
            select_pins=[int(x) for x in hcfg.get("select_pins", [5, 6, 13, 19])],
            adc_channel=int(hcfg.get("adc_channel", 0)),
            slot_count=slot_count,
            settle_s=float(hcfg.get("settle_s", 0.001)),
            gain=int(hcfg.get("gain", 1)),
        )
        rfid = R200Scanner(
            port=str(rcfg.get("port", "/dev/ttyUSB0")),
            baudrate=int(rcfg.get("baudrate", 115200)),
            power_dbm=int(rcfg.get("power_dbm", 26)),
            inter_scan_delay_s=float(rcfg.get("inter_scan_delay_s", 0.05)),
            debug_detect=bool(rcfg.get("debug_detect", False)),
        )
        solenoid = ArduinoSolenoidController(
            port=str(scfg.get("port", "auto")),
            baudrate=int(scfg.get("baudrate", 115200)),
            timeout=float(scfg.get("timeout_s", 3.0)),
            slot_count=slot_count,
        )
        door_enabled = bool(dcfg.get("enabled", True))
        door = None
        if door_enabled:
            if dcfg.get("gpio") is None:
                raise ValueError("door.enabled=true인데 door.gpio가 설정되지 않았습니다.")
            door = GpioDoorSensor(
                gpio=int(dcfg["gpio"]),
                pull_up=bool(dcfg.get("pull_up", True)),
                open_when_active=bool(dcfg.get("open_when_active", True)),
            )

    hall_debouncer = HallStateDebouncer(
        slot_count=slot_count,
        cfg=HallDebounceConfig(
            occupied_on_v=float(hcfg.get("occupied_on_v", 2.15)),
            empty_off_v=float(hcfg.get("empty_off_v", 2.05)),
            debounce_s=float(hcfg.get("debounce_s", 0.05)),
            occupied_low=bool(hcfg.get("occupied_low", False)),
        ),
    )

    controller = SmartClosetController(db=db, hall=hall, rfid=rfid, solenoid=solenoid, cfg=cfg)

    if door_enabled and door is not None:
        session_engine = ClosetSessionEngine(
            db=db,
            hall=hall,
            hall_debouncer=hall_debouncer,
            door=door,
            door_debouncer=DoorStateDebouncer(float(dcfg.get("debounce_s", 0.03))),
            rfid=rfid,
            solenoid=solenoid,
            cfg=SessionConfig(
                poll_interval_s=float(sess_cfg.get("poll_interval_s", 0.02)),
                analysis_scans=int(rcfg.get("analysis_scans", 20)),
                analysis_poll_timeout_s=float(rcfg.get("analysis_poll_timeout_s", 0.13)),
                analysis_poll_delay_s=float(rcfg.get("analysis_poll_delay_s", 0.02)),
                analysis_max_tags=int(rcfg.get("analysis_max_tags", 10)),
                analysis_top_n=int(rcfg.get("analysis_top_n", 3)),
                analysis_fail_rate_pct=float(rcfg.get("analysis_fail_rate_pct", 15.0)),
                retry_delay_s=float(sess_cfg.get("retry_delay_s", 0.05)),
            ),
            get_reserved_registration_rfid=controller.get_reserved_registration_rfid,
            on_slot_inserted=controller.on_slot_inserted,
            is_registration_active=controller.is_automatic_registration_active,
            on_slot_removed=controller.on_slot_removed,
        )
        controller.session_engine = session_engine

    return controller
