"""SQLite storage for the Astrabot audiobook-request feature.

Database name: ``audiobooks`` (file ``audiobooks.db`` by default).
Tables keep the column names from the design document so the code maps
1:1 to the spec: ``users(email, firstName)`` and
``requests(requester, bookName, linkToBook)``.

Email addresses are stored lowercased, with no surrounding whitespace.
Migration note (2026-10-29): this feature was originally phone-based.
If an old phone-based database is opened, its ``users``, ``requests``
and ``sessions`` tables are renamed to ``*_phone_legacy`` (preserved,
not used) and fresh email-based tables are created. Old phone accounts
cannot be converted automatically -- there is no email address on file
for them -- so those users create a new account with their email.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SESSION_DAYS = 30

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(raw: str | None) -> str | None:
    """Return a lowercased email address, or None if it is not one."""
    if raw is None:
        return None
    email = str(raw).strip().lower()
    return email if _EMAIL_RE.match(email) else None


def mask_email(email: str) -> str:
    """Mask an email address for logs: ``t***@example.com``."""
    if "@" in email:
        local, domain = email.split("@", 1)
        return f"{local[:1]}***@{domain}" if local else f"***@{domain}"
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

    def _table_columns(self, table: str) -> list[str]:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [row["name"] for row in rows]

    def init_schema(self) -> None:
        with self._lock:
            # Migrate a pre-2026-10-29 phone-based database, if present:
            # preserve the old tables under *_phone_legacy, then build
            # fresh email-based tables. Only runs once -- a migrated
            # database no longer has a phoneNumber column.
            for table in ("users", "requests", "sessions"):
                try:
                    cols = self._table_columns(table)
                except Exception:
                    cols = []
                if "phoneNumber" in cols:
                    self.conn.execute(
                        f"ALTER TABLE {table} RENAME TO {table}_phone_legacy"
                    )
            cur = self.conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    firstName TEXT NOT NULL,
                    createdAt TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requester TEXT NOT NULL REFERENCES users(email),
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
                    email TEXT NOT NULL REFERENCES users(email),
                    expiresAt TEXT NOT NULL
                )
                """
            )
            self.conn.commit()

    # -- users ---------------------------------------------------------
    def get_user(self, email: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT email, firstName, createdAt FROM users "
                "WHERE email = ?",
                (email,),
            ).fetchone()
        return dict(row) if row else None

    def create_user(self, email: str, first_name: str) -> dict:
        first_name = first_name.strip()
        if not first_name:
            raise ValueError("First name is required.")
        with self._lock:
            self.conn.execute(
                "INSERT INTO users (email, firstName, createdAt) "
                "VALUES (?, ?, ?)",
                (email, first_name, utcnow_iso()),
            )
            self.conn.commit()
        user = self.get_user(email)
        assert user is not None
        return user

    def get_or_create_user(self, email: str, first_name: str) -> dict:
        """Idempotent create: an existing email logs in, never duplicates."""
        existing = self.get_user(email)
        if existing:
            return existing
        try:
            return self.create_user(email, first_name)
        except sqlite3.IntegrityError:
            existing = self.get_user(email)
            assert existing is not None
            return existing

    # -- sessions ------------------------------------------------------
    def create_session(self, email: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
        with self._lock:
            self.conn.execute(
                "INSERT INTO sessions (token, email, expiresAt) "
                "VALUES (?, ?, ?)",
                (token, email, expires),
            )
            self.conn.commit()
        return token

    def session_email(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            row = self.conn.execute(
                "SELECT email, expiresAt FROM sessions WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        if row["expiresAt"] < utcnow_iso():
            self.delete_session(token)
            return None
        return row["email"]

    def delete_session(self, token: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self.conn.commit()

    # -- requests ------------------------------------------------------
    def create_requests(
        self, email: str, books: list[dict]
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
                    (email, book["bookName"], book["linkToBook"], now, now),
                )
                created.append(
                    {
                        "id": cur.lastrowid,
                        "requester": email,
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
                "ON u.email = r.requester "
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
                "ON u.email = r.requester WHERE r.id = ?",
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
                "ON u.email = r.requester "
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
