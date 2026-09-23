"""Dauerhaftes Kerzen-Archiv in Supabase Storage (Bucket `candle-archive`).

Problem: Der Disk-Cache (/tmp) ist auf Render flüchtig; nach jedem Deploy wird
die komplette Historie neu geladen und alles, was die Quelle nicht mehr liefert
(Bitunix: nur ab Kontrakt-Listing), wäre verloren. Das Archiv hält je Symbol
EINE komprimierte Matrix (`<symbol>.npy.gz`, 1m-Kerzen) – die Historie wächst
damit über die Zeit über das Quellen-Limit hinaus (Audit 03: Bulk-Daten raus aus
Mongo, Supabase als Ablage).

Upload höchstens einmal je UPLOAD_MIN_INTERVAL_S pro Symbol und nur, wenn die
Historie gewachsen ist. Ohne Supabase-Konfiguration ist alles ein No-Op.
"""
import asyncio
import gzip
import io
import logging
import os
import time
from typing import Dict, Optional

import numpy as np

from services import supabase_storage as storage
from services.candles import CandleArray

logger = logging.getLogger(__name__)

BUCKET = os.environ.get("CANDLE_ARCHIVE_BUCKET", "candle-archive")
ENABLED = os.environ.get("CANDLE_ARCHIVE", "1") != "0"
UPLOAD_MIN_INTERVAL_S = 6 * 3600
_LAST: Dict[str, Dict] = {}     # symbol -> {"at": ts, "count": n}


def enabled() -> bool:
    return ENABLED and storage.configured()


def _path(symbol: str) -> str:
    return f"{symbol}.npy.gz"


def encode(candles: CandleArray) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        np.save(gz, candles.matrix(), allow_pickle=False)
    return buf.getvalue()


def decode(data: bytes) -> Optional[CandleArray]:
    try:
        m = np.load(io.BytesIO(gzip.decompress(data)), allow_pickle=False)
    except (OSError, ValueError, EOFError) as e:
        logger.warning(f"candle_archive: decode fehlgeschlagen: {e}")
        return None
    if m.ndim != 2 or not m.shape[0] or m.shape[1] < 6:
        return None
    return CandleArray.from_matrix(m[:, :6])


def upload_due(symbol: str, count: int, now: Optional[float] = None) -> bool:
    """Nur hochladen, wenn gewachsen und Mindestabstand eingehalten (rein, testbar)."""
    last = _LAST.get(symbol)
    if not last:
        return True
    now = now if now is not None else time.time()
    return count > int(last["count"]) and (now - float(last["at"])) >= UPLOAD_MIN_INTERVAL_S


async def download(symbol: str) -> Optional[CandleArray]:
    if not enabled():
        return None
    data = await storage.download(BUCKET, _path(symbol))
    if not data:
        return None
    ca = await asyncio.to_thread(decode, data)
    if ca is not None and len(ca):
        _LAST[symbol] = {"at": time.time(), "count": len(ca)}
        logger.info(f"candle_archive: {symbol} aus Supabase geladen ({len(ca)} Kerzen)")
    return ca


async def upload(symbol: str, candles: CandleArray, force: bool = False) -> bool:
    if not enabled() or candles is None or not len(candles):
        return False
    if not force and not upload_due(symbol, len(candles)):
        return False
    blob = await asyncio.to_thread(encode, candles)
    ok = await storage.ensure_bucket(BUCKET) and await storage.upload(BUCKET, _path(symbol), blob, "application/gzip")
    if ok:
        _LAST[symbol] = {"at": time.time(), "count": len(candles)}
        logger.info(f"candle_archive: {symbol} gesichert ({len(candles)} Kerzen, {len(blob) / 1e6:.1f} MB)")
    return ok


async def list_archive() -> list:
    if not enabled():
        return []
    return await storage.list_objects(BUCKET)
