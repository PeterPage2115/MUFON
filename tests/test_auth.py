"""Tests for the dashboard auth helpers (task C1): SessionStore, ActionLimiter,
the Discord OAuth URL/token helpers (no real HTTP — the API calls are only
asserted by signature/shape; the flow is mocked in test_dashboard_api) and
resolve_admin.

allow: SIZE_OK — declarative test file (one tiny Given/When/Then test per
behavior), same precedent as the other test modules.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from travian.dashboard import auth

# --- SessionStore -------------------------------------------------------------


class TestSessionStore:
    def test_create_get_delete_roundtrip(self) -> None:
        store = auth.SessionStore()
        token = store.create("user-1", "Tester", admin=True)
        session = store.get(token)
        assert session is not None
        assert session.user_id == "user-1"
        assert session.username == "Tester"
        assert session.admin is True
        assert session.expires_at > datetime.now(UTC)

        store.delete(token)
        assert store.get(token) is None

    def test_tokens_are_unique(self) -> None:
        store = auth.SessionStore()
        assert store.create("u", "A", admin=False) != store.create("u", "A", admin=False)

    def test_expired_session_pruned_on_get(self) -> None:
        store = auth.SessionStore()
        token = store.create("user-1", "Tester", admin=False)
        session = store.get(token)
        assert session is not None
        # Age the session past its TTL, then get() must prune + return None.
        store._sessions[token] = auth.Session(  # pyright: ignore[reportPrivateUsage]  # test-only age manipulation
            token=token,
            user_id=session.user_id,
            username=session.username,
            admin=session.admin,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        assert store.get(token) is None
        assert token not in store._sessions  # pyright: ignore[reportPrivateUsage]  # test-only inspection

    def test_unknown_token_none(self) -> None:
        store = auth.SessionStore()
        assert store.get("no-such-token") is None

    def test_thread_safety_smoke(self) -> None:
        store = auth.SessionStore()
        tokens: list[str] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(50):
                    token = store.create("u", "W", admin=False)
                    tokens.append(token)
                    assert store.get(token) is not None
                    store.delete(token)
            except BaseException as exc:  # noqa: BLE001 — test bookkeeping
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors
        assert len(tokens) == 200


# --- ActionLimiter -------------------------------------------------------------


class TestActionLimiter:
    def test_limit_enforced(self) -> None:
        limiter = auth.ActionLimiter(limit=2, window_s=60)
        assert limiter.allow("k") is True
        assert limiter.allow("k") is True
        assert limiter.allow("k") is False
        assert limiter.retry_after("k") > 0

    def test_keys_are_independent(self) -> None:
        limiter = auth.ActionLimiter(limit=1, window_s=60)
        assert limiter.allow("a") is True
        assert limiter.allow("a") is False
        assert limiter.allow("b") is True

    def test_window_slides(self) -> None:
        limiter = auth.ActionLimiter(limit=2, window_s=1)
        assert limiter.allow("k") is True
        assert limiter.allow("k") is True
        assert limiter.allow("k") is False
        time.sleep(1.1)
        assert limiter.allow("k") is True  # the window slid past the first hit

    def test_retry_after_zero_when_room(self) -> None:
        limiter = auth.ActionLimiter(limit=3, window_s=60)
        limiter.allow("k")
        assert limiter.retry_after("k") == 0

    def test_retry_after_unknown_key_zero(self) -> None:
        assert auth.ActionLimiter().retry_after("nope") == 0


# --- Discord OAuth URL ----------------------------------------------------------


class TestAuthorizeUrl:
    def test_shape_and_scopes(self) -> None:
        url = auth.authorize_url("client-123", "http://host:8099/api/auth/callback", "state-abc")
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "discord.com"
        assert parsed.path == "/oauth2/authorize"
        qs = parse_qs(parsed.query)
        assert qs["response_type"] == ["code"]
        assert qs["client_id"] == ["client-123"]
        assert qs["state"] == ["state-abc"]
        assert qs["scope"] == ["identify guilds guilds.members.read"]
        assert qs["redirect_uri"] == ["http://host:8099/api/auth/callback"]
        assert "redirect_uri=" in parsed.query  # URL-encoded, not raw


# --- resolve_admin ---------------------------------------------------------------


def _member(roles: list[str]) -> dict[str, object]:
    return {"roles": roles}


def _guild(guild_id: str, permissions: object) -> dict[str, object]:
    return {"id": guild_id, "permissions": permissions}


class TestResolveAdmin:
    def test_role_grants_admin(self) -> None:
        assert auth.resolve_admin(_member(["111", "555"]), [], "100", admin_role_id=555) == (True, True)

    def test_member_without_role_is_member_not_admin(self) -> None:
        assert auth.resolve_admin(_member([]), [], "100", admin_role_id=555) == (True, False)

    def test_no_admin_role_configured(self) -> None:
        assert auth.resolve_admin(_member(["555"]), [], "100", admin_role_id=None) == (True, False)

    def test_manage_guild_bit_grants_admin(self) -> None:
        guilds = [_guild("100", "32")]  # Manage Server = 1 << 5
        assert auth.resolve_admin(_member([]), guilds, "100", admin_role_id=555) == (True, True)

    def test_manage_guild_bit_absent(self) -> None:
        guilds = [_guild("100", "0")]
        assert auth.resolve_admin(_member([]), guilds, "100", admin_role_id=None) == (True, False)

    def test_member_endpoint_missing_fallback_guilds(self) -> None:
        guilds = [_guild("100", "32")]  # Manage Server = 1 << 5
        assert auth.resolve_admin(None, guilds, "100", admin_role_id=555) == (True, True)

    def test_member_endpoint_missing_without_guild_not_member(self) -> None:
        assert auth.resolve_admin(None, [], "100", admin_role_id=555) == (False, False)

    def test_member_endpoint_missing_without_bit_not_admin(self) -> None:
        guilds = [_guild("100", "0")]
        assert auth.resolve_admin(None, guilds, "100", admin_role_id=555) == (True, False)

    def test_permissions_non_string_ignored(self) -> None:
        guilds = [_guild("100", 32)]  # non-string bitfield → not admin
        assert auth.resolve_admin(None, guilds, "100", admin_role_id=None) == (True, False)


# --- helper sanity ----------------------------------------------------------------


class TestConstants:
    def test_manage_guild_bit_is_0x20(self) -> None:
        assert auth.MANAGE_GUILD_BIT == 1 << 5
