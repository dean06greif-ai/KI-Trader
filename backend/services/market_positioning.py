"""Markt-Positionierung: Funding-Rate + Long/Short-Ratio (Konten) als Zeitreihen.

Quellen (öffentlich, ohne Key):
  * Historie: data.binance.vision – Funding-Settlements (monatlich) und
    5m-Metrics mit ``count_long_short_ratio`` (globale Konten-L/S, wie TradeX)
  * Live: Binance fapi (fundingRate / globalLongShortAccountRatio), Fallback OKX
    (funding-rate-history / rubik long-short-account-ratio-contract)

Bewertung wie die TradeX-Indikatoren: nicht der Rohwert zählt, sondern das
Perzentil gegen die eigene Historie (Funding: letzte N Tage, L/S: letzte N Tage).
Kein Look-Ahead: jeder Kerze wird nur der zuletzt VOR ihrem Schluss bekannte
Wert zugeordnet (Funding = abgerechnetes Settlement, L/S = 5m-Snapshot).

Sync-Download (Backtest/Optimizer laufen in Threads) + async Live-Refresh.
Fehlende Daten -> Bedingung gilt als NICHT erfüllt (konservativ).
"""
import asyncio
import csv
import io
import logging
import os
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

ARCHIVE = "https://data.binance.vision/data/futures/um"
BINANCE_FAPI = "https://fapi.binance.com"
OKX = "https://www.okx.com/api/v5"
DAY_MS = 86_400_000
FUNDING_MAX_AGE_MS = 13 * 3_600_000      # Settlements alle 4-8h
LS_MAX_AGE_MS = 40 * 60_000               # 5m-Snapshots
CACHE_DIR = os.environ.get("POSITIONING_CACHE_DIR") or os.path.join(
    tempfile.gettempdir(), "ki_positioning")

_lock = threading.RLock()
_SERIES: Dict[str, Dict[str, Dict[int, float]]] = {}   # sym -> kind -> {ts: val}
_ARR: Dict[tuple, tuple] = {}                            # (sym, kind) -> (ver, ts, val)
_VER: Dict[tuple, int] = {}
_RANK: Dict[tuple, np.ndarray] = {}                      # (sym, kind, days, ver) -> pct
_DONE: set = set()                                       # geladene Archiv-Dateien
_LIVE_STATE: Dict[str, Dict] = {}


# ------------------------------------------------------------------ Speicher
def _merge(sym: str, kind: str, rows: Dict[int, float]):
    if not rows:
        return
    with _lock:
        _SERIES.setdefault(sym, {}).setdefault(kind, {}).update(rows)
        _VER[(sym, kind)] = _VER.get((sym, kind), 0) + 1


def series(sym: str, kind: str):
    """(ts, values) sortiert als numpy – gecacht bis zur nächsten Änderung."""
    with _lock:
        ver = _VER.get((sym, kind), 0)
        hit = _ARR.get((sym, kind))
        if hit and hit[0] == ver:
            return hit[1], hit[2], ver
        d = _SERIES.get(sym, {}).get(kind, {})
        ts = np.array(sorted(d), dtype=np.int64)
        val = np.array([d[t] for t in ts], dtype=float)
        _ARR[(sym, kind)] = (ver, ts, val)
        return ts, val, ver


def rolling_pct(ts: np.ndarray, val: np.ndarray, days: float) -> np.ndarray:
    """Perzentil (0..1) jedes Werts innerhalb der letzten `days` Tage (inkl. sich selbst)."""
    if len(ts) == 0:
        return np.array([], dtype=float)
    s = pd.Series(val, index=pd.to_datetime(ts, unit="ms"))
    return s.rolling(f"{max(float(days), 0.05)}D").rank(pct=True).to_numpy()


def _pct_cached(sym: str, kind: str, days: float):
    ts, val, ver = series(sym, kind)
    key = (sym, kind, float(days), ver)
    with _lock:
        r = _RANK.get(key)
    if r is None:
        r = rolling_pct(ts, val, days)
        with _lock:
            for k in [k for k in _RANK if k[:3] == key[:3]]:
                _RANK.pop(k, None)
            _RANK[key] = r
    return ts, val, r


def asof(ts_src: np.ndarray, arr: np.ndarray, ts_query: np.ndarray, max_age_ms: int):
    """Letzter bekannter Wert <= ts_query (ohne Look-Ahead); zu alt -> NaN."""
    out = np.full(len(ts_query), np.nan)
    if len(ts_src) == 0:
        return out
    idx = np.searchsorted(ts_src, ts_query, side="right") - 1
    ok = idx >= 0
    idx_c = np.where(ok, idx, 0)
    fresh = ok & ((ts_query - ts_src[idx_c]) <= max_age_ms)
    out[fresh] = arr[idx_c[fresh]]
    return out


