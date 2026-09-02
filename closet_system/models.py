from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class ClothingItem:
    id: int
    rfid_uid: str
    category: str
    color: str
    season: str
    extra: str = ""
    slot: Optional[int] = None
    position: str = ""
    color_hex: str = ""
    pattern: str = ""
    preference: int = 0
    wear_count: int = 0


@dataclass(slots=True)
class HallEvent:
    slot: int
    before_occupied: bool
    after_occupied: bool
    timestamp: float
    voltage: float


@dataclass(slots=True)
class PendingPlacement:
    slot: int
    timestamp: float
    voltage: float


@dataclass(slots=True)
class TagTrack:
    epc: str
    first_seen_at: float
    last_seen_at: float
    hit_scans: int = 1
    consecutive_hits: int = 1
    first_stable_at: Optional[float] = None
    seen_timestamps: list[float] = field(default_factory=list)

    def record(self, now: float, min_hits_for_stable: int) -> None:
        self.last_seen_at = now
        self.hit_scans += 1
        self.consecutive_hits += 1
        self.seen_timestamps.append(now)
        if self.first_stable_at is None and self.hit_scans >= min_hits_for_stable:
            self.first_stable_at = now
