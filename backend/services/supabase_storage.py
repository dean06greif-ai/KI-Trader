"""Minimaler Supabase-Storage-Client (REST, aiohttp) – gemeinsam genutzt von
services/backup.py (Mongo-Dumps) und services/candle_archive.py (Kerzen-Archiv).

Konfiguration wie das KI-Gedächtnis (services/ai_memory.supabase_config):
SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY. Ohne Konfiguration ist `configured()`
False – Aufrufer verhalten sich dann still (kein Fehler, kein Upload).
"""
import json
import logging
from typing import Dict, List, Optional

import aiohttp

from services.ai_memory import supabase_config

logger = logging.getLogger(__name__)

TIMEOUT_S = 120


def configured() -> bool:
    return supabase_config() is not None


def _base() -> str:
    cfg = supabase_config() or {}
    return str(cfg.get("base") or "").rstrip("/") + "/storage/v1"


def _headers(content_type: Optional[str] = None) -> Dict[str, str]:
    key = (supabase_config() or {}).get("key") or ""
    h = {"Authorization": f"Bearer {key}", "apikey": key}
    if content_type:
        h["Content-Type"] = content_type
    return h


async def ensure_bucket(bucket: str) -> bool:
    """Privaten Bucket anlegen, falls er fehlt (idempotent)."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.get(f"{_base()}/bucket/{bucket}", headers=_headers()) as r:
            if r.status == 200:
                return True
        body = {"id": bucket, "name": bucket, "public": False}
        async with s.post(f"{_base()}/bucket", headers=_headers("application/json"),
                          data=json.dumps(body)) as r:
            if r.status in (200, 201):
                logger.info(f"supabase_storage: Bucket '{bucket}' angelegt")
                return True
            txt = (await r.text())[:200]
            if "already exists" in txt.lower() or r.status == 409:
                return True
            logger.warning(f"supabase_storage: Bucket '{bucket}' anlegen fehlgeschlagen ({r.status}): {txt}")
            return False


async def upload(bucket: str, path: str, data: bytes,
                 content_type: str = "application/octet-stream") -> bool:
    h = _headers(content_type)
    h["x-upsert"] = "true"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as s:
        async with s.post(f"{_base()}/object/{bucket}/{path}", headers=h, data=data) as r:
            if r.status in (200, 201):
                return True
            logger.warning(f"supabase_storage: Upload {bucket}/{path} fehlgeschlagen "
                           f"({r.status}): {(await r.text())[:200]}")
            return False


async def download(bucket: str, path: str) -> Optional[bytes]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as s:
        async with s.get(f"{_base()}/object/{bucket}/{path}", headers=_headers()) as r:
            if r.status == 200:
                return await r.read()
            if r.status not in (400, 404):
                logger.warning(f"supabase_storage: Download {bucket}/{path} ({r.status})")
            return None


async def list_objects(bucket: str, prefix: str = "", limit: int = 1000) -> List[Dict]:
    body = {"prefix": prefix, "limit": limit, "offset": 0,
            "sortBy": {"column": "name", "order": "desc"}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.post(f"{_base()}/object/list/{bucket}", headers=_headers("application/json"),
                          data=json.dumps(body)) as r:
            if r.status != 200:
                logger.warning(f"supabase_storage: list {bucket} ({r.status}): {(await r.text())[:200]}")
                return []
            rows = await r.json(content_type=None)
    out = []
    for o in rows or []:
        meta = o.get("metadata") or {}
        out.append({"name": o.get("name"), "size": int(meta.get("size") or 0),
                    "updated_at": o.get("updated_at") or o.get("created_at")})
    return out


async def delete(bucket: str, paths: List[str]) -> int:
    if not paths:
        return 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        async with s.delete(f"{_base()}/object/{bucket}", headers=_headers("application/json"),
                            data=json.dumps({"prefixes": paths})) as r:
            if r.status != 200:
                logger.warning(f"supabase_storage: delete {bucket} ({r.status}): {(await r.text())[:200]}")
                return 0
            rows = await r.json(content_type=None)
            return len(rows) if isinstance(rows, list) else len(paths)
