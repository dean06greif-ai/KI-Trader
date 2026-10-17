"""Prozessweit begrenzter Speicher für berechnete Indikator-Serien (numpy).

Hintergrund (Absturz bei hoher Iterationszahl im Optimizer): FastSeries legte
JEDE Parameter-Kombination (z.B. EMA 9/10/11/…, RSI 7/8/…) dauerhaft als
volles numpy-Array über die gesamte Historie ab. Bei 1m-Kerzen sind das
mehrere MB je Array – bei tausenden Kombinationen und N Kind-Prozessen
(Windows: spawn, jeder Prozess hat seinen eigenen Cache) läuft der RAM voll.
Regime-Lab-Suchen rechnen auf kurzen Regime-Abschnitten und waren deshalb
kaum betroffen.

`ArrayLRU` verhält sich wie ein dict (gleiche Zugriffe wie vorher), alle
Instanzen eines Prozesses teilen sich aber EIN Byte-Budget: wird es
überschritten, fliegen die am längsten unbenutzten Serien raus (sie werden
bei Bedarf neu berechnet bzw. aus indicator_cache geladen – Ergebnisse
bleiben identisch, nur der Speicher ist gedeckelt).

Budget: Env FAST_SIM_CACHE_MB, sonst automatisch (lokaler Worker: Anteil des
RAM je Rechen-Prozess, Server: 512 MB).
"""
import os
import threading
import weakref
from collections import OrderedDict

MIN_KEEP = 4          # die jüngsten Einträge einer Serie nie verdrängen (Mehrfach-Sets)
SERVER_DEFAULT_MB = 512
WORKER_MIN_MB, WORKER_MAX_MB = 256, 4096

_LOCK = threading.RLock()
_GLOBAL: "OrderedDict[tuple, tuple]" = OrderedDict()   # (id(cache), key) -> (weakref, nbytes)
_KEYS: dict = {}                                        # id(cache) -> set(keys) (schnelles Aufräumen)
_STATE = {"total": 0, "budget": None, "evicted": 0}


def _nbytes(v) -> int:
    return int(getattr(v, "nbytes", 0) or 0)


def auto_budget_mb() -> int:
    raw = os.environ.get("FAST_SIM_CACHE_MB")
    if raw:
        try:
            return max(int(float(raw)), 32)
        except ValueError:
            pass
    if not os.environ.get("KI_LOCAL_WORKER"):
        return SERVER_DEFAULT_MB
    try:
        import psutil
        total_mb = psutil.virtual_memory().total / (1024 * 1024)
    except Exception:  # noqa: BLE001
        total_mb = 8192
    try:
        workers = int(os.environ.get("SIM_WORKERS") or 1)
    except ValueError:
        workers = 1
    procs = (os.cpu_count() or 1) if workers <= 0 else workers
    # Hälfte des RAM für Indikator-Serien, verteilt auf Kind-Prozesse + Hauptprozess
    mb = int(total_mb * 0.5 / (procs + 1))
    return max(WORKER_MIN_MB, min(WORKER_MAX_MB, mb))


def budget_bytes() -> int:
    if _STATE["budget"] is None:
        _STATE["budget"] = auto_budget_mb() * 1024 * 1024
    return _STATE["budget"]


def set_budget_mb(mb: int) -> None:
    """Budget live ändern (Tests / Diagnose)."""
    with _LOCK:
        _STATE["budget"] = max(int(mb), 1) * 1024 * 1024
        _evict(None)


def stats() -> dict:
    with _LOCK:
        return {"entries": len(_GLOBAL), "mb": round(_STATE["total"] / 1048576, 1),
                "budget_mb": round(budget_bytes() / 1048576, 1), "evicted": _STATE["evicted"]}


def _evict(protect_cache) -> None:
    budget = budget_bytes()
    while _STATE["total"] > budget and _GLOBAL:
        gk, (ref, n) = next(iter(_GLOBAL.items()))
        cache = ref()
        if cache is not None and cache is protect_cache and \
                gk[1] in list(OrderedDict.keys(cache))[-MIN_KEEP:]:
            break  # jüngste Serien nie verdrängen – kurze Überschreitung ist ok
        _GLOBAL.popitem(last=False)
        _STATE["total"] -= n
        _STATE["evicted"] += 1
        (_KEYS.get(gk[0]) or set()).discard(gk[1])
        if cache is not None:
            OrderedDict.pop(cache, gk[1], None)


class ArrayLRU(OrderedDict):
    """dict-kompatibler Cache mit prozessweitem Byte-Budget (siehe Modul-Doku)."""

    def __init__(self):
        super().__init__()
        self._ref = weakref.ref(self)
        weakref.finalize(self, _forget, id(self))

    def __getitem__(self, key):
        val = OrderedDict.__getitem__(self, key)
        with _LOCK:
            gk = (id(self), key)
            if gk in _GLOBAL:
                _GLOBAL.move_to_end(gk)
        return val

    def get(self, key, default=None):
        """Lesen ohne KeyError-Fenster zwischen `in` und `[]` (parallele Threads)."""
        with _LOCK:
            if not OrderedDict.__contains__(self, key):
                return default
            return self[key]

    def __setitem__(self, key, val):
        with _LOCK:
            gk = (id(self), key)
            old = _GLOBAL.pop(gk, None)
            if old is not None:
                _STATE["total"] -= old[1]
            OrderedDict.__setitem__(self, key, val)
            n = _nbytes(val)
            _GLOBAL[gk] = (self._ref, n)
            _KEYS.setdefault(id(self), set()).add(key)
            _STATE["total"] += n
            _evict(self)


def _forget(cache_id: int) -> None:
    """Serie wurde freigegeben -> ihre Einträge aus der Buchhaltung entfernen."""
    with _LOCK:
        for key in _KEYS.pop(cache_id, ()):
            old = _GLOBAL.pop((cache_id, key), None)
            if old is not None:
                _STATE["total"] -= old[1]
