"""Discord OAuth, sessions and action rate-limiting for the dashboard (C1).

Pure helpers — no FastAPI dependency (the app wires them in via
``create_app``): a thread-safe in-memory :class:`SessionStore` (restarting
the bot logs everyone out — a conscious tradeoff, documented in the README),
a sliding-window :class:`ActionLimiter` for the admin actions, the Discord
OAuth token exchange / user / guild-member API calls (plain httpx — the
``discord.py`` package has no user-OAuth helper) and :func:`resolve_admin`
turning guild membership + roles into the member/admin flag pair.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from urllib.parse import urlencode

import httpx

#: Discord API base (v10 is the current stable API version).
DISCORD_API: Final = "https://discord.com/api/v10"
#: ``Manage Server`` permission bit (1 << 5) in Discord's permission bitfield.
MANAGE_GUILD_BIT: Final = 1 << 5

_SESSION_TTL: Final = timedelta(days=7)
_HTTP_TIMEOUT: Final = 10.0


@dataclass(frozen=True)
class Session:
    """One logged-in dashboard user (OAuth mode)."""

    token: str
    user_id: str
    username: str
    admin: bool
    expires_at: datetime  # UTC


class SessionStore:
    """Thread-safe in-memory token → :class:`Session` map.

    ``get`` prunes the looked-up session lazily when it expired; expired
    sessions are also swept on every ``create``. Sessions vanish on process
    restart by design.
    """

    _sessions: dict[str, Session]
    _lock: threading.Lock

    def __init__(self) -> None:
        self._sessions = {}
        self._lock = threading.Lock()

    def create(self, user_id: str, username: str, admin: bool) -> str:
        token = secrets.token_urlsafe(32)
        session = Session(
            token=token,
            user_id=user_id,
            username=username,
            admin=admin,
            expires_at=datetime.now(UTC) + _SESSION_TTL,
        )
        with self._lock:
            self._prune_locked()
            self._sessions[token] = session
        return token

    def get(self, token: str) -> Session | None:
        now = datetime.now(UTC)
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expires_at <= now:
                del self._sessions[token]
                return None
            return session

    def delete(self, token: str) -> None:
        with self._lock:
            _ = self._sessions.pop(token, None)

    def _prune_locked(self) -> None:
        now = datetime.now(UTC)
        expired = [token for token, session in self._sessions.items() if session.expires_at <= now]
        for token in expired:
            del self._sessions[token]


class ActionLimiter:
    """Sliding-window rate limit per key (default: 6 calls / 60 s).

    ``allow`` records the call and returns True while the window has room;
    once ``limit`` calls sit inside the window it returns False without
    recording (the caller surfaces ``Retry-After`` via :meth:`retry_after`).
    Thread-safe (the dashboard runs in a multi-threaded server).
    """

    limit: int
    window_s: int
    _hits: dict[str, deque[float]]
    _lock: threading.Lock

    def __init__(self, limit: int = 6, window_s: int = 60) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            _trim_hits(hits, now, self.window_s)
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest recorded call leaves the window (0 = no
        wait needed / unknown key)."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            _trim_hits(hits, now, self.window_s)
            if len(hits) < self.limit:
                return 0
            return max(1, int(hits[0] + self.window_s - now) + 1)


def _trim_hits(hits: deque[float], now: float, window_s: int) -> None:
    """Drop timestamps that fell out of the sliding window (caller holds the lock)."""
    cutoff = now - window_s
    while hits and hits[0] <= cutoff:
        _ = hits.popleft()


# --- Discord OAuth (user scope) -----------------------------------------------


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """The Discord authorization URL for the dashboard login.

    Scopes: ``identify`` (user id/name), ``guilds`` (membership + permission
    bitfields), ``guilds.members.read`` (guild member object incl. roles).
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": "identify guilds guilds.members.read",
        "state": state,
        "redirect_uri": redirect_uri,
    }
    return "https://discord.com/oauth2/authorize?" + urlencode(params)


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict[str, object]:
    """Exchange an authorization ``code`` for an access-token response.

    POST ``/api/oauth2/token`` with Basic auth (client id/secret) and a
    form-encoded body per the Discord docs; raises on any non-2xx.
    """
    resp = httpx.post(
        "https://discord.com/api/oauth2/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        auth=(client_id, client_secret),
        timeout=_HTTP_TIMEOUT,
    )
    _ = resp.raise_for_status()
    return cast(dict[str, object], resp.json())


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def fetch_user(access_token: str) -> dict[str, object]:
    """``GET /users/@me`` — the logged-in user's profile."""
    resp = httpx.get(f"{DISCORD_API}/users/@me", headers=_bearer(access_token), timeout=_HTTP_TIMEOUT)
    _ = resp.raise_for_status()
    return cast(dict[str, object], resp.json())


def fetch_guild_member(access_token: str, guild_id: int | str) -> dict[str, object] | None:
    """``GET /users/@me/guilds/{guild}/member`` — 404 (not a member) → None."""
    resp = httpx.get(
        f"{DISCORD_API}/users/@me/guilds/{guild_id}/member",
        headers=_bearer(access_token),
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code == 404:
        return None
    _ = resp.raise_for_status()
    return cast(dict[str, object], resp.json())


def fetch_guilds(access_token: str) -> list[dict[str, object]]:
    """``GET /users/@me/guilds`` — membership list with permission bitfields."""
    resp = httpx.get(f"{DISCORD_API}/users/@me/guilds", headers=_bearer(access_token), timeout=_HTTP_TIMEOUT)
    _ = resp.raise_for_status()
    return cast(list[dict[str, object]], resp.json())


def resolve_admin(
    member: dict[str, object] | None,
    guilds: list[dict[str, object]],
    guild_id: int | str,
    admin_role_id: int | None,
) -> tuple[bool, bool]:
    """(is_member, is_admin) for the dashboard login.

    With a member object (member endpoint OK): membership confirmed; admin =
    ``admin_role_id`` in the member's roles OR the ``Manage Server`` bit in
    the guilds-list entry. With ``member=None`` (endpoint 404): membership
    falls back to the guilds list, and admin can only come from the
    ``Manage Server`` bit — never from a role check (roles are unknown).
    """
    guild_id_str = str(guild_id)

    def manage_guild_entry() -> bool:
        entry = next((g for g in guilds if str(g.get("id", "")) == guild_id_str), None)
        permissions = entry.get("permissions") if entry is not None else None
        if not isinstance(permissions, str):
            return False
        try:
            return (int(permissions) & MANAGE_GUILD_BIT) != 0
        except ValueError:
            return False

    if member is None:
        if not any(str(g.get("id", "")) == guild_id_str for g in guilds):
            return False, False
        return True, manage_guild_entry()

    roles: set[str] = set()
    roles_raw = member.get("roles", [])
    if isinstance(roles_raw, list):
        for item in cast(list[object], roles_raw):
            roles.add(str(item))
    has_role = admin_role_id is not None and str(admin_role_id) in roles
    return True, has_role or manage_guild_entry()
