"""SQLite storage for the Astrabot audiobook-request feature.

Database name: ``audiobooks`` (file ``audiobooks.db`` by default).
Tables keep the column names from the design document so the code maps
1:1 to the spec: ``users(phoneNumber, firstName)`` and
``requests(requester, bookName, linkToBook)``.

Phone numbers are stored as exactly 10 digits, never formatted, never
with a country code. Display formatting (727-266-9924) happens only in
the UI layer.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SESSION_DAYS = 30


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phone(raw: str | None) -> str | None:
    """Return the 10 digits of a phone number, or None if it is not 10 digits.

    Accepts formatted input (``727-266-9924``, ``(727) 266-9924``) and
    plain digits. An 11-digit number starting with ``1`` is rejected on
    purpose: the spec says valid inputs are 10 digits only.
    """
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits if len(digits) == 10 else None


def format_phone(digits: str) -> str:
    """Format 10 digits as ``727-266-9924``; pass anything else through."""
    if len(digits) == 10 and digits.isdigit():
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits


def mask_phone(digits: str) -> str:
    """Mask a phone number for logs: ``***-***-9924``."""
    if len(digits) == 10 and digits.isdigit():
        return f"***-***-{digits[6:]}"
    return "***"


class AudiobooksDB:
    """Thread-safe-ish SQLite wrapper.

    The bot is asyncio-based and this database is small, so a single
    connection guarded by a lock is enough. Callers must not share
    cursors across threads.
    """

    def __init__(self, path: str | Path = "audiobooks.db"):
        self.path = str(path)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    phoneNumber TEXT PRIMARY KEY,
                    firstName TEXT NOT NULL,
                    createdAt TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requester TEXT NOT NULL REFERENCES users(phoneNumber),
                    bookName TEXT NOT NULL,
                    linkToBook TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Requested',
                    discordMessageId TEXT,
                    discordChannelId TEXT,
                    createdAt TEXT NOT NULL,
                    updatedAt TEXT NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_requester "
                "ON requests(requester)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_status "
                "ON requests(status)"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    phoneNumber TEXT NOT NULL REFERENCES users(phoneNumber),
                    expiresAt TEXT NOT NULL
                )
                """
            )
            self.conn.commit()

    # -- users ---------------------------------------------------------
    def get_user(self, phone: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT phoneNumber, firstName, createdAt FROM users "
                "WHERE phoneNumber = ?",
                (phone,),
            ).fetchone()
        return dict(row) if row else None

    def create_user(self, phone: str, first_name: str) -> dict:
        first_name = first_name.strip()
        if not first_name:
            raise ValueError("First name is required.")
        with self._lock:
            self.conn.execute(
                "INSERT INTO users (phoneNumber, firstName, createdAt) "
                "VALUES (?, ?, ?)",
                (phone, first_name, utcnow_iso()),
            )
            self.conn.commit()
        user = self.get_user(phone)
        assert user is not None
        return user

    def get_or_create_user(self, phone: str, first_name: str) -> dict:
        """Idempotent create: an existing number logs in, never duplicates."""
        existing = self.get_user(phone)
        if existing:
            return existing
        try:
            return self.create_user(phone, first_name)
        except sqlite3.IntegrityError:
            existing = self.get_user(phone)
            assert existing is not None
            return existing

    # -- sessions ------------------------------------------------------
    def create_session(self, phone: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
        with self._lock:
            self.conn.execute(
                "INSERT INTO sessions (token, phoneNumber, expiresAt) "
                "VALUES (?, ?, ?)",
                (token, phone, expires),
            )
            self.conn.commit()
        return token

    def session_phone(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            row = self.conn.execute(
                "SELECT phoneNumber, expiresAt FROM sessions WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        if row["expiresAt"] < utcnow_iso():
            self.delete_session(token)
            return None
        return row["phoneNumber"]

    def delete_session(self, token: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self.conn.commit()

    # -- requests ------------------------------------------------------
    def create_requests(
        self, phone: str, books: list[dict]
    ) -> list[dict]:
        """Insert one row per book, in a single transaction.

        ``books`` is a list of ``{"bookName": ..., "linkToBook": ...}``.
        Returns the created rows (with ids).
        """
        now = utcnow_iso()
        created: list[dict] = []
        with self._lock:
            cur = self.conn.cursor()
            for book in books:
                cur.execute(
                    "INSERT INTO requests (requester, bookName, linkToBook, "
                    "status, createdAt, updatedAt) VALUES (?, ?, ?, "
                    "'Requested', ?, ?)",
                    (phone, book["bookName"], book["linkToBook"], now, now),
                )
                created.append(
                    {
                        "id": cur.lastrowid,
                        "requester": phone,
                        "bookName": book["bookName"],
                        "linkToBook": book["linkToBook"],
                        "status": "Requested",
                        "discordMessageId": None,
                        "discordChannelId": None,
                        "createdAt": now,
                        "updatedAt": now,
                    }
                )
            self.conn.commit()
        return created

    def list_requests(self) -> list[dict]:
        """All requests, newest first, with the requester's first name."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT r.id, r.requester, r.bookName, r.linkToBook, "
                "r.status, r.discordMessageId, r.discordChannelId, "
                "r.createdAt, r.updatedAt, u.firstName AS requesterName "
                "FROM requests r LEFT JOIN users u "
                "ON u.phoneNumber = r.requester "
                "ORDER BY r.id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_request(self, request_id: int) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT r.id, r.requester, r.bookName, r.linkToBook, "
                "r.status, r.discordMessageId, r.discordChannelId, "
                "r.createdAt, r.updatedAt, u.firstName AS requesterName "
                "FROM requests r LEFT JOIN users u "
                "ON u.phoneNumber = r.requester WHERE r.id = ?",
                (request_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_request_by_message(self, message_id: int | str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT r.id, r.requester, r.bookName, r.linkToBook, "
                "r.status, r.discordMessageId, r.discordChannelId, "
                "r.createdAt, r.updatedAt, u.firstName AS requesterName "
                "FROM requests r LEFT JOIN users u "
                "ON u.phoneNumber = r.requester "
                "WHERE r.discordMessageId = ?",
                (str(message_id),),
            ).fetchone()
        return dict(row) if row else None

    def set_request_status(self, request_id: int, status: str) -> dict | None:
        with self._lock:
            self.conn.execute(
                "UPDATE requests SET status = ?, updatedAt = ? WHERE id = ?",
                (status, utcnow_iso(), request_id),
            )
            self.conn.commit()
        return self.get_request(request_id)

    def set_discord_message(
        self, request_id: int, message_id: int | str, channel_id: int | str
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE requests SET discordMessageId = ?, "
                "discordChannelId = ?, updatedAt = ? WHERE id = ?",
                (str(message_id), str(channel_id), utcnow_iso(), request_id),
            )
            self.conn.commit()

    def delete_request(self, request_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM requests WHERE id = ?", (request_id,))
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()