def ls_kind(sym: str, ts_query_last: int) -> str:
    """Binance-L/S bevorzugt; OKX nur, wenn Binance am Ende nicht frisch ist
    (z.B. Server in gesperrter Region). Nie gemischt -> Perzentil bleibt sauber."""
    ts, _, _ = series(sym, "ls")
    if len(ts) and ts_query_last - ts[-1] <= LS_MAX_AGE_MS:
        return "ls"
    ts_o, _, _ = series(sym, "ls_okx")
    return "ls_okx" if len(ts_o) else "ls"


def funding_kind(sym: str, ts_query_last: int) -> str:
    ts, _, _ = series(sym, "funding")
    if len(ts) and ts_query_last - ts[-1] <= FUNDING_MAX_AGE_MS:
        return "funding"
    ts_o, _, _ = series(sym, "funding_okx")
    return "funding_okx" if len(ts_o) else "funding"


def setup_arrays(sym: str, ts_close: np.ndarray, funding_days: float,
                 ls_days: float) -> Dict[str, np.ndarray]:
    """Je Kerze: Funding-/L/S-Rohwert und Perzentil (NaN = keine Daten)."""
    last = int(ts_close[-1]) if len(ts_close) else 0
    fk, lk = funding_kind(sym, last), ls_kind(sym, last)
    f_ts, f_val, f_pct = _pct_cached(sym, fk, funding_days)
    l_ts, l_val, l_pct = _pct_cached(sym, lk, ls_days)
    return {
        "funding": asof(f_ts, f_val, ts_close, FUNDING_MAX_AGE_MS),
        "funding_pct": asof(f_ts, f_pct, ts_close, FUNDING_MAX_AGE_MS),
        "ls": asof(l_ts, l_val, ts_close, LS_MAX_AGE_MS),
        "ls_pct": asof(l_ts, l_pct, ts_close, LS_MAX_AGE_MS),
        "funding_source": fk, "ls_source": lk,
    }


# ------------------------------------------------------------------ Archiv (sync)
def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, url.split("/um/", 1)[-1].replace("/", "_"))


def _download(url: str) -> Optional[bytes]:
    path = _cache_path(url)
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        return data or None           # leere Datei = bekannter 404
    try:
        r = requests.get(url, timeout=20)
    except requests.RequestException as e:
        logger.debug(f"positioning archive {url}: {e}")
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    if r.status_code == 404:
        open(path, "wb").close()
        return None
    if r.status_code != 200:
        return None
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
    with open(tmp, "wb") as f:
        f.write(r.content)
    os.replace(tmp, path)          # atomar: parallele Prozesse lesen nie halbe Dateien
    return r.content


def _csv_rows(blob: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = z.namelist()[0]
            text = z.read(name).decode("utf-8", "ignore")
    except (zipfile.BadZipFile, IndexError):
        return []
    return list(csv.DictReader(io.StringIO(text)))


def _load_funding_month(sym: str, ym: str):
    blob = _download(f"{ARCHIVE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{ym}.zip")
    if not blob:
        return
    rows = {}
    for r in _csv_rows(blob):
        try:
            rows[int(r["calc_time"])] = float(r["last_funding_rate"])
        except (KeyError, ValueError):
            continue
    _merge(sym, "funding", rows)


def _load_metrics_day(sym: str, day: str):
    blob = _download(f"{ARCHIVE}/daily/metrics/{sym}/{sym}-metrics-{day}.zip")
    if not blob:
        return
    rows = {}
    for r in _csv_rows(blob):
        try:
            t = datetime.strptime(r["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            v = float(r["count_long_short_ratio"])
        except (KeyError, ValueError):
            continue
        if v > 0:
            rows[int(t.timestamp() * 1000)] = v
    _merge(sym, "ls", rows)


def _load_funding_current_month(sym: str, now: datetime):
    """Laufender Monat fehlt im Monats-Archiv -> Binance fapi (falls erreichbar)."""
    start = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    try:
        r = requests.get(f"{BINANCE_FAPI}/fapi/v1/fundingRate",
                         params={"symbol": sym, "startTime": start, "limit": 1000}, timeout=15)
        if r.status_code == 200:
            _merge(sym, "funding", {int(x["fundingTime"]): float(x["fundingRate"]) for x in r.json()})
            _DONE.add(("fl", sym))
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.debug(f"binance funding current month {sym}: {e}")


def ensure_history(sym: str, start_ms: int, end_ms: int, funding_days: float = 90,
                   ls_days: float = 7, workers: int = 8):
    """Archiv-Daten für [start - Lookback, end] laden (idempotent, Disk-Cache)."""
    now = datetime.now(timezone.utc)
    f_start = datetime.fromtimestamp((start_ms - (funding_days + 2) * DAY_MS) / 1000, tz=timezone.utc)
    l_start = datetime.fromtimestamp((start_ms - (ls_days + 1) * DAY_MS) / 1000, tz=timezone.utc)
    end = min(datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc), now)
    jobs = []
    m = f_start.replace(day=1)
    while m <= end:
        key = ("f", sym, m.strftime("%Y-%m"))
        if key not in _DONE and m.strftime("%Y-%m") != now.strftime("%Y-%m"):
            jobs.append((key, _load_funding_month, (sym, key[2])))
        m = (m + timedelta(days=32)).replace(day=1)
    d = l_start.date()
    while d <= end.date() and d < now.date():
        key = ("m", sym, d.isoformat())
        if key not in _DONE:
            jobs.append((key, _load_metrics_day, (sym, key[2])))
        d += timedelta(days=1)
    if end.strftime("%Y-%m") == now.strftime("%Y-%m") and ("fl", sym) not in _DONE:
        _load_funding_current_month(sym, now)
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda j: j[1](*j[2]), jobs))
    for key, _, _ in jobs:
        # die letzten 2 Tage können später noch erscheinen -> nicht als erledigt merken
        if key[0] == "f" or key[2] < (now.date() - timedelta(days=2)).isoformat():
            _DONE.add(key)


