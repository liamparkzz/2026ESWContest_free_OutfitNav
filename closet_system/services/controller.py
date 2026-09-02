from __future__ import annotations

import re
import threading
import time
import uuid

from ..database import ClosetDatabase, normalize_uid
from .codi import WeatherManager, recommend_outfits


class SmartClosetController:
    def __init__(self, db: ClosetDatabase, hall, rfid, solenoid, session_engine=None, cfg: dict | None = None):
        self.db = db
        self.hall = hall
        self.rfid = rfid
        self.solenoid = solenoid
        self.session_engine = session_engine
        self.cfg = cfg or {}
        self._lock = threading.RLock()
        self._reserved_registration_rfid: str | None = None
        self._reservation_expires_at = 0.0
        self._active_find_slot: int | None = None
        self._active_find_rfid: str | None = None
        self._active_outfit_slots: list[int] = []
        self._active_outfit_rfids: list[str] = []
        self.weather_manager = WeatherManager((self.cfg.get("codi", {}) or {}))
        self._presence_lock = threading.RLock()
        self._presence_cache: dict | None = None
        self._presence_cache_at = 0.0
        self._registration: dict = {
            "token": None,
            "status": "IDLE",
            "message": "신규 옷 등록을 시작하지 않았습니다.",
            "slot": None,
            "item": None,
            "selected": None,
            "started_at": None,
            "expires_at": None,
        }

    def start(self) -> None:
        if self.session_engine:
            self.session_engine.start()

    def stop(self) -> None:
        if self.session_engine:
            self.session_engine.stop()
        try:
            self.solenoid.all_off()
        except Exception:
            pass
        # Door is owned by session engine/factory; hardware close methods are idempotent.
        for hw in [self.hall, self.rfid, self.solenoid]:
            try:
                hw.close()
            except Exception:
                pass

    def get_reserved_registration_rfid(self) -> str | None:
        with self._lock:
            if self._reserved_registration_rfid and time.monotonic() > self._reservation_expires_at:
                self._reserved_registration_rfid = None
            return self._reserved_registration_rfid

    def _scan_best_unregistered_rfid(self, label: str) -> dict:
        """Find one unregistered RFID using the validated 20-scan method.

        The low-level RFID recognition code is not changed here.  This only
        analyzes 20 calls to the existing M100 ``single_poll()`` and chooses
        the highest-recognition unregistered tag that meets the 15% threshold.
        """
        rcfg = self.cfg.get("rfid", {})
        analysis = self.rfid.scan_20_analysis(
            previous_counts=None,
            total_scans=int(rcfg.get("analysis_scans", 20)),
            poll_timeout_s=float(rcfg.get("analysis_poll_timeout_s", 0.13)),
            poll_delay_s=float(rcfg.get("analysis_poll_delay_s", 0.02)),
            max_tags=int(rcfg.get("analysis_max_tags", 10)),
            top_n=int(rcfg.get("analysis_top_n", 3)),
            fail_rate_pct=float(rcfg.get("analysis_fail_rate_pct", 15.0)),
            label=label,
        )

        registered = self.db.registered_rfids()
        fail_rate = float(rcfg.get("analysis_fail_rate_pct", 15.0))
        candidates = [
            item
            for item in analysis.get("top10", [])
            if normalize_uid(item["rfid"]) not in registered
            and float(item["rate"]) >= fail_rate
        ]

        if not candidates:
            raise RuntimeError(
                f"20회 Scan에서 인식률 {fail_rate:.0f}% 이상인 미등록 RFID가 없습니다. "
                "태그를 다시 가까이 대고 재시도하세요."
            )

        best_rate = float(candidates[0]["rate"])
        best = [item for item in candidates if float(item["rate"]) == best_rate]
        if len(best) != 1:
            raise RuntimeError(
                "최고 인식률의 미등록 RFID가 여러 개라 자동 선택하지 않았습니다: "
                + ", ".join(item["rfid"] for item in best)
            )

        return {
            "rfid": normalize_uid(best[0]["rfid"]),
            "selected": best[0],
            "top3": analysis.get("top3", []),
            "top10": analysis.get("top10", []),
            "counts": analysis.get("counts", {}),
            "scans": analysis.get("scans", 20),
        }

    def scan_unregistered_rfid(self) -> dict:
        """Legacy manual scan endpoint retained for backward compatibility."""
        result = self._scan_best_unregistered_rfid("웹 수동 신규 RFID 등록")
        uid = result["rfid"]
        with self._lock:
            self._reserved_registration_rfid = uid
            self._reservation_expires_at = time.monotonic() + float(
                (self.cfg.get("rfid", {}) or {}).get("registration_reservation_s", 90.0)
            )
        self.db.log_event(
            "RFID_REGISTER_CANDIDATE",
            rfid_uid=uid,
            detail={
                "selected": result["selected"],
                "top3": result["top3"],
                "top10": result["top10"],
                "scans": result["scans"],
            },
        )
        return result

    def _expire_registration_locked(self) -> None:
        expires_at = self._registration.get("expires_at")
        if (
            self._registration.get("status") == "WAITING_HALL"
            and expires_at is not None
            and time.monotonic() >= float(expires_at)
        ):
            self._registration.update(
                status="FAILED",
                message="등록 대기 시간이 만료되었습니다. 다시 시작한 뒤 옷걸이를 걸어주세요.",
            )

    def registration_status(self) -> dict:
        with self._lock:
            self._expire_registration_locked()
            registration = dict(self._registration)
            registration.pop("token", None)
            expires_at = registration.pop("expires_at", None)
            registration["remaining_s"] = (
                max(0, round(float(expires_at) - time.monotonic(), 1))
                if expires_at is not None and registration.get("status") == "WAITING_HALL"
                else None
            )
            if isinstance(registration.get("item"), dict):
                registration["item"] = dict(registration["item"])
            return registration

    def start_automatic_registration(
        self,
        category: str,
        color: str,
        season: str,
        extra: str = "",
        position: str = "",
        color_hex: str = "",
        pattern: str = "",
    ) -> dict:
        """Arm registration; the next EMPTY->OCCUPIED Hall edge starts RFID."""
        if self.session_engine is None:
            raise RuntimeError("자동 등록에는 Hall/문 센서 세션이 필요합니다.")

        with self._lock:
            self._expire_registration_locked()
            if self._registration.get("status") in {"WAITING_HALL", "SCANNING"}:
                raise RuntimeError("이미 신규 옷 등록을 기다리거나 RFID를 스캔하고 있습니다.")

            wait_s = float(
                (self.cfg.get("rfid", {}) or {}).get("registration_wait_timeout_s", 120.0)
            )
            token = uuid.uuid4().hex
            now = time.monotonic()
            self._registration = {
                "token": token,
                "status": "WAITING_HALL",
                "message": "준비되었습니다. 문을 열고 빈 위치에 옷걸이를 걸어주세요.",
                "slot": None,
                "item": None,
                "selected": None,
                "started_at": now,
                "expires_at": now + max(10.0, wait_s),
                "category": category.strip(),
                "color": color.strip(),
                "season": season.strip(),
                "extra": extra.strip(),
                "position": position.strip(),
                "color_hex": color_hex.strip().upper(),
                "pattern": pattern.strip(),
            }
            self._reserved_registration_rfid = None
            self._reservation_expires_at = 0.0

        self.db.log_event(
            "AUTO_REGISTER_ARMED",
            detail={
                "category": category,
                "color": color,
                "season": season,
                "position": position,
                "color_hex": color_hex,
                "pattern": pattern,
            },
        )
        return self.registration_status()

    def cancel_automatic_registration(self) -> dict:
        with self._lock:
            status = self._registration.get("status")
            if status in {"WAITING_HALL", "SCANNING"}:
                self._registration.update(
                    status="CANCELLED",
                    message="신규 옷 등록을 취소했습니다.",
                    expires_at=None,
                )
                slot = self._registration.get("slot")
            else:
                slot = self._registration.get("slot")
        if status in {"WAITING_HALL", "SCANNING"}:
            self.db.log_event("AUTO_REGISTER_CANCELLED", slot=slot)
        return self.registration_status()

    def is_automatic_registration_active(self) -> bool:
        with self._lock:
            self._expire_registration_locked()
            return self._registration.get("status") in {"WAITING_HALL", "SCANNING"}

    def on_slot_inserted(self, slot: int, timestamp: float, voltage: float) -> bool:
        """Claim a new Hall edge when the UI has armed automatic registration."""
        with self._lock:
            self._expire_registration_locked()
            if self._registration.get("status") != "WAITING_HALL":
                return False
            token = str(self._registration["token"])
            self._registration.update(
                status="SCANNING",
                message=f"{slot}번 위치를 감지했습니다. RFID를 20회 스캔하고 있습니다.",
                slot=int(slot),
                expires_at=None,
            )

        self.db.log_event(
            "AUTO_REGISTER_HALL_DETECTED",
            slot=slot,
            detail={"hall_event_time": timestamp, "voltage": voltage},
        )
        threading.Thread(
            target=self._finish_automatic_registration,
            args=(token, int(slot)),
            name=f"auto-register-slot-{slot}",
            daemon=True,
        ).start()
        return True

    def _finish_automatic_registration(self, token: str, slot: int) -> None:
        try:
            scan = self._scan_best_unregistered_rfid(f"신규 옷 등록 Slot {slot}")
            with self._lock:
                if (
                    self._registration.get("token") != token
                    or self._registration.get("status") != "SCANNING"
                ):
                    return
                metadata = {
                    "category": self._registration.get("category", ""),
                    "color": self._registration.get("color", ""),
                    "season": self._registration.get("season", ""),
                    "extra": self._registration.get("extra", ""),
                    "position": self._registration.get("position", ""),
                    "color_hex": self._registration.get("color_hex", ""),
                    "pattern": self._registration.get("pattern", ""),
                }

            with self._lock:
                if (
                    self._registration.get("token") != token
                    or self._registration.get("status") != "SCANNING"
                ):
                    return
                if not self.session_engine.slot_is_occupied(slot):
                    raise RuntimeError("RFID 스캔 중 옷걸이가 빠졌습니다. 다시 등록해주세요.")

                # Keep cancellation/removal and the DB insert serialized. This
                # prevents a cancelled 20-scan from creating a hidden item.
                item = self.db.register_item(scan["rfid"], slot=slot, **metadata)
                item_payload = self._item_payload(item, include_rfid=True)
                item_payload["inside"] = True
                self._invalidate_presence_cache()
                self.db.log_event(
                    "PLACEMENT_CONFIRMED",
                    slot=slot,
                    rfid_uid=item.rfid_uid,
                    detail={
                        "clothing_id": item.id,
                        "category": item.category,
                        "color": item.color,
                        "season": item.season,
                        "extra": item.extra,
                        "slot": slot,
                        "count": scan["selected"]["count"],
                        "rate": scan["selected"]["rate"],
                        "top3": scan["top3"],
                        "scans": scan["scans"],
                        "automatic_registration": True,
                    },
                )
                # COMPLETED is published last, after every DB/event write. API
                # clients can therefore treat it as a durable completion point.
                self._registration.update(
                    status="COMPLETED",
                    message=f"옷 {item.id}로 등록하고 {slot}번 위치에 배정했습니다.",
                    item=item_payload,
                    selected=dict(scan["selected"]),
                    expires_at=None,
                )
        except Exception as e:
            with self._lock:
                if self._registration.get("token") == token:
                    self._registration.update(
                        status="FAILED",
                        message=str(e),
                        expires_at=None,
                    )
            self.db.log_event(
                "AUTO_REGISTER_FAILED",
                slot=slot,
                detail={"error": str(e)},
            )

    def register_item(
        self,
        rfid: str,
        category: str,
        color: str,
        season: str,
        extra: str = "",
        position: str = "",
        color_hex: str = "",
        pattern: str = "",
    ) -> dict:
        uid = normalize_uid(rfid)
        reserved = self.get_reserved_registration_rfid()
        if not reserved or uid != reserved:
            raise RuntimeError("RFID 등록 후보가 만료되었거나 현재 스캔된 RFID와 다릅니다. RFID 인식을 다시 수행하세요.")
        item = self.db.register_item(
            uid,
            category,
            color,
            season,
            extra,
            position=position,
            color_hex=color_hex,
            pattern=pattern,
        )
        with self._lock:
            self._reserved_registration_rfid = None
            self._reservation_expires_at = 0.0
        # If the user registered the RFID while the garment was already hanging,
        # there may be no new EMPTY->OCCUPIED Hall edge after registration. Ask
        # the session engine to start matching every occupied-but-unassigned slot
        # so the first registration can still announce its physical location.
        if self.session_engine:
            try:
                self.session_engine.request_match_for_occupied_slots()
            except Exception as e:
                print(f"[REGISTER] occupied-slot rematch warning: {e}")
        self._invalidate_presence_cache()
        return {
            "ok": True,
            "item": {
                "id": item.id,
                "rfid": item.rfid_uid,
                "category": item.category,
                "color": item.color,
                "season": item.season,
                "extra": item.extra,
                "slot": item.slot,
                "position": item.position,
                "color_hex": item.color_hex,
                "pattern": item.pattern,
            },
            "message": f"옷 {item.id}로 등록되었습니다.",
        }

    def _invalidate_presence_cache(self) -> None:
        with self._presence_lock:
            self._presence_cache = None
            self._presence_cache_at = 0.0

    def _current_hall_states(self) -> list[bool]:
        if self.session_engine:
            return [bool(x) for x in self.session_engine.hall_debouncer.snapshot()]

        # Fallback for configurations without a door/session engine.
        hcfg = self.cfg.get("hall", {}) or {}
        occupied_on = float(hcfg.get("occupied_on_v", 2.15))
        empty_off = float(hcfg.get("empty_off_v", 2.05))
        occupied_low = bool(hcfg.get("occupied_low", False))
        voltages = self.hall.read_all(samples_per_slot=1)
        states = []
        for voltage in voltages:
            if occupied_low:
                states.append(float(voltage) <= empty_off)
            else:
                states.append(float(voltage) >= occupied_on)
        return states

    def verify_closet_state(self, max_age_s: float = 1.0, force: bool = False) -> dict:
        """Verify physical closet presence by combining Hall and RFID.

        Hall is authoritative for whether a numbered hanger slot is physically
        occupied.  A stored DB slot is never enough by itself.  For occupied
        Hall slots, the exact validated 20-scan RFID routine is run and the
        mapped RFID must meet the configured >=15% recognition threshold to be
        called CONFIRMED_INSIDE.

        If Hall is EMPTY, any stale DB slot mapping is cleared immediately. This
        fixes the case where the program restarts after garments were removed
        and no new OCCUPIED->EMPTY edge exists to clear the old database state.
        """
        now = time.monotonic()
        with self._presence_lock:
            if (
                not force
                and self._presence_cache is not None
                and now - self._presence_cache_at <= float(max_age_s)
            ):
                return self._presence_cache

            hall_states = self._current_hall_states()
            stale_cleared = []
            for item in self.db.registered_items():
                if item.slot is None:
                    continue
                slot_index = int(item.slot) - 1
                hall_occupied = 0 <= slot_index < len(hall_states) and hall_states[slot_index]
                if not hall_occupied:
                    old_slot = int(item.slot)
                    self.db.clear_slot(old_slot)
                    stale_cleared.append({"id": item.id, "rfid": item.rfid_uid, "slot": old_slot})
                    self.db.log_event(
                        "STALE_SLOT_CLEARED",
                        slot=old_slot,
                        rfid_uid=item.rfid_uid,
                        detail={"clothing_id": item.id, "reason": "HALL_EMPTY_VERIFY"},
                    )

            # Re-read after stale mappings were cleared.
            items = self.db.registered_items()
            occupied_slots = [i + 1 for i, occupied in enumerate(hall_states) if occupied]

            rcfg = self.cfg.get("rfid", {}) or {}
            fail_rate = float(rcfg.get("analysis_fail_rate_pct", 15.0))
            analysis = None
            rates: dict[str, float] = {}
            counts: dict[str, int] = {}

            # No Hall slot is occupied -> nothing can be confirmed as inside.
            # Do not let an RFID seen near/outside the closet override Hall.
            if occupied_slots:
                analysis = self.rfid.scan_20_analysis(
                    previous_counts=None,
                    total_scans=int(rcfg.get("analysis_scans", 20)),
                    poll_timeout_s=float(rcfg.get("analysis_poll_timeout_s", 0.13)),
                    poll_delay_s=float(rcfg.get("analysis_poll_delay_s", 0.02)),
                    max_tags=int(rcfg.get("analysis_max_tags", 10)),
                    top_n=int(rcfg.get("analysis_top_n", 3)),
                    fail_rate_pct=fail_rate,
                    label="찾기 화면 Hall + RFID 현재상태 확인",
                )
                rates = {normalize_uid(k): float(v) for k, v in (analysis.get("rates", {}) or {}).items()}
                counts = {normalize_uid(k): int(v) for k, v in (analysis.get("counts", {}) or {}).items()}

            payload_items = []
            confirmed_items = []
            uncertain_count = 0
            for item in items:
                uid = normalize_uid(item.rfid_uid)
                rate = float(rates.get(uid, 0.0))
                count = int(counts.get(uid, 0))
                slot = item.slot
                hall_occupied = bool(
                    slot is not None
                    and 1 <= int(slot) <= len(hall_states)
                    and hall_states[int(slot) - 1]
                )
                rfid_seen = rate >= fail_rate

                if slot is not None and hall_occupied and rfid_seen:
                    presence = "CONFIRMED_INSIDE"
                    inside = True
                elif slot is not None and hall_occupied and not rfid_seen:
                    presence = "HALL_ONLY_RFID_WEAK"
                    inside = False
                    uncertain_count += 1
                elif slot is None and rfid_seen:
                    presence = "RFID_ONLY_NO_SLOT"
                    inside = False
                    uncertain_count += 1
                else:
                    presence = "OUTSIDE"
                    inside = False

                data = self._item_payload(item, include_rfid=True)
                data.update(
                    {
                        "inside": inside,
                        "presence": presence,
                        "hall_occupied": hall_occupied,
                        "rfid_count": count,
                        "rfid_rate": round(rate, 1),
                    }
                )
                payload_items.append(data)
                if inside:
                    confirmed_items.append(data)

            result = {
                "registered_count": len(payload_items),
                "inside_count": len(confirmed_items),
                "outside_count": sum(1 for x in payload_items if x["presence"] == "OUTSIDE"),
                "uncertain_count": uncertain_count,
                "occupied_hall_count": len(occupied_slots),
                "occupied_slots": occupied_slots,
                "hall_occupied": hall_states,
                "rfid_scanned": bool(occupied_slots),
                "rfid_threshold_pct": fail_rate,
                "rfid_top3": (analysis or {}).get("top3", []),
                "stale_cleared": stale_cleared,
                "items": payload_items,
                "inside_items": confirmed_items,
            }
            self._presence_cache = result
            self._presence_cache_at = time.monotonic()
            return result

    @staticmethod
    def _parse_clothing_number(query: str) -> int | None:
        m = re.fullmatch(r"\s*(?:옷\s*)?(\d+)\s*", query or "")
        return int(m.group(1)) if m else None

    def _activate_find(self, item, query: str) -> dict:
        verified = self.verify_closet_state(max_age_s=1.5)
        verified_item = next((x for x in verified.get("items", []) if int(x["id"]) == int(item.id)), None)
        if not verified_item or not verified_item.get("inside"):
            presence = (verified_item or {}).get("presence", "OUTSIDE")
            if presence == "HALL_ONLY_RFID_WEAK":
                msg = f"옷 {item.id}의 홈은 눌려 있지만 RFID 확인률이 기준보다 낮아 위치를 확정하지 않았습니다."
            elif presence == "RFID_ONLY_NO_SLOT":
                msg = f"옷 {item.id} RFID는 감지됐지만 Hall 홈 위치가 확인되지 않았습니다."
            else:
                msg = f"옷 {item.id}은 현재 Hall과 RFID로 옷장 안이 확인되지 않았습니다."
            return {"ok": False, "status": presence, "message": msg}

        # Refresh item because Hall verification may have cleared a stale slot.
        item = self.db.get_by_id(int(item.id))
        if item is None or item.slot is None:
            return {"ok": False, "status": "OUTSIDE", "message": "현재 위치가 없습니다."}

        # New find automatically turns the previous target off first.
        self.complete_find(silent=True)
        self.solenoid.on(item.slot)
        with self._lock:
            self._active_find_slot = item.slot
            self._active_find_rfid = item.rfid_uid
        self.db.log_event("FIND_ACTIVATE", slot=item.slot, rfid_uid=item.rfid_uid, detail={"query": query, "clothing_id": item.id})
        return {
            "ok": True,
            "status": "FOUND",
            "message": f"옷 {item.id}은 {item.slot}번 위치에 있습니다. 솔레노이드를 올렸습니다.",
            "slot": item.slot,
            "rfid": item.rfid_uid,
            "item": {
                "id": item.id,
                "category": item.category,
                "color": item.color,
                "season": item.season,
                "extra": item.extra,
            },
        }

    @staticmethod
    def _item_payload(item, include_rfid: bool = False) -> dict:
        data = {
            "id": item.id,
            "category": item.category,
            "color": item.color,
            "season": item.season,
            "extra": item.extra,
            "slot": item.slot,
            "inside": item.slot is not None,
            "position": item.position,
            "color_hex": item.color_hex,
            "pattern": item.pattern,
            "preference": item.preference,
            "wear_count": item.wear_count,
        }
        if include_rfid:
            data["rfid"] = item.rfid_uid
        return data

    def list_registered_items(self) -> dict:
        state = self.verify_closet_state(max_age_s=1.0)
        return {
            "count": state["registered_count"],
            "inside_count": state["inside_count"],
            "outside_count": state["outside_count"],
            "uncertain_count": state["uncertain_count"],
            "items": state["items"],
            "hall_occupied": state["hall_occupied"],
            "rfid_scanned": state["rfid_scanned"],
        }

    def delete_registered_item(self, item_id: int) -> dict:
        """Delete the clothing information and RFID registration.

        If the clothing is currently the active Find target, its solenoid is
        turned off first. Deleting an item that is physically hanging also
        deletes its stored slot association; the Hall sensor itself is not
        changed, so the user should remove/reinsert the hanger before using that
        physical slot for a fresh automatic placement event.
        """
        item = self.db.get_by_id(int(item_id))
        if item is None:
            return {
                "ok": False,
                "status": "NOT_REGISTERED",
                "message": f"옷 {item_id}은 등록되어 있지 않습니다.",
            }

        with self._lock:
            active_same_item = (
                (
                    self._active_find_rfid is not None
                    and normalize_uid(self._active_find_rfid) == normalize_uid(item.rfid_uid)
                )
                or normalize_uid(item.rfid_uid)
                in {normalize_uid(uid) for uid in self._active_outfit_rfids}
            )
        if active_same_item:
            self.complete_find(silent=True)

        deleted = self.db.delete_item(int(item_id))
        if deleted is None:
            return {
                "ok": False,
                "status": "NOT_REGISTERED",
                "message": f"옷 {item_id}은 등록되어 있지 않습니다.",
            }

        self._invalidate_presence_cache()
        location = f"{deleted.slot}번 위치 정보와 함께 " if deleted.slot is not None else ""
        return {
            "ok": True,
            "status": "DELETED",
            "message": f"옷 {deleted.id}의 {location}RFID 등록을 삭제했습니다.",
            "item": self._item_payload(deleted, include_rfid=True),
        }

    def list_inside_items(self) -> dict:
        state = self.verify_closet_state(max_age_s=1.0)
        items = []
        for data in state["inside_items"]:
            copy = dict(data)
            copy.pop("rfid", None)
            items.append(copy)
        return {
            "count": len(items),
            "items": items,
            "hall_occupied": state["hall_occupied"],
            "rfid_scanned": state["rfid_scanned"],
        }

    def find(self, query: str) -> dict:
        number = self._parse_clothing_number(query)
        if number is not None:
            item = self.db.get_by_id(number)
            if item is None:
                return {"ok": False, "status": "NOT_REGISTERED", "message": f"옷 {number}은 등록되어 있지 않습니다."}
            return self._activate_find(item, query)

        # Feature search does NOT activate a solenoid immediately.
        # It returns every best-matching item currently located in the closet so
        # the browser can read the candidates aloud as 1번, 2번, ... and let the
        # user choose one.  Only the subsequent selection activates the solenoid.
        verified = self.verify_closet_state(max_age_s=1.5)
        confirmed_ids = {int(x["id"]) for x in verified.get("inside_items", [])}
        matches = [(item, score) for item, score in self.db.find_items(query) if int(item.id) in confirmed_ids]
        if not matches:
            return {
                "ok": False,
                "status": "NOT_FOUND",
                "message": "해당 조건의 옷을 옷장에서 찾지 못했습니다.",
                "options": [],
            }

        top_score = matches[0][1]
        top = [(item, score) for item, score in matches if score == top_score]
        options = [
            {
                "id": item.id,
                "category": item.category,
                "color": item.color,
                "season": item.season,
                "extra": item.extra,
            }
            for item, _ in top
        ]

        return {
            "ok": True,
            "status": "CANDIDATES",
            "message": f"조건에 맞는 옷을 {len(options)}벌 찾았습니다. 번호를 말씀해주세요.",
            "query": query,
            "options": options,
        }

    def complete_find(self, silent: bool = False) -> dict:
        with self._lock:
            slot = self._active_find_slot
            uid = self._active_find_rfid
            outfit_slots = list(self._active_outfit_slots)
            outfit_rfids = list(self._active_outfit_rfids)
            self._active_find_slot = None
            self._active_find_rfid = None
            self._active_outfit_slots = []
            self._active_outfit_rfids = []
        targets: list[tuple[int, str | None]] = []
        if slot is not None:
            targets.append((slot, uid))
        targets.extend(zip(outfit_slots, outfit_rfids))
        completed_slots: list[int] = []
        errors: list[Exception] = []
        for target_slot, target_uid in targets:
            if target_slot in completed_slots:
                continue
            try:
                self.solenoid.off(target_slot)
            except Exception as e:
                errors.append(e)
                print(f"[FIND] solenoid off warning: {e}")
            self.db.log_event("FIND_COMPLETE", slot=target_slot, rfid_uid=target_uid)
            completed_slots.append(target_slot)
        if errors and not silent:
            raise errors[0]
        return {"ok": True, "slot": slot, "slots": completed_slots}

    def recommend_codi(self, location: str, top_n: int = 3) -> dict:
        weather = self.weather_manager.get_weather(location)
        current_season = self.weather_manager.get_season(float(weather["temperature"]))
        state = self.verify_closet_state(max_age_s=0.0, force=True)
        confirmed_ids = [int(item["id"]) for item in state.get("inside_items", [])]
        items = [item for item_id in confirmed_ids if (item := self.db.get_by_id(item_id))]
        results, ignored = recommend_outfits(items, current_season, weather, top_n=top_n)
        if not results:
            positions = {item.position for item in items if item.position}
            raise RuntimeError(
                "추천 가능한 상의와 하의 조합이 없습니다. "
                "옷장 안에 상의·하의를 각각 등록하고 등록 정보의 구분을 확인해주세요."
                + (f" 현재 구분: {', '.join(sorted(positions))}" if positions else "")
            )
        recommendations = [
            {
                "score": result["score"],
                "top": self._item_payload(result["top"]),
                "bottom": self._item_payload(result["bottom"]),
                "reasons": result["reasons"],
            }
            for result in results
        ]
        self.db.log_event(
            "CODI_RECOMMEND",
            detail={
                "input_location": location,
                "season": current_season,
                "recommendations": [
                    {
                        "score": result["score"],
                        "top_id": result["top"]["id"],
                        "bottom_id": result["bottom"]["id"],
                    }
                    for result in recommendations
                ],
            },
        )
        return {
            "weather": weather,
            "current_season": current_season,
            "recommendations": recommendations,
            "ignored_items": ignored,
        }

    def activate_codi(self, top_id: int, bottom_id: int) -> dict:
        if int(top_id) == int(bottom_id):
            raise ValueError("상의와 하의는 서로 다른 옷이어야 합니다.")
        state = self.verify_closet_state(max_age_s=1.5)
        confirmed_ids = {int(item["id"]) for item in state.get("inside_items", [])}
        if int(top_id) not in confirmed_ids or int(bottom_id) not in confirmed_ids:
            raise RuntimeError("선택한 코디의 상의 또는 하의가 현재 옷장 안에서 확인되지 않습니다.")
        top = self.db.get_by_id(int(top_id))
        bottom = self.db.get_by_id(int(bottom_id))
        if top is None or bottom is None or top.slot is None or bottom.slot is None:
            raise RuntimeError("선택한 코디의 위치 정보가 없습니다.")

        self.complete_find(silent=True)
        activated: list[int] = []
        try:
            for slot in (int(top.slot), int(bottom.slot)):
                self.solenoid.on(slot)
                activated.append(slot)
        except Exception:
            for slot in activated:
                try:
                    self.solenoid.off(slot)
                except Exception:
                    pass
            raise

        with self._lock:
            self._active_outfit_slots = [int(top.slot), int(bottom.slot)]
            self._active_outfit_rfids = [top.rfid_uid, bottom.rfid_uid]
        self.db.mark_worn([top.id, bottom.id])
        top = self.db.get_by_id(top.id) or top
        bottom = self.db.get_by_id(bottom.id) or bottom
        self.db.log_event(
            "CODI_ACTIVATE",
            detail={
                "top_id": top.id,
                "top_slot": top.slot,
                "bottom_id": bottom.id,
                "bottom_slot": bottom.slot,
            },
        )
        return {
            "ok": True,
            "message": (
                f"상의 옷 {top.id}은 {top.slot}번, 하의 옷 {bottom.id}은 "
                f"{bottom.slot}번 위치입니다. 두 솔레노이드를 올렸습니다."
            ),
            "top": self._item_payload(top),
            "bottom": self._item_payload(bottom),
            "slots": [top.slot, bottom.slot],
        }

    def update_codi_preference(self, item_id: int, preference: int) -> dict:
        item = self.db.update_preference(item_id, preference)
        if item is None:
            return {"ok": False, "message": f"옷 {item_id}은 등록되어 있지 않습니다."}
        return {"ok": True, "item": self._item_payload(item)}

    def on_slot_removed(self, slot: int) -> None:
        with self._lock:
            active = self._active_find_slot == slot or slot in self._active_outfit_slots
            registration_scanning = (
                self._registration.get("status") == "SCANNING"
                and self._registration.get("slot") == slot
            )
            if registration_scanning:
                self._registration.update(
                    status="FAILED",
                    message="RFID 스캔 중 옷걸이가 빠졌습니다. 다시 등록해주세요.",
                    expires_at=None,
                )
        if active:
            self.complete_find(silent=True)

    def status(self) -> dict:
        base = {
            "registered_count": len(self.db.registered_rfids()),
            "inside_count": sum(1 for x in (self.session_engine.hall_debouncer.snapshot() if self.session_engine else []) if x),
            "reserved_registration_rfid": self.get_reserved_registration_rfid(),
            "active_find_slot": self._active_find_slot,
            "active_find_rfid": self._active_find_rfid,
            "active_outfit_slots": list(self._active_outfit_slots),
            "rfid_power_dbm": getattr(self.rfid, "current_power_dbm", None),
            "rfid_last_analysis": getattr(self.rfid, "last_analysis", {}),
            "registration": self.registration_status(),
        }
        if self.session_engine:
            base["session"] = self.session_engine.status()
        else:
            try:
                base["hall_voltages"] = [round(v, 4) for v in self.hall.read_all()]
            except Exception as e:
                base["hall_error"] = str(e)
        return base
