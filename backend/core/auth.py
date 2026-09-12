"""Admin-Auth (JWT). Schützt WRITE-Aktionen und sensible Finanz-/Kosten-Endpunkte;
unkritische GETs + Health bleiben öffentlich.

Härtung (Audit S01/E5/E6, 09/2026):
- JWT_SECRET MUSS gesetzt sein. In Produktion (Render setzt `RENDER=true`) oder mit
  `JWT_REQUIRE_SECRET=1` bricht der Start ohne Secret ab (fail-fast). Lokal/Dev wird
  ein zufälliges Prozess-Secret erzeugt (Tokens verfallen beim Neustart) – nie mehr
  das öffentlich bekannte 'change-me'.
- Passwortvergleich in konstanter Zeit (hmac.compare_digest).
- Login-Lockout: nach LOGIN_MAX_FAILS Fehlversuchen je Client-IP LOGIN_LOCK_MIN Minuten Sperre.
"""
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

import jwt
from fastapi import HTTPException, Request

from core import config  # noqa: F401  (stellt load_dotenv sicher)

logger = logging.getLogger(__name__)

_INSECURE_SECRETS = {"", "change-me", "changeme", "secret", "jwt_secret"}


def _production() -> bool:
    return bool(os.getenv("RENDER")) or str(os.getenv("JWT_REQUIRE_SECRET", "")).lower() in ("1", "true", "yes")


def resolve_jwt_secret(env_value: Optional[str], production: bool) -> str:
    """Secret bestimmen (rein): fehlend/unsicher -> Produktion: Fehler, sonst Zufall."""
    val = (env_value or "").strip()
    if val not in _INSECURE_SECRETS and len(val) >= 16:
        return val
    if production:
        raise RuntimeError(
            "JWT_SECRET fehlt oder ist unsicher (mind. 16 Zeichen, nicht 'change-me'). "
            "Bitte als Env-Variable setzen – Start abgebrochen (fail-fast).")
    logger.warning("JWT_SECRET fehlt/unsicher – lokales Zufalls-Secret wird verwendet "
                   "(Tokens verfallen beim Neustart). Für Render JWT_SECRET setzen!")
    return secrets.token_hex(32)


JWT_SECRET = resolve_jwt_secret(os.getenv("JWT_SECRET"), _production())
ADMIN_USER = os.getenv("ADMIN_USER", "Admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

LOGIN_MAX_FAILS = 5
LOGIN_LOCK_MIN = 15
_login_fails: Dict[str, Tuple[int, float]] = {}   # ip -> (fails, locked_until_ts)


def credentials_valid(user: str, pw: str, admin_user: str = None,
                      admin_pw: str = None) -> bool:
    """Konstante-Zeit-Prüfung (rein). Leerer Benutzername nur erlaubt, wenn kein
    ADMIN_USER konfiguriert ist."""
    admin_user = ADMIN_USER if admin_user is None else admin_user
    admin_pw = ADMIN_PASSWORD if admin_pw is None else admin_pw
    pw_ok = hmac.compare_digest(str(pw or "").encode(), str(admin_pw or "").encode())
    if admin_user:
        user_ok = hmac.compare_digest(str(user or "").encode(), str(admin_user).encode())
    else:
        user_ok = True
    return pw_ok and user_ok


def login_locked(ip: str, now: Optional[float] = None) -> Optional[int]:
    """Sekunden Restsperre für die IP oder None."""
    now = now or time.time()
    fails, until = _login_fails.get(ip, (0, 0.0))
    if until and until > now:
        return int(until - now)
    return None


def register_login_failure(ip: str, now: Optional[float] = None) -> None:
    now = now or time.time()
    fails, until = _login_fails.get(ip, (0, 0.0))
    if until and until <= now:
        fails = 0
    fails += 1
    locked_until = now + LOGIN_LOCK_MIN * 60 if fails >= LOGIN_MAX_FAILS else 0.0
    _login_fails[ip] = (fails, locked_until)
    if locked_until:
        logger.warning(f"Login-Lockout für {ip}: {fails} Fehlversuche, {LOGIN_LOCK_MIN} min Sperre")


def clear_login_failures(ip: str) -> None:
    _login_fails.pop(ip, None)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_admin_token() -> str:
    payload = {"sub": "admin", "exp": datetime.now(timezone.utc) + timedelta(days=1)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def require_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="Admin-Login erforderlich")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if payload.get("sub") != "admin":
            raise HTTPException(status_code=401, detail="Ungültiges Token")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token abgelaufen")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Ungültiges Token")
    return True