def ensure_for_candles(sym: str, ts: np.ndarray, funding_days: float, ls_days: float):
    if len(ts):
        ensure_history(sym, int(ts[0]), int(ts[-1]), funding_days, ls_days)


# ------------------------------------------------------------------ Live (async)
async def _get(session, url, params):
    import aiohttp
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return await r.json(content_type=None)


def _okx_inst(sym: str) -> str:
    return f"{sym[:-4]}-USDT-SWAP" if sym.endswith("USDT") else sym


async def refresh_live(session, sym: str) -> Dict:
    """Aktuellste Funding-Settlements + 5m-L/S nachladen. Binance zuerst, OKX Fallback."""
    st = {"funding": None, "ls": None}
    try:
        d = await _get(session, f"{BINANCE_FAPI}/fapi/v1/fundingRate", {"symbol": sym, "limit": 1000})
        _merge(sym, "funding", {int(x["fundingTime"]): float(x["fundingRate"]) for x in d})
        st["funding"] = "binance"
    except Exception as e:  # noqa: BLE001
        logger.debug(f"binance funding {sym}: {e}")
    try:
        d = await _get(session, f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
                       {"symbol": sym, "period": "5m", "limit": 500})
        _merge(sym, "ls", {int(x["timestamp"]): float(x["longShortRatio"]) for x in d})
        st["ls"] = "binance"
    except Exception as e:  # noqa: BLE001
        logger.debug(f"binance ls {sym}: {e}")
    inst = _okx_inst(sym)
    have_f = len(series(sym, "funding_okx")[0]) > 0
    have_l = len(series(sym, "ls_okx")[0]) > 0
    if st["funding"] is None:
        try:
            rows, after = {}, None
            for _ in range(1 if have_f else 3):
                p = {"instId": inst, "limit": 100, **({"after": after} if after else {})}
                d = (await _get(session, f"{OKX}/public/funding-rate-history", p)).get("data") or []
                if not d:
                    break
                rows.update({int(x["fundingTime"]): float(x["realizedRate"] or x["fundingRate"]) for x in d})
                after = d[-1]["fundingTime"]
            _merge(sym, "funding_okx", rows)
            st["funding"] = "okx" if rows else None
        except Exception as e:  # noqa: BLE001
            logger.debug(f"okx funding {sym}: {e}")
    if st["ls"] is None:
        try:
            rows, end = {}, None
            for _ in range(1 if have_l else 15):
                p = {"instId": inst, "period": "5m", "limit": 100, **({"end": end} if end else {})}
                d = (await _get(session, f"{OKX}/rubik/stat/contracts/long-short-account-ratio-contract",
                                p)).get("data") or []
                if not d:
                    break
                rows.update({int(x[0]): float(x[1]) for x in d})
                end = str(int(d[-1][0]) - 1)
                await asyncio.sleep(0.25)
            _merge(sym, "ls_okx", rows)
            st["ls"] = "okx" if rows else None
        except Exception as e:  # noqa: BLE001
            logger.debug(f"okx ls {sym}: {e}")
    st["ts"] = int(time.time() * 1000)
    _LIVE_STATE[sym] = st
    return st


async def live_loop(get_symbols: Callable[[], List[str]], interval_sec: int = 60,
                    funding_days: float = 90, ls_days: float = 7):
    """Hält Funding/L/S für die Live-Scanner-Symbole aktuell (nur Symbole, deren
    aktive Strategien Positionierung brauchen – get_symbols entscheidet)."""
    import aiohttp
    last_archive = 0.0
    while True:
        try:
            syms = list(get_symbols() or [])
            if syms:
                if time.time() - last_archive > 6 * 3600:
                    now_ms = int(time.time() * 1000)
                    for s in syms:
                        await asyncio.to_thread(ensure_history, s, now_ms - DAY_MS, now_ms,
                                                funding_days, ls_days, 4)
                    last_archive = time.time()
                async with aiohttp.ClientSession() as session:
                    for s in syms:
                        await refresh_live(session, s)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"positioning live_loop: {e}")
        await asyncio.sleep(interval_sec)
