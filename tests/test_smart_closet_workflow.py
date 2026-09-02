from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from closet_system.factory import build_controller
from closet_system.database import ClosetDatabase


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("조건이 제한 시간 안에 충족되지 않았습니다.")


class SmartClosetWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "closet-test.db")
        self.cfg = {
            "slot_count": 8,
            "database": {"path": self.db_path},
            "hall": {
                "occupied_on_v": 2.15,
                "empty_off_v": 2.05,
                "occupied_low": False,
                "debounce_s": 0.001,
            },
            "door": {"enabled": True, "debounce_s": 0.001},
            "rfid": {
                "analysis_scans": 20,
                "analysis_poll_timeout_s": 0.13,
                "analysis_poll_delay_s": 0.02,
                "analysis_max_tags": 10,
                "analysis_top_n": 3,
                "analysis_fail_rate_pct": 15.0,
                "registration_wait_timeout_s": 30,
            },
            "session": {"poll_interval_s": 0.002, "retry_delay_s": 0.005},
        }
        self.controller = build_controller(self.cfg, mock=True)
        self.controller.start()
        self.controller.session_engine.door.open = True
        wait_until(lambda: self.controller.session_engine.status()["session_open"])

    def tearDown(self) -> None:
        self.controller.stop()
        self.temp_dir.cleanup()

    def _register_one(self, rfid: str = "E200001722110001") -> dict:
        started = self.controller.start_automatic_registration(
            category="셔츠류",
            color="회색",
            season="겨울",
            extra="긴팔",
        )
        self.assertEqual("WAITING_HALL", started["status"])
        self.controller.rfid.tags = {rfid}
        self.controller.hall.voltages[2] = 3.0
        wait_until(lambda: self.controller.registration_status()["status"] == "COMPLETED")
        return self.controller.registration_status()

    def test_new_hall_edge_registers_and_assigns_highest_unregistered_rfid(self) -> None:
        result = self._register_one()

        self.assertEqual(20, result["selected"]["count"])
        self.assertEqual(3, result["item"]["slot"])
        self.assertEqual("회색", result["item"]["color"])
        self.assertEqual("E200001722110001", result["item"]["rfid"])
        self.assertEqual(result["item"]["id"], self.controller.db.get_by_slot(3).id)

    def test_scan_selects_highest_rate_tag_after_excluding_registered_tags(self) -> None:
        self.controller.db.register_item("AAAA", "상의", "검정", "여름")
        self.controller.rfid.scan_20_analysis = lambda **kwargs: {
            "scans": 20,
            "counts": {"AAAA": 20, "BBBB": 17, "CCCC": 6},
            "rates": {"AAAA": 100.0, "BBBB": 85.0, "CCCC": 30.0},
            "top10": [
                {"rfid": "AAAA", "count": 20, "rate": 100.0},
                {"rfid": "BBBB", "count": 17, "rate": 85.0},
                {"rfid": "CCCC", "count": 6, "rate": 30.0},
            ],
            "top3": [],
        }

        selected = self.controller._scan_best_unregistered_rfid("test")

        self.assertEqual("BBBB", selected["rfid"])
        self.assertEqual(17, selected["selected"]["count"])

    def test_equal_top_rates_fail_without_creating_a_registration(self) -> None:
        self.controller.start_automatic_registration("셔츠", "회색", "겨울")
        self.controller.rfid.tags = {"AAAA", "BBBB"}
        self.controller.hall.voltages[0] = 3.0

        wait_until(lambda: self.controller.registration_status()["status"] == "FAILED")

        self.assertEqual(set(), self.controller.db.registered_rfids())
        self.assertIsNone(self.controller.db.get_by_slot(1))
        self.assertIn("여러 개", self.controller.registration_status()["message"])

    def test_codi_recommends_inside_pair_and_activates_two_solenoids(self) -> None:
        top = self.controller.db.register_item(
            "TOP001",
            "반팔 티셔츠",
            "흰색",
            "여름",
            slot=1,
            position="상의",
            color_hex="#FFFFFF",
            pattern="무지",
        )
        bottom = self.controller.db.register_item(
            "BOTTOM001",
            "반바지",
            "남색",
            "여름",
            slot=2,
            position="하의",
            color_hex="#243B5A",
            pattern="무지",
        )
        self.controller.rfid.tags = {top.rfid_uid, bottom.rfid_uid}
        self.controller.hall.voltages[0] = 3.0
        self.controller.hall.voltages[1] = 3.0
        wait_until(
            lambda: self.controller.session_engine.hall_debouncer.snapshot()[:2]
            == [True, True]
        )

        class FakeWeather:
            @staticmethod
            def get_weather(location):
                return {
                    "input_location": location,
                    "location": "서울",
                    "temperature": 28.0,
                    "feels_like": 29.0,
                    "humidity": 55,
                    "condition": "Clear",
                    "description": "맑음",
                }

            @staticmethod
            def get_season(temperature):
                return "여름"

        self.controller.weather_manager = FakeWeather()
        recommendation = self.controller.recommend_codi("서울", top_n=3)
        self.assertEqual(1, len(recommendation["recommendations"]))
        self.assertEqual(top.id, recommendation["recommendations"][0]["top"]["id"])
        self.assertEqual(bottom.id, recommendation["recommendations"][0]["bottom"]["id"])

        activated = self.controller.activate_codi(top.id, bottom.id)
        self.assertEqual([1, 2], activated["slots"])
        self.assertTrue(self.controller.solenoid.states[0])
        self.assertTrue(self.controller.solenoid.states[1])
        self.assertEqual(1, self.controller.db.get_by_id(top.id).wear_count)
        self.assertEqual(1, self.controller.db.get_by_id(bottom.id).wear_count)

        self.controller.complete_find()
        self.assertFalse(self.controller.solenoid.states[0])
        self.assertFalse(self.controller.solenoid.states[1])

    def test_number_find_removal_voice_event_and_delete_preserve_requirements(self) -> None:
        registered = self._register_one()
        item_id = registered["item"]["id"]
        uid = registered["item"]["rfid"]

        found = self.controller.find(f"옷 {item_id}")
        self.assertTrue(found["ok"])
        self.assertEqual(3, found["slot"])
        self.assertTrue(self.controller.solenoid.states[2])

        self.controller.hall.voltages[2] = 1.0
        wait_until(lambda: self.controller.db.get_by_id(item_id).slot is None)
        wait_until(lambda: not self.controller.solenoid.states[2])
        still_registered = self.controller.db.get_by_id(item_id)
        self.assertEqual(uid, still_registered.rfid_uid)

        wait_until(
            lambda: any(
                event["event_type"] == "REMOVE_CONFIRMED"
                for event in self.controller.db.recent_events(30)
            )
        )
        removal = next(
            event
            for event in self.controller.db.recent_events(30)
            if event["event_type"] == "REMOVE_CONFIRMED"
        )
        detail = json.loads(removal["detail_json"])
        self.assertEqual("회색", detail["color"])
        self.assertEqual("셔츠류", detail["category"])
        self.assertEqual("겨울", detail["season"])

        deleted = self.controller.delete_registered_item(item_id)
        self.assertTrue(deleted["ok"])
        self.assertNotIn(uid, self.controller.db.registered_rfids())
        self.assertIsNone(self.controller.db.get_by_id(item_id))

        # A deleted RFID is once again an unregistered RFID candidate.
        candidate = self.controller.scan_unregistered_rfid()
        self.assertEqual(uid, candidate["rfid"])


class DatabaseMigrationTest(unittest.TestCase):
    def test_existing_database_receives_codi_columns_without_losing_items(self) -> None:
        # Windows can keep a SQLite WAL handle briefly after schema migration.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            path = str(Path(temp_dir) / "legacy.db")
            with sqlite3.connect(path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE smart_closet_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        rfid_uid TEXT NOT NULL UNIQUE,
                        category TEXT NOT NULL DEFAULT '',
                        color TEXT NOT NULL DEFAULT '',
                        season TEXT NOT NULL DEFAULT '',
                        extra TEXT NOT NULL DEFAULT '',
                        slot INTEGER NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    INSERT INTO smart_closet_items
                    (rfid_uid, category, color, season, extra, slot, created_at, updated_at)
                    VALUES ('LEGACY01', '청바지', '파란색', '가을', '', NULL, 1, 1);
                    """
                )

            database = ClosetDatabase(path)
            item = database.get_by_rfid("LEGACY01")

            self.assertIsNotNone(item)
            self.assertEqual("청바지", item.category)
            self.assertEqual("", item.position)
            self.assertEqual(0, item.preference)
            self.assertEqual(0, item.wear_count)

if __name__ == "__main__":
    unittest.main()
