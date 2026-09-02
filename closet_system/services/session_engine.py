from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..database import ClosetDatabase, normalize_uid
from ..hardware.door import DoorStateDebouncer
from ..hardware.hall import HallStateDebouncer


@dataclass
class SessionConfig:
    poll_interval_s: float = 0.02
    analysis_scans: int = 20
    analysis_poll_timeout_s: float = 0.13
    analysis_poll_delay_s: float = 0.02
    analysis_max_tags: int = 10
    analysis_top_n: int = 3
    analysis_fail_rate_pct: float = 15.0
    retry_delay_s: float = 0.05


class ClosetSessionEngine:
    """Door + Hall + RFID placement/removal engine.

    Final behavior:
    - Door OPEN starts a physical interaction session.
    - Hall EMPTY->OCCUPIED while door is open starts RFID matching.
    - Each matching attempt runs the validated 20-scan (~3 s) RFID analysis.
      Up to 10 tags are ranked and the highest-recognition eligible registered/
      unlocated RFID at >=15% is assigned to that slot.
    - TOP3 plus NEW / <15% recognition-failure changes are printed every round.
    - If no eligible RFID is found, matching repeats until one is found. The wait is
      cancelled if that Hall slot becomes EMPTY.
    - Hall OCCUPIED->EMPTY while door is open immediately clears only the slot
      location and reports the persistent clothing number (DB item id).
    - Door CLOSE ends the interaction session. A placement matcher already started
      while open may continue after close as long as the Hall remains OCCUPIED.
    """

    def __init__(
        self,
        db: ClosetDatabase,
        hall,
        hall_debouncer: HallStateDebouncer,
        door,
        door_debouncer: DoorStateDebouncer,
        rfid,
        solenoid,
        cfg: SessionConfig,
        get_reserved_registration_rfid,
        on_slot_inserted=None,
        is_registration_active=None,
        on_slot_removed=None,
    ):
        self.db = db
        self.hall = hall
        self.hall_debouncer = hall_debouncer
        self.door = door
        self.door_debouncer = door_debouncer
        self.rfid = rfid
        self.solenoid = solenoid
        self.cfg = cfg
        self.get_reserved_registration_rfid = get_reserved_registration_rfid
        self.on_slot_inserted = on_slot_inserted
        self.is_registration_active = is_registration_active
        self.on_slot_removed = on_slot_removed

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._pending_lock = threading.RLock()
        self._pending_slots: set[int] = set()

        self.session_open = False
        self.session_started_at: float | None = None
        self.last_status = "INIT"
        self.last_voltages: list[float] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._initialize()
        self._thread = threading.Thread(target=self._run, name="closet-session-engine", daemon=True)
        self._thread.start()
        print("[SESSION] door/hall monitoring started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        try:
            self.door.close()
        except Exception:
            pass

    def _initialize(self) -> None:
        voltages = self.hall.read_all(samples_per_slot=1)
        self.last_voltages = voltages
        states = self.hall_debouncer.initialize(voltages)
        raw_open = bool(self.door.is_open())
        self.door_debouncer.initialize(raw_open)
        self.session_open = raw_open
        self.session_started_at = time.monotonic() if raw_open else None
        self.last_status = "OPEN" if raw_open else "CLOSED"

        # Reconcile stale database locations on every boot. A DB slot alone must
        # never make the UI say that clothing is inside when its Hall sensor is
        # physically EMPTY.
        for slot, occupied in enumerate(states, start=1):
            item = self.db.get_by_slot(slot)
            if item is not None and not occupied:
                self.db.clear_slot(slot)
                self.db.log_event(
                    "STARTUP_STALE_SLOT_CLEARED",
                    slot=slot,
                    rfid_uid=item.rfid_uid,
                    detail={"clothing_id": item.id, "reason": "HALL_EMPTY_AT_STARTUP"},
                )
                print(f"[RECONCILE] slot {slot} Hall EMPTY -> 옷 {item.id}의 오래된 위치 정보 삭제")

        print(f"[SESSION] initial door={'OPEN' if raw_open else 'CLOSED'} / hall={states}")

        # If a garment was already hanging when the server started, there is no
        # EMPTY->OCCUPIED edge to trigger matching. Start matching for occupied,
        # unassigned slots so the system can recover actual locations.
        self.request_match_for_occupied_slots()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = time.monotonic()

                raw_open = bool(self.door.is_open())
                door_event = self.door_debouncer.update(raw_open, now)
                if door_event:
                    was_open, is_open = door_event
                    if not was_open and is_open:
                        self._begin_session(now)
                    elif was_open and not is_open:
                        self._end_session(now)

                voltages = self.hall.read_all(samples_per_slot=1)
                self.last_voltages = voltages
                hall_events = self.hall_debouncer.update(voltages, now)

                if self.session_open:
                    for slot, before, after, voltage in hall_events:
                        self.db.log_event(
                            "HALL_CHANGE_OPEN",
                            slot=slot,
                            detail={"before": before, "after": after, "voltage": voltage},
                        )
                        if (not before) and after:
                            self._on_insert(slot, now, voltage)
                        elif before and (not after):
                            self._on_remove(slot, voltage)

                time.sleep(self.cfg.poll_interval_s)
            except Exception as e:
                self.last_status = f"ERROR: {e}"
                print(f"[SESSION] ERROR: {e}")
                time.sleep(0.25)

    def _begin_session(self, now: float) -> None:
        with self._lock:
            self.session_open = True
            self.session_started_at = now
            self.last_status = "OPEN"
        self.db.log_event("DOOR_OPEN", detail={"hall": self.hall_debouncer.snapshot()})
        print("[DOOR] OPEN")

    def _end_session(self, now: float) -> None:
        with self._lock:
            self.session_open = False
            self.session_started_at = None
            self.last_status = "CLOSED"
        self.db.log_event("DOOR_CLOSE", detail={"hall": self.hall_debouncer.snapshot()})
        print("[DOOR] CLOSED")

    def _on_insert(self, slot: int, now: float, voltage: float) -> None:
        print(f"[HALL] slot {slot} OCCUPIED ({voltage:.3f} V)")
        if self.db.get_by_slot(slot) is not None:
            print(f"[MATCH] slot {slot}에는 이미 위치 정보가 있어 새 매칭을 시작하지 않습니다.")
            return

        if self.on_slot_inserted:
            try:
                if self.on_slot_inserted(slot, now, voltage):
                    print(f"[REGISTER] slot {slot} 신규 Hall 입력을 자동 등록 흐름이 처리합니다.")
                    return
            except Exception as e:
                self.db.log_event(
                    "AUTO_REGISTER_HALL_CALLBACK_ERROR",
                    slot=slot,
                    detail={"error": str(e)},
                )
                print(f"[REGISTER] slot {slot} 자동 등록 시작 오류: {e}")
                return

        with self._pending_lock:
            if slot in self._pending_slots:
                return
            self._pending_slots.add(slot)

        self.db.log_event("PLACEMENT_PENDING", slot=slot, detail={"hall_event_time": now, "voltage": voltage})
        threading.Thread(
            target=self._match_until_found,
            args=(slot,),
            name=f"rfid-match-slot-{slot}",
            daemon=True,
        ).start()

    def request_match_for_occupied_slots(self) -> None:
        """Start RFID matching for Hall-occupied slots that have no DB location.

        Used at startup and immediately after a new RFID registration, because
        in both cases the hanger may already be pressing the Hall sensor and no
        new EMPTY->OCCUPIED edge will occur.
        """
        states = self.hall_debouncer.snapshot()
        voltages = list(self.last_voltages)
        now = time.monotonic()
        for slot, occupied in enumerate(states, start=1):
            if not occupied or self.db.get_by_slot(slot) is not None:
                continue
            voltage = float(voltages[slot - 1]) if slot - 1 < len(voltages) else 0.0
            self._on_insert(slot, now, voltage)

    def _slot_is_occupied(self, slot: int) -> bool:
        states = self.hall_debouncer.snapshot()
        return 1 <= slot <= len(states) and bool(states[slot - 1])

    def slot_is_occupied(self, slot: int) -> bool:
        """Thread-safe public snapshot used by the registration controller."""
        return self._slot_is_occupied(slot)

    def _match_until_found(self, slot: int) -> None:
        attempt = 1
        previous_counts: dict[str, int] | None = None
        try:
            while not self._stop.is_set():
                if not self._slot_is_occupied(slot):
                    print(f"[MATCH] slot {slot}: Hall이 EMPTY가 되어 RFID 대기를 취소합니다.")
                    self.db.log_event("PLACEMENT_CANCELLED", slot=slot, detail={"reason": "HALL_EMPTY"})
                    return

                if self.db.get_by_slot(slot) is not None:
                    return

                # Give an armed/new automatic registration exclusive priority on
                # the serial RFID reader. Existing unassigned-slot recovery can
                # resume as soon as registration completes, fails, or is cancelled.
                if self.is_registration_active and self.is_registration_active():
                    time.sleep(max(0.05, self.cfg.retry_delay_s))
                    continue

                eligible = self.db.unlocated_registered_rfids()
                reserved = normalize_uid(self.get_reserved_registration_rfid() or "")
                if reserved:
                    eligible.discard(reserved)

                print(
                    f"[MATCH] slot {slot} attempt #{attempt}: "
                    f"{self.cfg.analysis_scans}회 RFID Scan (~3초)"
                )
                analysis = self.rfid.scan_20_analysis(
                    previous_counts=previous_counts,
                    total_scans=self.cfg.analysis_scans,
                    poll_timeout_s=self.cfg.analysis_poll_timeout_s,
                    poll_delay_s=self.cfg.analysis_poll_delay_s,
                    max_tags=self.cfg.analysis_max_tags,
                    top_n=self.cfg.analysis_top_n,
                    fail_rate_pct=self.cfg.analysis_fail_rate_pct,
                    label=f"Slot {slot} placement attempt #{attempt}",
                )
                counts = dict(analysis.get("counts", {}))
                previous_counts = counts.copy()

                # Only the top-10 analyzed RFID tags participate in slot matching.
                # An eligible registered/unlocated tag must also meet the same 15%
                # recognition threshold used by the standalone test.
                candidates = [
                    item
                    for item in analysis.get("top10", [])
                    if item["rfid"] in eligible
                    and float(item["rate"]) >= self.cfg.analysis_fail_rate_pct
                ]

                if not candidates:
                    print(
                        f"[MATCH] slot {slot}: 인식률 {self.cfg.analysis_fail_rate_pct:.0f}% 이상인 "
                        "미배정 등록 RFID 없음 -> 다음 20 Scan 계속 대기"
                    )
                    attempt += 1
                    time.sleep(self.cfg.retry_delay_s)
                    continue

                best_rate = float(candidates[0]["rate"])
                best = [item for item in candidates if float(item["rate"]) == best_rate]

                if len(best) != 1:
                    print(
                        f"[MATCH] slot {slot}: 최고 인식률 동률 "
                        f"{[item['rfid'] for item in best]} ({best_rate:.1f}%) "
                        "-> 추측하지 않고 다음 20 Scan"
                    )
                    self.db.log_event(
                        "PLACEMENT_RETRY_AMBIGUOUS",
                        slot=slot,
                        detail={
                            "best": best,
                            "top3": analysis.get("top3", []),
                            "scans": analysis.get("scans", self.cfg.analysis_scans),
                        },
                    )
                    attempt += 1
                    time.sleep(self.cfg.retry_delay_s)
                    continue

                selected = best[0]
                tag = selected["rfid"]

                if not self._slot_is_occupied(slot):
                    print(f"[MATCH] slot {slot}: RFID는 찾았지만 Hall이 EMPTY라 저장하지 않습니다.")
                    return
                if self.db.get_by_slot(slot) is not None:
                    return
                if tag not in self.db.unlocated_registered_rfids():
                    attempt += 1
                    continue

                self.db.assign_slot(tag, slot)
                item = self.db.get_by_rfid(tag)
                self.db.log_event(
                    "PLACEMENT_CONFIRMED",
                    slot=slot,
                    rfid_uid=tag,
                    detail={
                        "clothing_id": item.id if item else None,
                        "category": item.category if item else "",
                        "color": item.color if item else "",
                        "season": item.season if item else "",
                        "extra": item.extra if item else "",
                        "slot": slot,
                        "attempt": attempt,
                        "count": selected["count"],
                        "rate": selected["rate"],
                        "top3": analysis.get("top3", []),
                        "new_tags": analysis.get("new_tags", []),
                        "failed_tags": analysis.get("failed_tags", []),
                        "scans": analysis.get("scans", self.cfg.analysis_scans),
                    },
                )
                number = item.id if item else "?"
                print(
                    f"[MATCHED] Slot {slot} 등록 완료: 옷 {number} / RFID={tag} / "
                    f"{selected['count']}/{analysis.get('scans', self.cfg.analysis_scans)} "
                    f"({selected['rate']:.1f}%)"
                )
                return
        except Exception as e:
            self.db.log_event("PLACEMENT_ERROR", slot=slot, detail={"error": str(e)})
            print(f"[MATCH] slot {slot} ERROR: {e}")
        finally:
            with self._pending_lock:
                self._pending_slots.discard(slot)

    def _on_remove(self, slot: int, voltage: float) -> None:
        print(f"[HALL] slot {slot} EMPTY ({voltage:.3f} V)")
        item = self.db.get_by_slot(slot)
        uid = self.db.clear_slot(slot)

        if self.on_slot_removed:
            try:
                self.on_slot_removed(slot)
            except Exception as e:
                print(f"[SESSION] on_slot_removed warning: {e}")

        if item and uid:
            print(f"[REMOVED] 해당 옷은 옷 {item.id}입니다. Slot {slot} 위치 정보를 삭제했습니다.")
            # Include the clothing description in the removal event so every open
            # web screen can announce exactly what was taken out without needing
            # to know which page the user is currently viewing.
            self.db.log_event(
                "REMOVE_CONFIRMED",
                slot=slot,
                rfid_uid=uid,
                detail={
                    "clothing_id": item.id,
                    "category": item.category,
                    "color": item.color,
                    "season": item.season,
                    "extra": item.extra,
                },
            )
        else:
            print(f"[REMOVE] slot {slot}: 저장된 RFID/옷 정보가 없습니다.")

    def status(self) -> dict:
        with self._pending_lock:
            pending = sorted(self._pending_slots)
        with self._lock:
            return {
                "session_open": self.session_open,
                "status": self.last_status,
                "door_open": bool(self.door.is_open()),
                "hall_voltages": [round(v, 4) for v in self.last_voltages],
                "hall_occupied": self.hall_debouncer.snapshot(),
                "pending_slots": pending,
            }
