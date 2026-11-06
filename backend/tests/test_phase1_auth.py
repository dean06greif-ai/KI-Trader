"""Phase 1.5 (Audit S01/S03/S04/E5/E6): JWT-Secret fail-fast, konstante-Zeit-Login,
Lockout, geschützte Finanz-/Kosten-Endpunkte. Ohne Netzwerk."""
import inspect
import time

import pytest

from core import auth


def test_resolve_jwt_secret_fail_fast_in_production():
    with pytest.raises(RuntimeError):
        auth.resolve_jwt_secret(None, production=True)
    with pytest.raises(RuntimeError):
        auth.resolve_jwt_secret("change-me", production=True)
    with pytest.raises(RuntimeError):
        auth.resolve_jwt_secret("short", production=True)
    assert auth.resolve_jwt_secret("a" * 48, production=True) == "a" * 48


def test_resolve_jwt_secret_dev_uses_random_not_default():
    s1 = auth.resolve_jwt_secret(None, production=False)
    s2 = auth.resolve_jwt_secret("change-me", production=False)
    assert s1 != "change-me" and len(s1) >= 32 and s1 != s2


def test_credentials_valid_constant_time_and_user_required():
    assert auth.credentials_valid("Admin", "pw", "Admin", "pw")
    assert not auth.credentials_valid("", "pw", "Admin", "pw")  # leerer User nicht mehr ok
    assert not auth.credentials_valid("Admin", "PW", "Admin", "pw")
    assert auth.credentials_valid("", "pw", "", "pw")  # ohne ADMIN_USER egal
    assert "compare_digest" in inspect.getsource(auth.credentials_valid)


def test_login_lockout_after_max_fails():
    ip = "203.0.113.7"
    auth.clear_login_failures(ip)
    now = time.time()
    for _ in range(auth.LOGIN_MAX_FAILS - 1):
        auth.register_login_failure(ip, now)
    assert auth.login_locked(ip, now) is None
    auth.register_login_failure(ip, now)
    assert auth.login_locked(ip, now) is not None
    assert auth.login_locked(ip, now + auth.LOGIN_LOCK_MIN * 60 + 1) is None
    auth.clear_login_failures(ip)
    assert auth.login_locked(ip) is None


def test_token_roundtrip():
    import asyncio
    from types import SimpleNamespace
    tok = auth.create_admin_token()
    req = SimpleNamespace(headers={"Authorization": f"Bearer {tok}"})
    assert asyncio.run(auth.require_admin(req)) is True


def test_sensitive_endpoints_require_admin():
    from routers import autotrade, analytics, dynamic, notify
    checks = [
        (autotrade.get_capital_allocation, "/api/autotrade/capital"),
        (autotrade.get_balance, "/api/autotrade/balance"),
        (analytics.ai_review, "/api/analytics/ai-review"),
        (analytics.clear_analytics_preview, "/api/analytics/clear/preview"),
        (dynamic.dynamic_refresh, "/api/dynamic/{did}/refresh"),
        (notify.add_notification, "/api/notifications"),
    ]
    for fn, path in checks:
        params = inspect.signature(fn).parameters.values()
        assert any(getattr(p.default, "dependency", None) is auth.require_admin
                   for p in params), f"{path} ohne require_admin"


def test_cors_no_wildcard_with_credentials():
    src = open("/app/backend/server.py", encoding="utf-8").read()
    assert 'allow_origins=["*"], allow_credentials=True' not in src
    assert "CORS_ORIGINS" in src
