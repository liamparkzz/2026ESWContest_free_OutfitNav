from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .models import ClothingItem


class _ClosingConnection(sqlite3.Connection):
    """SQLite context manager that also releases its file handle on exit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def normalize_uid(uid: str) -> str:
    return "".join(str(uid or "").strip().upper().split())


class ClosetDatabase:
    def __init__(self, path: str):
        self.path = str(Path(path))
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=10,
            check_same_thread=False,
            factory=_ClosingConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS smart_closet_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rfid_uid TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL DEFAULT '',
                    color TEXT NOT NULL DEFAULT '',
                    season TEXT NOT NULL DEFAULT '',
                    extra TEXT NOT NULL DEFAULT '',
                    slot INTEGER NULL,
                    position TEXT NOT NULL DEFAULT '',
                    color_hex TEXT NOT NULL DEFAULT '',
                    pattern TEXT NOT NULL DEFAULT '',
                    preference INTEGER NOT NULL DEFAULT 0,
                    wear_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    CHECK (slot IS NULL OR (slot >= 1 AND slot <= 8))
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_smart_closet_slot_unique
                ON smart_closet_items(slot)
                WHERE slot IS NOT NULL;

                CREATE TABLE IF NOT EXISTS smart_closet_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_time REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    slot INTEGER NULL,
                    rfid_uid TEXT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS smart_closet_deleted_rfids (
                    rfid_uid TEXT PRIMARY KEY,
                    deleted_at REAL NOT NULL
                );
                """
            )
            self._migrate_codi_columns(conn)
            self._migrate_legacy_items(conn)

    @staticmethod
    def _migrate_codi_columns(conn: sqlite3.Connection) -> None:
        """Add outfit fields without replacing an existing wardrobe database."""
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(smart_closet_items)").fetchall()
        }
        additions = {
            "position": "TEXT NOT NULL DEFAULT ''",
            "color_hex": "TEXT NOT NULL DEFAULT ''",
            "pattern": "TEXT NOT NULL DEFAULT ''",
            "preference": "INTEGER NOT NULL DEFAULT 0",
            "wear_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(
                    f'ALTER TABLE smart_closet_items ADD COLUMN "{name}" {definition}'
                )

    def _migrate_legacy_items(self, conn: sqlite3.Connection) -> None:
        """Import legacy `clothing_items` registrations if that table exists.

        The earlier Smart Closet code used clothing_items.rfid_uid.  This keeps old
        registrations from being accidentally registered again while moving to the
        integrated runtime. Unknown legacy columns are simply ignored.
        """
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clothing_items'"
        ).fetchone()
        if not exists:
            return
        columns = {row[1] for row in conn.execute("PRAGMA table_info(clothing_items)").fetchall()}
        if "rfid_uid" not in columns:
            return

        def col_expr(candidates: list[str], fallback: str = "''") -> str:
            for name in candidates:
                if name in columns:
                    return f"COALESCE(\"{name}\", '')"
            return fallback

        category = col_expr(["category", "subcategory", "type", "clothing_type"])
        color = col_expr(["color_name", "color", "colour"])
        season = col_expr(["season"])
        extra = col_expr(["extra", "description", "notes", "memo"])
        rows = conn.execute(
            f"SELECT rfid_uid, {category} AS category, {color} AS color, {season} AS season, {extra} AS extra "
            "FROM clothing_items WHERE rfid_uid IS NOT NULL AND TRIM(rfid_uid) != ''"
        ).fetchall()
        now = time.time()
        deleted = {
            normalize_uid(r[0])
            for r in conn.execute("SELECT rfid_uid FROM smart_closet_deleted_rfids").fetchall()
        }
        for row in rows:
            uid = normalize_uid(row[0])
            if not uid or uid in deleted:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO smart_closet_items
                (rfid_uid, category, color, season, extra, slot, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (uid, row[1] or "", row[2] or "", row[3] or "", row[4] or "", now, now),
            )

    @staticmethod
    def _row_to_item(row: sqlite3.Row | None) -> Optional[ClothingItem]:
        if row is None:
            return None
        return ClothingItem(
            id=int(row["id"]),
            rfid_uid=row["rfid_uid"],
            category=row["category"],
            color=row["color"],
            season=row["season"],
            extra=row["extra"],
            slot=row["slot"],
            position=row["position"],
            color_hex=row["color_hex"],
            pattern=row["pattern"],
            preference=int(row["preference"]),
            wear_count=int(row["wear_count"]),
        )

    def register_item(
        self,
        rfid_uid: str,
        category: str,
        color: str,
        season: str,
        extra: str = "",
        slot: int | None = None,
        position: str = "",
        color_hex: str = "",
        pattern: str = "",
    ) -> ClothingItem:
        uid = normalize_uid(rfid_uid)
        if not uid:
            raise ValueError("RFID UID가 비어 있습니다.")
        if slot is not None and not 1 <= int(slot) <= 8:
            raise ValueError("slot은 1~8이어야 합니다.")
        now = time.time()
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM smart_closet_items WHERE rfid_uid=?", (uid,)).fetchone()
            if existing:
                raise ValueError(f"이미 등록된 RFID입니다: {uid}")
            if slot is not None:
                occupant = conn.execute(
                    "SELECT rfid_uid FROM smart_closet_items WHERE slot=?", (int(slot),)
                ).fetchone()
                if occupant:
                    raise RuntimeError(
                        f"slot {slot}에는 이미 다른 RFID가 배치되어 있습니다: {occupant[0]}"
                    )
            # Explicit re-registration is allowed even if the same RFID was
            # deleted previously. Remove the tombstone before inserting again.
            conn.execute("DELETE FROM smart_closet_deleted_rfids WHERE rfid_uid=?", (uid,))
            cur = conn.execute(
                """
                INSERT INTO smart_closet_items
                (rfid_uid, category, color, season, extra, slot,
                 position, color_hex, pattern, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    category.strip(),
                    color.strip(),
                    season.strip(),
                    extra.strip(),
                    int(slot) if slot is not None else None,
                    position.strip(),
                    color_hex.strip().upper(),
                    pattern.strip(),
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM smart_closet_items WHERE id=?", (cur.lastrowid,)).fetchone()
        self.log_event(
            "REGISTER",
            slot=slot,
            rfid_uid=uid,
            detail={
                "category": category,
                "color": color,
                "season": season,
                "slot": slot,
                "position": position,
                "color_hex": color_hex,
                "pattern": pattern,
            },
        )
        return self._row_to_item(row)  # type: ignore[return-value]

    def registered_rfids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT rfid_uid FROM smart_closet_items").fetchall()
        return {normalize_uid(r[0]) for r in rows}

    def get_by_rfid(self, rfid_uid: str) -> Optional[ClothingItem]:
        uid = normalize_uid(rfid_uid)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM smart_closet_items WHERE rfid_uid=?", (uid,)).fetchone()
        return self._row_to_item(row)

    def get_by_slot(self, slot: int) -> Optional[ClothingItem]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM smart_closet_items WHERE slot=?", (slot,)).fetchone()
        return self._row_to_item(row)

    def get_by_id(self, item_id: int) -> Optional[ClothingItem]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM smart_closet_items WHERE id=?", (int(item_id),)).fetchone()
        return self._row_to_item(row)

    def unlocated_registered_rfids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT rfid_uid FROM smart_closet_items WHERE slot IS NULL").fetchall()
        return {normalize_uid(r[0]) for r in rows}

    def assign_slot(self, rfid_uid: str, slot: int) -> None:
        uid = normalize_uid(rfid_uid)
        if not 1 <= slot <= 8:
            raise ValueError("slot은 1~8이어야 합니다.")
        now = time.time()
        with self._connect() as conn:
            occupant = conn.execute("SELECT rfid_uid FROM smart_closet_items WHERE slot=?", (slot,)).fetchone()
            if occupant and normalize_uid(occupant[0]) != uid:
                raise RuntimeError(f"slot {slot}에 이미 다른 RFID가 배치되어 있습니다: {occupant[0]}")
            row = conn.execute("SELECT id FROM smart_closet_items WHERE rfid_uid=?", (uid,)).fetchone()
            if not row:
                raise KeyError(f"등록되지 않은 RFID입니다: {uid}")
            conn.execute("UPDATE smart_closet_items SET slot=?, updated_at=? WHERE rfid_uid=?", (slot, now, uid))
        self.log_event("ASSIGN_SLOT", slot=slot, rfid_uid=uid)

    def clear_slot(self, slot: int) -> Optional[str]:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute("SELECT rfid_uid FROM smart_closet_items WHERE slot=?", (slot,)).fetchone()
            if not row:
                return None
            uid = normalize_uid(row[0])
            conn.execute("UPDATE smart_closet_items SET slot=NULL, updated_at=? WHERE slot=?", (now, slot))
        self.log_event("CLEAR_SLOT", slot=slot, rfid_uid=uid)
        return uid

    def occupied_items(self) -> list[ClothingItem]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM smart_closet_items WHERE slot IS NOT NULL ORDER BY slot").fetchall()
        return [self._row_to_item(r) for r in rows if r is not None]  # type: ignore[list-item]


    def registered_items(self) -> list[ClothingItem]:
        """Return every currently registered clothing item, inside or outside."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM smart_closet_items ORDER BY id"
            ).fetchall()
        return [self._row_to_item(r) for r in rows if r is not None]  # type: ignore[list-item]

    def delete_item(self, item_id: int) -> Optional[ClothingItem]:
        """Delete one clothing/RFID registration permanently.

        A tombstone prevents the legacy migration table from silently restoring
        the same RFID on the next program restart. If the user later registers
        the same physical RFID again, register_item() removes the tombstone.
        """
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM smart_closet_items WHERE id=?", (int(item_id),)
            ).fetchone()
            item = self._row_to_item(row)
            if item is None:
                return None
            uid = normalize_uid(item.rfid_uid)
            conn.execute(
                "INSERT OR REPLACE INTO smart_closet_deleted_rfids(rfid_uid, deleted_at) VALUES(?, ?)",
                (uid, now),
            )
            conn.execute("DELETE FROM smart_closet_items WHERE id=?", (int(item_id),))
        self.log_event(
            "DELETE_REGISTRATION",
            slot=item.slot,
            rfid_uid=item.rfid_uid,
            detail={
                "clothing_id": item.id,
                "category": item.category,
                "color": item.color,
                "season": item.season,
                "extra": item.extra,
                "slot": item.slot,
            },
        )
        return item

    def find_items(self, query: str) -> list[tuple[ClothingItem, int]]:
        q = "".join(query.lower().split())
        if not q:
            return []
        results: list[tuple[ClothingItem, int]] = []
        for item in self.occupied_items():
            score = 0
            fields = [item.category, item.color, item.season, item.extra, item.position, item.pattern]
            weights = [4, 4, 1, 1, 2, 1]
            for field, weight in zip(fields, weights):
                token = "".join((field or "").lower().split())
                if token and token in q:
                    score += weight
            # Allow user to say a partial word that exists in a field.
            for token in q.replace(",", " ").split():
                for field in fields[:2]:
                    compact = "".join((field or "").lower().split())
                    if token and token in compact:
                        score += 1
            if score > 0:
                results.append((item, score))
        results.sort(key=lambda x: (-x[1], x[0].slot or 999))
        return results

    def update_preference(self, item_id: int, preference: int) -> Optional[ClothingItem]:
        value = max(-5, min(5, int(preference)))
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM smart_closet_items WHERE id=?", (int(item_id),)
            ).fetchone()
            if not exists:
                return None
            conn.execute(
                "UPDATE smart_closet_items SET preference=?, updated_at=? WHERE id=?",
                (value, time.time(), int(item_id)),
            )
        self.log_event(
            "CODI_PREFERENCE",
            detail={"clothing_id": int(item_id), "preference": value},
        )
        return self.get_by_id(int(item_id))

    def mark_worn(self, item_ids: list[int]) -> list[ClothingItem]:
        unique_ids = sorted({int(item_id) for item_id in item_ids})
        if not unique_ids:
            return []
        now = time.time()
        with self._connect() as conn:
            for item_id in unique_ids:
                conn.execute(
                    "UPDATE smart_closet_items "
                    "SET wear_count=wear_count+1, updated_at=? WHERE id=?",
                    (now, item_id),
                )
        self.log_event("CODI_WORN", detail={"clothing_ids": unique_ids})
        return [item for item_id in unique_ids if (item := self.get_by_id(item_id)) is not None]

    def log_event(self, event_type: str, slot: int | None = None, rfid_uid: str | None = None, detail: dict | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO smart_closet_events(event_time,event_type,slot,rfid_uid,detail_json) VALUES(?,?,?,?,?)",
                (time.time(), event_type, slot, normalize_uid(rfid_uid) if rfid_uid else None, json.dumps(detail or {}, ensure_ascii=False)),
            )

    def recent_events(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM smart_closet_events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]
