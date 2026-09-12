"""Zentrale Test-Konfiguration: lädt backend/.env + REACT_APP_BACKEND_URL,
damit die E2E-Regressionstests in jeder Umgebung (lokal, CI, Render) laufen,
ohne dass Zugangsdaten hartkodiert werden müssen."""
import os
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND_DIR.parent


def _load_env_file(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_env_file(_BACKEND_DIR / ".env")
_load_env_file(_ROOT / "frontend" / ".env")


# ---------------------------------------------------------------------------
# Automatische Test-Marker: 'unit' (schnell, ohne Backend/Netzwerk) vs. 'live'
# (braucht eine laufende Backend-Umgebung). Auswahl: pytest -m unit | -m live.
# Klassifizierung über den Modul-Quelltext: Live-Tests sprechen das Backend
# über eine URL an (REACT_APP_BACKEND_URL / localhost:8001 / Render-Domain).
# ---------------------------------------------------------------------------
import pytest  # noqa: E402

_LIVE_HINTS = ("REACT_APP_BACKEND_URL", "BACKEND_URL", "onrender.com",
               "localhost:8001", "127.0.0.1:8001")
_live_module_cache = {}


def _is_live_module(path: str) -> bool:
    if path not in _live_module_cache:
        try:
            src = Path(path).read_text(errors="ignore")
        except OSError:
            src = ""
        _live_module_cache[path] = any(h in src for h in _LIVE_HINTS)
    return _live_module_cache[path]


def pytest_collection_modifyitems(config, items):
    for item in items:
        name = "live" if _is_live_module(str(item.fspath)) else "unit"
        item.add_marker(getattr(pytest.mark, name))
