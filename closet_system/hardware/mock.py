from __future__ import annotations

import threading


class MockHallArray:
    def __init__(self, slot_count: int = 8):
        self.slot_count = slot_count
        self.voltages = [1.0] * slot_count

    def read_all(self, samples_per_slot: int = 1):
        return list(self.voltages)

    def close(self):
        pass


class MockDoorSensor:
    def __init__(self):
        self.open = False

    def is_open(self):
        return self.open

    def close(self):
        pass


class MockSolenoidController:
    def __init__(self, slot_count: int = 8):
        self.slot_count = slot_count
        self.states = [False] * slot_count

    def on(self, slot):
        self.states[slot - 1] = True
        return f"OK ON {slot}"

    def off(self, slot):
        self.states[slot - 1] = False
        return f"OK OFF {slot}"

    def pulse(self, slot, duration_ms=500):
        return f"OK PULSE {slot} {duration_ms}"

    def all_off(self):
        self.states = [False] * self.slot_count
        return "OK ALL_OFF"

    def status(self):
        return str(self.states)

    def close(self):
        self.all_off()


class MockR200Scanner:
    def __init__(self):
        self.tags: set[str] = set()
        self._lock = threading.RLock()
        self.current_power_dbm = 26.0

    def scan_once(self, window_s=0.18):
        with self._lock:
            return set(self.tags)

    def scan_counts(self, duration_s=1.5, window_s=0.18):
        with self._lock:
            tags = set(self.tags)
        scans = max(1, int(duration_s / max(window_s, 0.01)))
        return {tag: scans for tag in tags}, scans

    def stable_scan(self, duration_s=2.0, window_s=0.18, min_hits=3, min_ratio=0.45):
        counts, scans = self.scan_counts(duration_s, window_s)
        return set(counts), counts, scans

    def scan_20_analysis(
        self,
        previous_counts=None,
        total_scans=20,
        poll_timeout_s=0.13,
        poll_delay_s=0.02,
        max_tags=10,
        top_n=3,
        fail_rate_pct=15.0,
        label="RFID",
    ):
        with self._lock:
            tags = sorted(self.tags)
        counts = {tag: int(total_scans) for tag in tags}
        top10 = [
            {"rfid": tag, "count": int(total_scans), "rate": 100.0}
            for tag in tags[: int(max_tags)]
        ]
        previous_counts = dict(previous_counts or {})
        return {
            "label": label,
            "scans": int(total_scans),
            "elapsed_s": 0.0,
            "unique_tags": len(tags),
            "counts": counts,
            "rates": {tag: 100.0 for tag in tags},
            "top10": top10,
            "top3": top10[: int(top_n)],
            "new_tags": [x for x in top10 if x["rfid"] not in previous_counts],
            "failed_tags": [],
            "fail_rate_pct": float(fail_rate_pct),
        }

    def close(self):
        pass
