"""Zentrale Obergrenzen für Such-Iterationen (Optimizer, Regime-Lab, Dynamik-Werkbank).

Vorher war die Grenze an drei Stellen fest auf 500 verdrahtet – höhere Eingaben
wurden still gekappt. Jetzt gilt EINE Quelle mit getrennten Grenzen:
- Cloud (Render): schützt CPU/RAM des Web-Service (Env SEARCH_MAX_ITERATIONS_CLOUD)
- Lokal (Worker): deutlich höher, der PC trägt die Last (Env SEARCH_MAX_ITERATIONS_LOCAL)
Der lokale Worker setzt KI_LOCAL_WORKER=1 und nutzt automatisch die lokale Grenze.
Spiegel im Frontend: frontend/src/constants/searchLimits.js (Test prüft Gleichstand).
"""
import os
from typing import Dict, Optional

DEFAULT_ITERATIONS = 40
DEFAULT_MAX_CLOUD = 2000
DEFAULT_MAX_LOCAL = 20000


def _env_int(name: str, dflt: int) -> int:
    try:
        return max(int(os.environ.get(name) or dflt), 1)
    except ValueError:
        return dflt


def is_local(execution: Optional[str] = None) -> bool:
    return str(execution or "").lower() == "local" or os.environ.get("KI_LOCAL_WORKER") == "1"


def max_iterations(execution: Optional[str] = None) -> int:
    if is_local(execution):
        return _env_int("SEARCH_MAX_ITERATIONS_LOCAL", DEFAULT_MAX_LOCAL)
    return _env_int("SEARCH_MAX_ITERATIONS_CLOUD", DEFAULT_MAX_CLOUD)


def clamp_iterations(value, execution: Optional[str] = None, minimum: int = 5) -> int:
    try:
        n = int(value) if value not in (None, "") else DEFAULT_ITERATIONS
    except (TypeError, ValueError):
        n = DEFAULT_ITERATIONS
    return int(min(max(n, minimum), max_iterations(execution)))


def limits() -> Dict[str, int]:
    return {"cloud": _env_int("SEARCH_MAX_ITERATIONS_CLOUD", DEFAULT_MAX_CLOUD),
            "local": _env_int("SEARCH_MAX_ITERATIONS_LOCAL", DEFAULT_MAX_LOCAL)}
