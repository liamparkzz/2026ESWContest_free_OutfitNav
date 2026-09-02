from __future__ import annotations

from collections import Counter
import threading
import time

import serial

from ..database import normalize_uid


SINGLE_POLL_CMD = bytes.fromhex("AA0022000022DD")


class R200Scanner:
    """M100/R200 RFID adapter using the exact AA...DD polling protocol
    that was verified in tools/test_rfid_live_power.py.

    Important timing is intentionally kept close to the known-working test:
    - single poll timeout: normally 0.20 s
    - inter-poll gap: 0.05 s
    - TX power: 10..26 dBm, verified with B7 when possible
    """

    MIN_POWER = 10
    MAX_POWER = 26

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 115200,
        power_dbm: int = 26,
        inter_scan_delay_s: float = 0.05,
        debug_detect: bool = False,
    ):
        self.ser = serial.Serial(
            port,
            baudrate,
            timeout=0.05,
            write_timeout=1.0,
        )
        self.lock = threading.RLock()
        self.scan_session_lock = threading.RLock()
        self.running = True
        self.current_power_dbm: float | None = None
        self.port = port
        self.baudrate = baudrate
        self.inter_scan_delay_s = float(inter_scan_delay_s)
        self.debug_detect = bool(debug_detect)
        self._debug_last_seen: dict[str, float] = {}
        self.last_analysis: dict = {}

        print(f"[RFID] M100 AA/DD 연결: {port}, {baudrate}bps")

        try:
            self.set_power(int(power_dbm), verify=True)
        except Exception as e:
            print(f"[RFID] power 설정 경고: {e}")

    # ---------------------------------------------------------
    # Exact frame parsing from the known-working live-power code
    # ---------------------------------------------------------
    @staticmethod
    def checksum_ok(frame: bytes) -> bool:
        if len(frame) < 7:
            return False
        plen = (frame[3] << 8) | frame[4]
        if len(frame) != 7 + plen:
            return False
        return (sum(frame[1:5 + plen]) & 0xFF) == frame[5 + plen]

    def extract_frames(self, buffer: bytearray) -> list[bytes]:
        frames: list[bytes] = []
        while True:
            try:
                start = buffer.index(0xAA)
            except ValueError:
                buffer.clear()
                break

            if start:
                del buffer[:start]

            if len(buffer) < 5:
                break

            plen = (buffer[3] << 8) | buffer[4]
            total = 7 + plen

            if len(buffer) < total:
                break

            if buffer[total - 1] != 0xDD:
                del buffer[0]
                continue

            frame = bytes(buffer[:total])
            del buffer[:total]

            if self.checksum_ok(frame):
                frames.append(frame)

        return frames

    # ---------------------------------------------------------
    # Power control: same B6 AA...DD protocol as working test
    # ---------------------------------------------------------
    def set_power(self, dbm: int, verify: bool = True) -> float | None:
        if not (self.MIN_POWER <= int(dbm) <= self.MAX_POWER):
            raise ValueError(
                f"RF 출력은 {self.MIN_POWER}~{self.MAX_POWER} dBm 범위여야 합니다."
            )

        dbm = int(dbm)
        value = dbm * 100
        msb = (value >> 8) & 0xFF
        lsb = value & 0xFF
        chk = (0x00 + 0xB6 + 0x00 + 0x02 + msb + lsb) & 0xFF
        cmd = bytes([0xAA, 0x00, 0xB6, 0x00, 0x02, msb, lsb, chk, 0xDD])

        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(cmd)
            self.ser.flush()

            deadline = time.monotonic() + 1.0
            buffer = bytearray()

            while time.monotonic() < deadline:
                waiting = self.ser.in_waiting
                data = self.ser.read(waiting if waiting else 1)
                if data:
                    buffer.extend(data)
                    for frame in self.extract_frames(buffer):
                        if frame[1] == 0x01 and frame[2] == 0xB6:
                            plen = (frame[3] << 8) | frame[4]
                            payload = frame[5:5 + plen]
                            if not payload or payload[0] == 0x00:
                                print(f"[RFID] RF 출력 변경 ACK: {dbm} dBm")
                                if verify:
                                    actual = self.get_power()
                                    if actual is not None:
                                        self.current_power_dbm = actual
                                        print(f"[RFID] 실제 설정값 확인: {actual:.2f} dBm")
                                        return actual
                                self.current_power_dbm = float(dbm)
                                return self.current_power_dbm
                            raise RuntimeError(
                                "RF 출력 변경 실패: " + frame.hex(" ").upper()
                            )
                else:
                    time.sleep(0.005)

        raise RuntimeError("RF 출력 설정 응답 없음")

    def get_power(self) -> float | None:
        # AA 00 B7 00 00 B7 DD
        cmd = bytes.fromhex("AA00B70000B7DD")

        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(cmd)
            self.ser.flush()

            deadline = time.monotonic() + 1.0
            buffer = bytearray()

            while time.monotonic() < deadline:
                waiting = self.ser.in_waiting
                data = self.ser.read(waiting if waiting else 1)
                if data:
                    buffer.extend(data)
                    for frame in self.extract_frames(buffer):
                        if frame[1] == 0x01 and frame[2] == 0xB7:
                            plen = (frame[3] << 8) | frame[4]
                            payload = frame[5:5 + plen]
                            if len(payload) >= 2:
                                raw = (payload[0] << 8) | payload[1]
                                self.current_power_dbm = raw / 100.0
                                return self.current_power_dbm
                            return None
                else:
                    time.sleep(0.005)

        return None

    # ---------------------------------------------------------
    # Exact inventory method from test_rfid_live_power.py
    # ---------------------------------------------------------
    def single_poll(self, timeout: float = 0.20) -> list[str]:
        epcs: list[str] = []

        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(SINGLE_POLL_CMD)
            self.ser.flush()

            deadline = time.monotonic() + float(timeout)
            buffer = bytearray()

            while time.monotonic() < deadline:
                waiting = self.ser.in_waiting
                data = self.ser.read(waiting if waiting else 1)

                if data:
                    buffer.extend(data)
                    for frame in self.extract_frames(buffer):
                        if frame[1] == 0x02 and frame[2] == 0x22:
                            plen = (frame[3] << 8) | frame[4]
                            payload = frame[5:5 + plen]
                            if len(payload) >= 5:
                                epc = payload[3:-2].hex().upper()
                                uid = normalize_uid(epc)
                                if uid and uid not in epcs:
                                    epcs.append(uid)
                else:
                    time.sleep(0.003)

        if self.debug_detect and epcs:
            now = time.monotonic()
            for epc in epcs:
                if now - self._debug_last_seen.get(epc, 0.0) >= 0.5:
                    print(f"[RFID DETECT] EPC={epc}")
                    self._debug_last_seen[epc] = now

        return epcs

    # Public API used by registration/session code.
    def scan_once(self, window_s: float = 0.20) -> set[str]:
        with self.scan_session_lock:
            tags = set(self.single_poll(float(window_s)))
            if self.inter_scan_delay_s > 0:
                time.sleep(self.inter_scan_delay_s)
            return tags

    def scan_20_analysis(
        self,
        previous_counts: dict[str, int] | None = None,
        total_scans: int = 20,
        poll_timeout_s: float = 0.13,
        poll_delay_s: float = 0.02,
        max_tags: int = 10,
        top_n: int = 3,
        fail_rate_pct: float = 15.0,
        label: str = "RFID",
    ) -> dict:
        """Run the same 20-scan analysis that was validated in the standalone test.

        IMPORTANT: the low-level recognition path is intentionally untouched:
        every scan still calls the exact known-working ``single_poll()`` above,
        which sends ``AA0022000022DD`` and parses M100 AA...DD inventory frames.

        One EPC can count at most once per scan.  The result contains the top 10,
        top 3, newly appearing tags compared with the previous round, and tags
        that fell below the configured failure rate (<15% by default).
        """
        total_scans = max(1, int(total_scans))
        poll_timeout_s = float(poll_timeout_s)
        poll_delay_s = max(0.0, float(poll_delay_s))
        max_tags = max(1, int(max_tags))
        top_n = max(1, int(top_n))
        fail_rate_pct = float(fail_rate_pct)

        counts: Counter[str] = Counter()
        started = time.monotonic()

        # Keep all 20 polls atomic. Registration, Hall matching, and any other
        # caller cannot interleave serial commands during the round.
        with self.scan_session_lock:
            for scan_index in range(1, total_scans + 1):
                epcs = self.single_poll(timeout=poll_timeout_s)
                counts.update(set(epcs or []))
                if scan_index < total_scans and poll_delay_s > 0:
                    time.sleep(poll_delay_s)

        elapsed = time.monotonic() - started
        ranked = counts.most_common(max_tags)
        rates = {
            epc: (count / total_scans) * 100.0
            for epc, count in counts.items()
        }
        top10 = [
            {"rfid": epc, "count": count, "rate": rates[epc]}
            for epc, count in ranked
        ]
        top3 = top10[:top_n]

        current_tags = set(counts.keys())
        previous_counts = dict(previous_counts or {})
        previous_tags = set(previous_counts.keys())

        new_tags = []
        for epc in sorted(
            current_tags - previous_tags,
            key=lambda tag: counts[tag],
            reverse=True,
        ):
            new_tags.append(
                {
                    "rfid": epc,
                    "previous_count": 0,
                    "previous_rate": 0.0,
                    "count": counts[epc],
                    "rate": rates[epc],
                }
            )

        failed_tags = []
        for epc in sorted(previous_tags):
            current_count = counts.get(epc, 0)
            current_rate = (current_count / total_scans) * 100.0
            if current_rate < fail_rate_pct:
                previous_count = previous_counts.get(epc, 0)
                failed_tags.append(
                    {
                        "rfid": epc,
                        "previous_count": previous_count,
                        "previous_rate": (previous_count / total_scans) * 100.0,
                        "count": current_count,
                        "rate": current_rate,
                    }
                )

        analysis = {
            "label": label,
            "scans": total_scans,
            "elapsed_s": elapsed,
            "unique_tags": len(counts),
            "counts": dict(counts),
            "rates": rates,
            "top10": top10,
            "top3": top3,
            "new_tags": new_tags,
            "failed_tags": failed_tags,
            "fail_rate_pct": fail_rate_pct,
        }
        self.last_analysis = analysis
        self._print_20scan_analysis(analysis)
        return analysis

    @staticmethod
    def _print_20scan_analysis(analysis: dict) -> None:
        scans = int(analysis.get("scans", 20))
        print()
        print("=" * 62)
        print(f"[RFID 20-SCAN] {analysis.get('label', 'RFID')}")
        print(
            f"Scan={scans} | elapsed={analysis.get('elapsed_s', 0.0):.2f}s | "
            f"unique={analysis.get('unique_tags', 0)}"
        )
        print("-" * 62)

        top10 = analysis.get("top10", [])
        if not top10:
            print("[NONE] 인식된 RFID 없음")
        else:
            for rank, item in enumerate(top10, start=1):
                marker = "*" if rank <= 3 else " "
                print(
                    f"{marker} {rank:>2}위 | {item['count']:>2}/{scans} | "
                    f"{item['rate']:>5.1f}% | {item['rfid']}"
                )

        print("[TOP 3]")
        for rank, item in enumerate(analysis.get("top3", []), start=1):
            print(
                f"  {rank}위 {item['rfid']} | "
                f"{item['count']}/{scans} ({item['rate']:.1f}%)"
            )

        changes = False
        for item in analysis.get("new_tags", []):
            changes = True
            print(
                f"[NEW] 신규 {item['rfid']} | "
                f"{item['count']}/{scans} ({item['rate']:.1f}%)"
            )
        for item in analysis.get("failed_tags", []):
            changes = True
            print(
                f"[WARN] 인식실패 {item['rfid']} | "
                f"이전 {item['previous_count']}/{scans} ({item['previous_rate']:.1f}%) -> "
                f"현재 {item['count']}/{scans} ({item['rate']:.1f}%)"
            )
        if not changes:
            print("[SAME] 변경점 없음")
        print("=" * 62)

    def scan_counts(
        self,
        duration_s: float = 1.5,
        window_s: float = 0.20,
    ) -> tuple[dict[str, int], int]:
        counts: Counter[str] = Counter()
        scans = 0
        end = time.monotonic() + float(duration_s)

        # Keep a whole matching/registration scan atomic so another web/session
        # scan cannot interleave commands on the same serial reader.
        with self.scan_session_lock:
            while time.monotonic() < end:
                epcs = self.single_poll(float(window_s))
                counts.update(set(epcs))
                scans += 1
                if self.inter_scan_delay_s > 0:
                    time.sleep(self.inter_scan_delay_s)

        return dict(counts), scans

    def stable_scan(
        self,
        duration_s: float = 2.0,
        window_s: float = 0.20,
        min_hits: int = 3,
        min_ratio: float = 0.45,
    ) -> tuple[set[str], dict[str, int], int]:
        counts, scans = self.scan_counts(
            duration_s=duration_s,
            window_s=window_s,
        )
        required = max(min_hits, int(scans * min_ratio + 0.999))
        stable = {tag for tag, count in counts.items() if count >= required}
        return stable, counts, scans

    def close(self) -> None:
        self.running = False
        try:
            if self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
