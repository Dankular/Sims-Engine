"""
persistence/auth.py — AuthStore: persistent user accounts with sim binding.

Schema
------
  users    — one row per registered player (username, email, hashed password,
             linked sim_id, optional MetaMask address)
  sessions — bearer tokens with TTL (default 7 days)

Password hashing
----------------
  PBKDF2-HMAC-SHA256, 100 000 iterations, 16-byte random salt.
  stdlib only — no bcrypt / argon2 dependency.

Uniqueness guarantees
---------------------
  username   → UNIQUE constraint (case-insensitive lookup)
  email      → UNIQUE constraint (always stored lowercase)
  sim_id     → UNIQUE constraint (UUID4 generated at signup,
               additionally verified against engine._sim_lookup before insert)
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


# ── User model ────────────────────────────────────────────────────────────────

@dataclass
class User:
    user_id:          str
    username:         str
    email:            str
    password_hash:    str   # never expose this outside auth layer
    sim_id:           str
    created_at:       float
    last_login:       float
    is_active:        bool  = True
    metamask_address: str   = ""

    def public_dict(self) -> dict:
        """Safe subset — never includes password_hash."""
        return {
            "user_id":          self.user_id,
            "username":         self.username,
            "email":            self.email,
            "sim_id":           self.sim_id,
            "created_at":       self.created_at,
            "last_login":       self.last_login,
            "is_active":        self.is_active,
            "metamask_address": self.metamask_address,
        }


# ── Validation helpers ────────────────────────────────────────────────────────

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
_EMAIL_RE    = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_username(username: str) -> str:
    u = username.strip()
    if not _USERNAME_RE.match(u):
        raise ValueError(
            "Username must be 3–30 characters: letters, digits, underscores only."
        )
    return u


def validate_email(email: str) -> str:
    e = email.strip().lower()
    if not _EMAIL_RE.match(e):
        raise ValueError("Invalid email address.")
    return e


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")


# ── Auth store ────────────────────────────────────────────────────────────────

SESSION_TTL = 7 * 24 * 3600   # 7 days


class AuthStore:
    """
    Persistent user + session store backed by SQLite.

    One AuthStore instance should be created at server startup and shared
    across requests (check_same_thread=False, protected by the engine lock
    where needed).
    """

    def __init__(self, db_path: str = "sim_auth.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id          TEXT PRIMARY KEY,
                username         TEXT NOT NULL,
                username_lower   TEXT UNIQUE NOT NULL,
                email            TEXT UNIQUE NOT NULL,
                password_hash    TEXT NOT NULL,
                sim_id           TEXT UNIQUE NOT NULL,
                created_at       REAL NOT NULL,
                last_login       REAL NOT NULL DEFAULT 0,
                is_active        INTEGER NOT NULL DEFAULT 1,
                metamask_address TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user
                ON sessions(user_id);
        """)
        self._conn.commit()

    # ── User creation ─────────────────────────────────────────────────────────

    def create_user(
        self,
        username: str,
        email: str,
        password: str,
        sim_id: str,
    ) -> User:
        """
        Register a new user and bind them to `sim_id`.

        Raises ValueError if:
          • username / email / sim_id already exists
          • input fails validation
        """
        username  = validate_username(username)
        email     = validate_email(email)
        validate_password(password)

        # Uniqueness pre-checks (friendly error before hitting UNIQUE constraint)
        if self._fetch("SELECT 1 FROM users WHERE username_lower=?",
                       (username.lower(),)):
            raise ValueError(f"Username '{username}' is already taken.")
        if self._fetch("SELECT 1 FROM users WHERE email=?", (email,)):
            raise ValueError("This email address is already registered.")
        if self._fetch("SELECT 1 FROM users WHERE sim_id=?", (sim_id,)):
            raise ValueError(f"Sim ID '{sim_id}' is already bound to another account.")

        user = User(
            user_id=uuid.uuid4().hex,
            username=username,
            email=email,
            password_hash=self._hash_password(password),
            sim_id=sim_id,
            created_at=time.time(),
            last_login=time.time(),
        )
        self._conn.execute(
            """INSERT INTO users
               (user_id, username, username_lower, email, password_hash,
                sim_id, created_at, last_login, is_active, metamask_address)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                user.user_id, user.username, user.username.lower(),
                user.email, user.password_hash, user.sim_id,
                user.created_at, user.last_login, 1, "",
            ),
        )
        self._conn.commit()
        return user

    # ── Authentication ────────────────────────────────────────────────────────

    def authenticate(self, credential: str, password: str) -> User | None:
        """
        Verify password for a username-or-email credential.
        Returns the User on success, None on failure.
        Updates `last_login` timestamp.
        """
        user = (
            self.get_by_username(credential)
            or self.get_by_email(credential)
        )
        if user is None or not user.is_active:
            return None
        if not self._verify_password(password, user.password_hash):
            return None
        self._conn.execute(
            "UPDATE users SET last_login=? WHERE user_id=?",
            (time.time(), user.user_id),
        )
        self._conn.commit()
        return user

    # ── Sessions ──────────────────────────────────────────────────────────────

    def create_session(self, user_id: str, ttl: float = SESSION_TTL) -> str:
        """Issue a new bearer token. Returns the token string."""
        token = secrets.token_urlsafe(32)
        now   = time.time()
        self._conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, now, now + ttl),
        )
        self._conn.commit()
        return token

    def verify_token(self, token: str) -> User | None:
        """
        Validate a bearer token and return the associated User.
        Returns None if the token is missing, expired, or the user is inactive.
        Expired tokens are deleted on the fly.
        """
        if not token:
            return None
        row = self._fetch(
            "SELECT user_id, expires_at FROM sessions WHERE token=?", (token,)
        )
        if not row:
            return None
        user_id, expires_at = row
        if time.time() > expires_at:
            self._conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            self._conn.commit()
            return None
        return self.get_by_id(user_id)

    def revoke_token(self, token: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        self._conn.commit()

    def revoke_all_sessions(self, user_id: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self._conn.commit()

    # ── Lookups ───────────────────────────────────────────────────────────────

    def get_by_id(self, user_id: str) -> User | None:
        row = self._fetch("SELECT * FROM users WHERE user_id=?", (user_id,))
        return self._row(row) if row else None

    def get_by_username(self, username: str) -> User | None:
        row = self._fetch(
            "SELECT * FROM users WHERE username_lower=?",
            (username.strip().lower(),),
        )
        return self._row(row) if row else None

    def get_by_email(self, email: str) -> User | None:
        row = self._fetch(
            "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
        )
        return self._row(row) if row else None

    def get_by_sim_id(self, sim_id: str) -> User | None:
        row = self._fetch("SELECT * FROM users WHERE sim_id=?", (sim_id,))
        return self._row(row) if row else None

    def sim_id_taken(self, sim_id: str) -> bool:
        return bool(self._fetch("SELECT 1 FROM users WHERE sim_id=?", (sim_id,)))

    # ── Profile updates ───────────────────────────────────────────────────────

    def link_metamask(self, user_id: str, address: str) -> None:
        self._conn.execute(
            "UPDATE users SET metamask_address=? WHERE user_id=?",
            (address.lower(), user_id),
        )
        self._conn.commit()

    def set_active(self, user_id: str, active: bool) -> None:
        self._conn.execute(
            "UPDATE users SET is_active=? WHERE user_id=?",
            (int(active), user_id),
        )
        self._conn.commit()

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        total   = self._fetch("SELECT COUNT(*) FROM users")[0]       # type: ignore[index]
        active_s= self._fetch(
            "SELECT COUNT(*) FROM sessions WHERE expires_at>?", (time.time(),)
        )[0]   # type: ignore[index]
        return {"total_users": total, "active_sessions": active_s}

    # ── Password helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        dk   = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return f"pbkdf2:{salt}:{dk.hex()}"

    @staticmethod
    def _verify_password(password: str, stored: str) -> bool:
        try:
            _, salt, dk_hex = stored.split(":")
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
            return secrets.compare_digest(dk.hex(), dk_hex)
        except Exception:
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch(self, sql: str, params: tuple = ()) -> tuple | None:
        return self._conn.execute(sql, params).fetchone()

    @staticmethod
    def _row(row: tuple) -> User:
        return User(
            user_id=row[0],
            username=row[1],
            # row[2] = username_lower, skip
            email=row[3],
            password_hash=row[4],
            sim_id=row[5],
            created_at=row[6],
            last_login=row[7],
            is_active=bool(row[8]),
            metamask_address=row[9],
        )
