"""Lokale Ausführung: Worker-Registry, Job-Queue und Ergebnis-Übernahme.

Der lokale Worker (local_worker/worker.py) nutzt exakt dieselben Module
(services.backtester / services.optimizer / services.candle_cache) und
verbindet sich per Outbound-Polling (keine Portfreigaben nötig).

Design-Prinzip: Lokale Jobs leben weiterhin in bt.JOBS / opt.JOBS. Dadurch
funktionieren alle bestehenden Status-/Active-/Cancel-/Equity-/Export-/
Apply-Endpoints und die komplette UI unverändert – nur die Berechnung
findet auf dem lokalen Rechner statt.
"""
import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services import backtester as bt
from services import job_control
from services import optimizer as opt
from services import regime_lab as rlab

logger = logging.getLogger(__name__)

WORKER_TIMEOUT = 8         # Sekunden ohne Heartbeat -> offline (Worker pollt alle ~2s;
                           # Rechenlast läuft im Worker in eigenem Thread und
                           # blockiert das Polling nicht)
QUEUED_TIMEOUT = 300       # Job wartet ohne Worker -> Fehler (kurze Reconnects tolerieren)
OFFLINE_JOB_TIMEOUT = 6 * 3600  # laufender Job + Worker offline: so lange darf die
                           # Verbindung abreißen (Render-Neustart, Read timeout, WLAN),
                           # bevor der Job als Fehler endet. Der Worker rechnet
                           # währenddessen weiter (auch pausiert) und lädt das Ergebnis
                           # nach der Wiederverbindung hoch. Vorher 180 s: Endlos-Suchen
                           # starben nach kurzen Aussetzern "bei 10 %" (Bug-Report 09/2026).
OFFLINE_HINT_AFTER = 30    # ab so vielen Sekunden offline: Hinweis in der Phase anzeigen
CONN_LOST_MSG = "Verbindung zum lokalen Worker verloren"
CANCEL_GRACE = 3           # Abbruch angefordert, Worker bestätigt nicht -> hart abbrechen
MAX_RESULT_TRADES = 50000  # wie Cloud-Persistierung

DEFAULT_SETTINGS = {
    "cpu_cores": 0,             # 0 = alle Kerne
    "ram_limit_mb": 0,          # Kerzen-RAM-Cache des Workers; 0 = automatisch (gesamter RAM minus 1 GB)
    "use_gpu": False,           # GPU (NVIDIA/CuPy) für Indikator-Vorberechnung
    "max_parallel_jobs": 1,     # gleichzeitige Rechen-Jobs auf dem Worker
    "data_dir": "",             # leer = Standardordner des Workers (globaler Fallback)
    "data_dirs": {},            # worker_id -> eigener Daten-Ordner (pro Worker!)
    "auto_update_enabled": False,
    "auto_update_minutes": 60,
}

WORKERS: Dict[str, Dict] = {}
COMPUTE_QUEUE: List[Dict] = []          # {"job_id","kind","payload"}
LOCAL_JOBS: Dict[str, Dict] = {}        # job_id -> {"kind","state","enqueued_at","last_update","worker_id"}
DATA_JOBS: Dict[str, Dict] = {}         # Daten-Jobs (Download/Update/Löschen)
DATA_QUEUE: List[str] = []
_watchdog_task = None
_settings_cache: Optional[Dict] = None
_token_cache: Optional[str] = None


def _now() -> float:
    return time.time()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- AP11: Auftrag-/Ergebnis-Provenienz (rein) ----------------
def canonical_hash(obj) -> str:
    """Deterministischer Kurz-Hash über beliebige JSON-fähige Strukturen
    (kanonisches JSON, sortierte Keys). Nicht-serialisierbares fällt auf
    default=str zurück – gleiche Inputs ⇒ gleicher Hash."""
    try:
        blob = json.dumps(obj, sort_keys=True, default=str,
                          separators=(",", ":"))
    except (TypeError, ValueError):
        blob = repr(obj)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def payload_input_hash(payload: Dict) -> str:
    """Input-Hash eines Rechenauftrags. Der reservierte Transportschlüssel
    `_input_hash` wird ausgeschlossen (Hash bleibt stabil, nachdem er dem
    Payload beigelegt wurde)."""
    clean = {k: v for k, v in (payload or {}).items() if k != "_input_hash"}
    return canonical_hash(clean)


# ---------------- Worker-Registry ----------------
REQUIRED_WORKER_VERSION = (1, 13, 0)
REQUIRED_WORKER_VERSION_STR = "1.13.0"
LOST_JOB_GRACE = 20        # Sekunden nach dem Claim, bevor ein vom Worker nicht mehr
                           # gemeldeter Job als verloren gilt (Worker-Neustart)
LOST_JOB_POLLS = 3         # ... und so viele Heartbeats in Folge ohne den Job


def _ver(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v or "0").split("."))
    except ValueError:
        return (0,)


def worker_online() -> bool:
    return any(_now() - w.get("last_seen", 0) < WORKER_TIMEOUT for w in WORKERS.values())


def worker_supports_dynamic() -> bool:
    """Dynamik-Modus braucht Worker >= 1.3.0 (sonst würde er den Job als
    Discovery interpretieren und ein unbrauchbares Ergebnis liefern)."""
    for w in WORKERS.values():
        if _now() - w.get("last_seen", 0) >= WORKER_TIMEOUT:
            continue
        try:
            parts = tuple(int(x) for x in str(w.get("version") or "0").split("."))
            if parts >= (1, 3):
                return True
        except ValueError:
            continue
    return False


def worker_supports_regime_lab() -> bool:
    """Regime-Lab-Jobs braucht Worker >= 1.5.0 (ältere Worker kennen den
    Job-Typ nicht und würden ihn ignorieren)."""
    for w in WORKERS.values():
        if _now() - w.get("last_seen", 0) >= WORKER_TIMEOUT:
            continue
        if _ver(w.get("version")) >= (1, 3):
            return True
    return False


FN_MIN_VERSION = {"calibrate": (1, 7), "autopilot": (1, 12), "ablation": (1, 14), "reevaluate": (1, 15)}


def worker_supports_fn(fn: str) -> bool:
    """Kennt ein verbundener Worker den Regime-Lab-Job `fn`?"""
    need = FN_MIN_VERSION.get(fn, (1, 5))
    return any(_now() - w.get("last_seen", 0) < WORKER_TIMEOUT and _ver(w.get("version")) >= need
               for w in WORKERS.values())


def worker_supports_explore() -> bool:
    """Endlos-Suche (Optimizer mode='explore') braucht Worker >= 1.9.0
    (sanfter Stop via 'stop'-Flag + deep_explore-Modul im Paket)."""
    for w in WORKERS.values():
        if _now() - w.get("last_seen", 0) >= WORKER_TIMEOUT:
            continue
        if _ver(w.get("version")) >= (1, 9):
            return True
    return False


def worker_supports_autopilot() -> bool:
    """Regime-Autopilot (Endlos-Suche der Regime-Erkennung) braucht Worker
    >= 1.12.0 (regime_autopilot-Modul + fn='autopilot' im Paket)."""
    for w in WORKERS.values():
        if _now() - w.get("last_seen", 0) >= WORKER_TIMEOUT:
            continue
        if _ver(w.get("version")) >= (1, 12):
            return True
    return False


def heartbeat(worker_id: str, body: Dict):
    w = WORKERS.setdefault(worker_id, {})
    for k in ("name", "version", "resources", "gpu", "data", "running_jobs", "sim_workers"):
        if body.get(k) is not None:
            w[k] = body[k]
    w["last_seen"] = _now()
    w["last_seen_iso"] = _iso()
    # Der Worker meldet im Heartbeat, welche Jobs bei ihm noch rechnen: das
    # zählt als Lebenszeichen des Jobs – auch wenn einzelne Fortschritts-
    # Meldungen verloren gehen (Render-Timeouts), stirbt der Job nicht
    # fälschlich als "Verbindung verloren" und der Offline-Hinweis verschwindet.
    for jid in body.get("running_jobs") or []:
        meta = LOCAL_JOBS.get(str(jid))
        if meta and meta.get("state") == "claimed":
            meta["worker_alive_at"] = _now()
            meta["missing_polls"] = 0
            if not meta.get("worker_id"):
                meta["worker_id"] = worker_id  # nach Server-Neustart wiederhergestellt
            if meta.get("offline_hint"):
                job = _get_job(str(jid), meta["kind"])
                if job is not None:
                    _clear_offline_hint(meta, job)
    if "running_jobs" in body:
        _detect_lost_jobs(worker_id, {str(j) for j in (body.get("running_jobs") or [])})
    if len(WORKERS) > 5:  # alte Worker-Einträge aufräumen
        for wid in sorted(WORKERS, key=lambda x: WORKERS[x].get("last_seen", 0))[:-5]:
            WORKERS.pop(wid, None)


def _detect_lost_jobs(worker_id: str, running: set):
    """Worker-Neustart erkennen: derselbe Worker meldet sich, listet aber einen
    ihm zugeteilten Job nicht mehr als laufend -> der Job ist im Worker-RAM
    verloren. Vorher blieb der Balken bis zu 6 h stehen („hing bei 10 %“).
    Liegt der Auftrag noch vor, wird er automatisch neu eingereiht; sonst
    endet er mit klarer Fehlermeldung statt endlosem Warten."""
    for jid, meta in list(LOCAL_JOBS.items()):
        if meta.get("state") != "claimed":
            continue
        owner = meta.get("worker_id")
        if owner is None:
            # nach Server-Neustart wiederhergestellt, kein Worker hat den Job
            # bisher als laufend gemeldet: nur eindeutig, wenn genau ein Worker online ist
            online = [w for w, v in WORKERS.items() if _now() - v.get("last_seen", 0) < WORKER_TIMEOUT]
            if online != [worker_id]:
                continue
        elif owner != worker_id:
            continue
        if jid in running or _now() - meta.get("claimed_at", meta.get("enqueued_at", 0)) < LOST_JOB_GRACE:
            continue
        meta["missing_polls"] = int(meta.get("missing_polls") or 0) + 1
        if meta["missing_polls"] < LOST_JOB_POLLS:
            continue
        job = _get_job(jid, meta["kind"])
        if job is None or job.get("status") not in ("running", "queued"):
            LOCAL_JOBS.pop(jid, None)
            continue
        item = meta.get("item")
        if item is not None and not meta.get("requeued"):
            meta.update({"state": "queued", "worker_id": None, "enqueued_at": _now(),
                         "last_update": _now(), "missing_polls": 0, "requeued": True})
            meta.pop("claimed_at", None)
            COMPUTE_QUEUE.append(item)
            job["phase"] = "Worker wurde neu gestartet – Job wird erneut ausgeführt..."
            job["progress"] = 0
            logger.warning(f"local_exec: Job {jid} auf Worker {worker_id} verloren "
                           "(Neustart) – automatisch neu eingereiht")
        else:
            _mark_error(job, "Der lokale Worker wurde neu gestartet, während dieser Job lief – "
                             "die Berechnung ist verloren. Bitte den Job neu starten.")
            LOCAL_JOBS.pop(jid, None)
            logger.warning(f"local_exec: Job {jid} auf Worker {worker_id} verloren – Fehler")


def workers_public() -> List[Dict]:
    # Worker, die sich seit >30 min nicht gemeldet haben, verschwinden aus der
    # Liste – sonst hängt eine alte Sitzung als Phantom-Eintrag im UI.
    for wid in [w for w, v in WORKERS.items()
                if _now() - v.get("last_seen", 0) > 1800]:
        WORKERS.pop(wid, None)
    out = []
    for wid, w in WORKERS.items():
        out.append({
            "worker_id": wid, "name": w.get("name"), "version": w.get("version"),
            "online": _now() - w.get("last_seen", 0) < WORKER_TIMEOUT,
            "last_seen": w.get("last_seen_iso"),
            "resources": w.get("resources") or {},
            "gpu": w.get("gpu") or {},
            "data": w.get("data") or {},
            "sim_workers": w.get("sim_workers"),
            "running_jobs": w.get("running_jobs") or [],
            "outdated": _ver(w.get("version")) < REQUIRED_WORKER_VERSION,
            "data_dir_override": ((_settings_cache or {}).get("data_dirs")
                                  or {}).get(wid),
        })
    out.sort(key=lambda x: (not x["online"], x.get("last_seen") or ""), reverse=False)
    return out


# ---------------- Einstellungen & Token (Mongo-persistiert) ----------------
async def get_settings(db) -> Dict:
    global _settings_cache
    if _settings_cache is None:
        doc = None
        if db is not None:
            doc = await db.settings.find_one({"_id": "local_worker_settings"})
        _settings_cache = {**DEFAULT_SETTINGS,
                           **{k: v for k, v in (doc or {}).items() if k in DEFAULT_SETTINGS}}
    return _settings_cache


async def save_settings(db, patch: Dict) -> Dict:
    cur = dict(await get_settings(db))
    for k, v in (patch or {}).items():
        if k in DEFAULT_SETTINGS:
            cur[k] = v
    try:
        cur["cpu_cores"] = min(max(int(cur.get("cpu_cores") or 0), 0), 128)
        ram = int(cur.get("ram_limit_mb") or 0)
        cur["ram_limit_mb"] = 0 if ram <= 0 else min(max(ram, 512), 262144)
        cur["max_parallel_jobs"] = min(max(int(cur.get("max_parallel_jobs") or 1), 1), 8)
        cur["auto_update_minutes"] = min(max(int(cur.get("auto_update_minutes") or 60), 5), 1440)
        cur["use_gpu"] = bool(cur.get("use_gpu"))
        cur["auto_update_enabled"] = bool(cur.get("auto_update_enabled"))
        cur["data_dir"] = str(cur.get("data_dir") or "")
        dd = cur.get("data_dirs")
        cur["data_dirs"] = ({str(k): str(v).strip() for k, v in dd.items()
                             if str(v or "").strip()} if isinstance(dd, dict) else {})
    except (TypeError, ValueError):
        raise ValueError("Ungültige Einstellungswerte")
    global _settings_cache
    _settings_cache = cur
    if db is not None:
        await db.settings.update_one({"_id": "local_worker_settings"},
                                     {"$set": cur}, upsert=True)
    return cur


async def get_settings_for_worker(db, worker_id: str) -> Dict:
    """Settings mit worker-spezifischem Daten-Ordner: jeder Worker (z.B. eigener
    PC + PC des Kumpels) bekommt SEINEN Pfad aus data_dirs[worker_id] statt
    des zuletzt global gespeicherten Pfads des jeweils anderen."""
    base = await get_settings(db)
    out = {k: v for k, v in base.items() if k != "data_dirs"}
    out["data_dir"] = ((base.get("data_dirs") or {}).get(worker_id)
                       or base.get("data_dir") or "")
    return out


async def set_worker_data_dir(db, worker_id: str, path: str) -> Dict:
    """Daten-Ordner EINES Workers setzen (leer = Eintrag entfernen)."""
    cur = dict((await get_settings(db)).get("data_dirs") or {})
    path = str(path or "").strip()
    if path:
        cur[worker_id] = path
    else:
        cur.pop(worker_id, None)
    return await save_settings(db, {"data_dirs": cur})


async def get_token(db) -> str:
    global _token_cache
    if _token_cache:
        return _token_cache
    token = secrets.token_hex(24)
    if db is None:
        # Ohne DB NICHT cachen: ein zufälliges Token würde sonst dauerhaft
        # im Prozess kleben und alle Worker mit dem echten Token aussperren.
        return token
    # Atomar: existiert schon ein Token, gewinnt IMMER das aus Mongo –
    # verhindert Desync, wenn mehrere Prozesse/Instanzen gleichzeitig starten.
    from pymongo import ReturnDocument
    doc = await db.settings.find_one_and_update(
        {"_id": "local_worker_token"},
        {"$setOnInsert": {"token": token}},
        upsert=True, return_document=ReturnDocument.AFTER)
    _token_cache = (doc or {}).get("token") or token
    return _token_cache


_token_refreshed_at = 0.0


async def refresh_token(db) -> str:
    """Cache verwerfen und Token frisch aus Mongo lesen (max. alle 3s).

    Heilt Cache-Desync nach Deploys/Neustarts oder wenn ein anderer
    Prozess/eine andere Instanz das Token regeneriert hat – der Worker
    bekam sonst dauerhaft 401, obwohl sein Token in Mongo gültig war."""
    global _token_cache, _token_refreshed_at
    now = time.time()
    if now - _token_refreshed_at < 3:
        return await get_token(db)
    _token_refreshed_at = now
    _token_cache = None
    return await get_token(db)


async def regenerate_token(db) -> str:
    global _token_cache
    token = secrets.token_hex(24)
    if db is not None:
        await db.settings.update_one({"_id": "local_worker_token"},
                                     {"$set": {"token": token}}, upsert=True)
    _token_cache = token
    return token


# ---------------- Job-Queue ----------------
def _get_job(job_id: str, kind: str) -> Optional[Dict]:
    if kind == "backtest":
        return bt.JOBS.get(job_id)
    if kind == "optimizer":
        return opt.JOBS.get(job_id)
    if kind == "regime_lab":
        return rlab.JOBS.get(job_id)
    return DATA_JOBS.get(job_id)


# ---------------- Neustart-Resilienz (Render 512 MB / Deploys) ----------------
# Lokale Rechen-Jobs leben in In-Memory-Dicts. Stirbt der Server (OOM/Deploy),
# rechnete der Worker bisher ins Leere: Status-Polls liefen auf 404 (UI-Balken
# flackerte weg) und das fertige Ergebnis wurde verworfen. Daher wird ein
# minimales Job-Meta in Mongo gespiegelt (db.local_jobs) und bei Bedarf
# wiederhergestellt – Berechnungen überleben so jeden Server-Neustart.
_KIND_STORES = {"backtest": lambda: bt.JOBS, "optimizer": lambda: opt.JOBS,
                "regime_lab": lambda: rlab.JOBS}


def _bg_task(coro):
    try:
        asyncio.get_event_loop().create_task(coro)
    except RuntimeError:
        pass  # kein Event-Loop (Tests)


def _persist_meta_bg(job_id: str, kind: str, params: Optional[Dict]):
    from core import state
    db = state.db
    if db is None or kind not in _KIND_STORES:
        return

    async def _write():
        try:
            await db.local_jobs.update_one(
                {"_id": job_id},
                {"$set": {"kind": kind, "params": params or {},
                          "created_at": _iso(), "updated_at": _iso()}},
                upsert=True)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"local_jobs persist failed {job_id}: {e}")
    _bg_task(_write())


def _persist_worker_bg(job_id: str, worker_id: str):
    """Zuständigen Worker in Mongo merken: nach einem Server-Neustart weiß der
    wiederhergestellte Job, welcher Worker ihn rechnet (Verlust-Erkennung)."""
    from core import state
    db = state.db
    if db is None:
        return

    async def _write():
        try:
            await db.local_jobs.update_one({"_id": job_id},
                                           {"$set": {"worker_id": worker_id}})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"local_jobs worker persist failed {job_id}: {e}")
    _bg_task(_write())


def _delete_meta_bg(job_id: str):
    from core import state
    db = state.db
    if db is None:
        return

    async def _delete():
        try:
            await db.local_jobs.delete_one({"_id": job_id})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"local_jobs delete failed {job_id}: {e}")
    _bg_task(_delete())


async def restore_job(db, job_id: str) -> Optional[Dict]:
    """Lokalen Job nach Server-Neustart aus db.local_jobs rekonstruieren.
    Gibt das LOCAL_JOBS-Meta zurück (oder None, wenn unbekannt)."""
    if db is None:
        return None
    try:
        doc = await db.local_jobs.find_one({"_id": job_id})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"local_exec restore lookup failed: {e}")
        return None
    if not doc:
        return None
    kind = doc.get("kind")
    store_fn = _KIND_STORES.get(kind)
    if store_fn is None:
        return None
    store = store_fn()
    if job_id not in store:
        store[job_id] = {"id": job_id, "status": "running",
                         "progress": round(float(doc.get("progress") or 0), 1),
                         "phase": "Läuft auf lokalem Worker...",
                         "params": doc.get("params") or {}, "best": None,
                         "cancel": False, "pause": bool(doc.get("pause")),
                         "created_at": doc.get("created_at") or _iso(),
                         "result": None, "error": None, "execution": "local"}
    meta = LOCAL_JOBS.setdefault(
        job_id, {"kind": kind, "state": "claimed", "enqueued_at": _now(),
                 "worker_id": doc.get("worker_id"), "last_update": _now()})
    ensure_watchdog()
    logger.info(f"local_exec: Job {job_id} ({kind}) nach Server-Neustart wiederhergestellt")
    return meta


async def restore_running_jobs(db, kind: Optional[str] = None) -> int:
    """Alle (oder alle eines Typs) offenen lokalen Jobs wiederherstellen –
    für die /active-Endpoints, damit die UI nach einem Neustart den
    laufenden Balken wiederfindet."""
    if db is None:
        return 0
    n = 0
    try:
        q = {"kind": kind} if kind else {}
        async for doc in db.local_jobs.find(q).limit(10):
            jid = doc.get("_id")
            if not jid or jid in LOCAL_JOBS:
                continue
            # Nur "frische" Jobs wiederbeleben: der Worker meldet Fortschritt
            # ~alle 30s in updated_at – ältere Einträge sind verwaist und
            # würden nur als Geister-Job neue Läufe blockieren.
            upd = doc.get("updated_at") or doc.get("created_at") or ""
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(upd)).total_seconds()
            except (ValueError, TypeError):
                age = 1e9
            if age > 3600:
                _delete_meta_bg(jid)
                continue
            if await restore_job(db, jid):
                n += 1
    except Exception as e:  # noqa: BLE001
        logger.warning(f"local_exec restore_running_jobs failed: {e}")
    return n


def enqueue_compute(kind: str, job_id: str, payload: Dict):
    """Rechen-Job (Backtest/Optimizer) für den lokalen Worker einreihen.
    Der Job existiert bereits in bt.JOBS/opt.JOBS (Status 'running')."""
    # AP11: Input-Hash VOR dem Versand berechnen und dem Auftrag beilegen –
    # der Worker echot ihn im Ergebnis, der Server prüft die Zuordnung.
    ih = payload_input_hash(payload)
    if isinstance(payload, dict):
        payload["_input_hash"] = ih
    job = _get_job(job_id, kind)
    if job is not None:
        job["execution"] = "local"
        job["phase"] = "Wartet auf lokalen Worker..."
        job["input_hash"] = ih
    COMPUTE_QUEUE.append({"job_id": job_id, "kind": kind, "payload": payload})
    LOCAL_JOBS[job_id] = {"kind": kind, "state": "queued", "enqueued_at": _now(),
                          "last_update": _now(), "worker_id": None,
                          "input_hash": ih}
    # Meta in Mongo spiegeln: überlebt Server-Neustarts (Render OOM/Deploy)
    _persist_meta_bg(job_id, kind, (job or {}).get("params"))
    ensure_watchdog()
    logger.info(f"local_exec: enqueued {kind} job {job_id}")


def create_data_job(kind: str, params: Dict) -> Dict:
    jid = "d" + uuid.uuid4().hex[:11]
    DATA_JOBS[jid] = {"id": jid, "kind": kind, "params": params, "status": "queued",
                      "progress": 0, "phase": "Wartet auf lokalen Worker...",
                      "cancel": False, "created_at": _iso(), "error": None,
                      "summary": None, "execution": "local"}
    DATA_QUEUE.append(jid)
    LOCAL_JOBS[jid] = {"kind": kind, "state": "queued", "enqueued_at": _now(),
                       "last_update": _now(), "worker_id": None}
    if len(DATA_JOBS) > 20:
        for k in [k for k, v in list(DATA_JOBS.items())
                  if v["status"] in ("done", "error", "cancelled")][:-10]:
            DATA_JOBS.pop(k, None)
    ensure_watchdog()
    return DATA_JOBS[jid]


def claim(worker_id: str, want_compute: bool = True, want_data: bool = True) -> Optional[Dict]:
    """Nächsten Job an den Worker vergeben (Daten-Jobs zuerst, sie sind IO-bound)."""
    if want_data:
        while DATA_QUEUE:
            jid = DATA_QUEUE.pop(0)
            dj = DATA_JOBS.get(jid)
            if not dj or dj.get("cancel"):
                if dj:
                    dj["status"] = "cancelled"
                    dj["phase"] = "Abgebrochen"
                LOCAL_JOBS.pop(jid, None)
                continue
            dj["status"] = "running"
            dj["phase"] = "Vom Worker übernommen..."
            meta = LOCAL_JOBS.setdefault(jid, {"kind": dj["kind"], "enqueued_at": _now()})
            meta.update({"state": "claimed", "worker_id": worker_id, "last_update": _now()})
            return {"job_id": jid, "kind": dj["kind"], "payload": dj["params"]}
    if want_compute:
        w_ver = _ver((WORKERS.get(worker_id) or {}).get("version"))
        skipped = []
        while COMPUTE_QUEUE:
            item = COMPUTE_QUEUE.pop(0)
            # Regime-Lab-Jobs nur an Worker vergeben, die den Job-Typ kennen –
            # ein alter Worker würde ihn sonst stumm liegen lassen.
            if item["kind"] == "regime_lab":
                fn = (((item.get("payload") or {}).get("args") or {}).get("fn"))
                need = FN_MIN_VERSION.get(fn, (1, 5))
                if w_ver < need:
                    skipped.append(item)
                    continue
            # Endlos-Suche nur an Worker >= 1.9 (ältere kennen mode='explore' nicht)
            if item["kind"] == "optimizer":
                _mode = (((((item.get("payload") or {}).get("args") or {})
                           .get("body")) or {}).get("mode"))
                if _mode == "explore" and w_ver < (1, 9):
                    skipped.append(item)
                    continue
            job = _get_job(item["job_id"], item["kind"])
            if not job or job.get("status") != "running" or job.get("cancel"):
                if job is not None and job.get("cancel"):
                    job["status"] = "cancelled"
                    job["phase"] = "Abgebrochen"
                LOCAL_JOBS.pop(item["job_id"], None)
                continue
            meta = LOCAL_JOBS.setdefault(item["job_id"],
                                         {"kind": item["kind"], "enqueued_at": _now()})
            meta.update({"state": "claimed", "worker_id": worker_id, "last_update": _now(),
                         "claimed_at": _now(), "missing_polls": 0, "item": item})
            _persist_worker_bg(item["job_id"], worker_id)
            job["phase"] = "Vom lokalen Worker übernommen..."
            COMPUTE_QUEUE[:0] = skipped
            return item
        COMPUTE_QUEUE[:0] = skipped
    return None


def cancel_ids() -> List[str]:
    out = []
    for jid, meta in LOCAL_JOBS.items():
        if meta.get("state") != "claimed":
            continue
        job = _get_job(jid, meta["kind"])
        if job is not None and job.get("cancel"):
            out.append(jid)
    return out


def cancel_data_job(job_id: str) -> bool:
    dj = DATA_JOBS.get(job_id)
    if not dj:
        return False
    dj["cancel"] = True
    if dj["status"] == "queued":
        dj["status"] = "cancelled"
        dj["phase"] = "Abgebrochen"
        if job_id in DATA_QUEUE:
            DATA_QUEUE.remove(job_id)
        LOCAL_JOBS.pop(job_id, None)
    else:
        dj["phase"] = "Wird abgebrochen..."
    return True


# ---------------- Fortschritt & Ergebnis vom Worker ----------------
async def apply_progress(job_id: str, data: Dict, db=None) -> Dict:
    meta = LOCAL_JOBS.get(job_id)
    if not meta:
        # Server neu gestartet? Job aus Mongo wiederherstellen statt den
        # Worker abzubrechen (vorher ging hier die komplette Rechnung verloren).
        meta = await restore_job(db, job_id)
    if not meta:
        return {"cancel": True}  # wirklich unbekannter Job -> Worker soll abbrechen
    job = _get_job(job_id, meta["kind"])
    if job is None:
        LOCAL_JOBS.pop(job_id, None)
        return {"cancel": True}
    _revive_if_conn_lost(job_id, job, meta["kind"])
    _clear_offline_hint(meta, job)
    if isinstance(data.get("progress"), (int, float)):
        job["progress"] = max(0, min(round(float(data["progress"]), 1), 100))
    if data.get("phase"):
        job["phase"] = str(data["phase"])[:200]
    if data.get("best") is not None:
        job["best"] = data["best"]
    job_control.apply_remote_state(job, data)
    meta["last_update"] = _now()
    meta["worker_alive_at"] = _now()
    # Fortschritt sparsam (max. alle 30s) in Mongo spiegeln
    if db is not None and _now() - meta.get("_meta_saved", 0) > 30:
        meta["_meta_saved"] = _now()

        async def _save_progress(p=job.get("progress")):
            try:
                await db.local_jobs.update_one(
                    {"_id": job_id}, {"$set": {"progress": p, "updated_at": _iso()}})
            except Exception:  # noqa: BLE001
                pass
        _bg_task(_save_progress())
    # "stop": sanfter Stop der Endlos-Suche (Suche beenden, Bestes behalten)
    # "pause": Worker hält die Rechnung an (job_control), bis wieder False
    return {"cancel": bool(job.get("cancel")),
            "stop": bool(job.get("stop_explore")),
            "pause": job_control.pause_requested(job)}


async def apply_result(job_id: str, data: Dict, db):
    meta = LOCAL_JOBS.pop(job_id, None)
    if meta is None:
        # Server-Neustart während der Berechnung: Job wiederherstellen,
        # damit das (teuer berechnete) Ergebnis nicht verworfen wird.
        if await restore_job(db, job_id):
            meta = LOCAL_JOBS.pop(job_id, None)
    kind = (meta or {}).get("kind") or data.get("kind")
    status = data.get("status") if data.get("status") in ("done", "error", "cancelled") else "error"
    job = _get_job(job_id, kind) if kind else None
    if job is None:
        logger.warning(f"local_exec: result for unknown job {job_id} ({kind})")
        return
    _revive_if_conn_lost(job_id, job, kind)
    if meta is not None:
        _clear_offline_hint(meta, job)
    if job.get("status") not in ("running", "queued") or job.get("finalizing"):
        # AP11: idempotenter Jobabschluss – Doppel-Upload oder verspätetes
        # Ergebnis eines bereits beendeten Jobs wird verworfen (vorher konnte
        # ein zweiter Upload nach restore_job erneut persistieren).
        logger.info(f"local_exec: duplicate/late result for finished job {job_id} ignored")
        return
    # AP11: Ergebnis muss zum Auftrag gehören – der Worker echot den
    # Input-Hash; Abweichung = verständliche Ablehnung statt "done" mit
    # unbrauchbaren Daten. Alte Worker ohne Echo bleiben zulässig.
    expected_ih = (meta or {}).get("input_hash") or job.get("input_hash")
    echoed_ih = data.get("input_hash")
    if expected_ih and echoed_ih and echoed_ih != expected_ih:
        job["status"] = "error"
        job["error"] = ("Worker-Ergebnis passt nicht zum Auftrag "
                        f"(Input-Hash {echoed_ih} ≠ erwartet {expected_ih}). "
                        "Ergebnis verworfen – Job bitte neu starten.")
        job["phase"] = "Fehler"
        _delete_meta_bg(job_id)
        logger.warning(f"local_exec: input-hash mismatch for job {job_id} – result rejected")
        return
    # Terminal-Status erst NACH der Persistenz setzen: sonst sieht das Frontend
    # „done“, lädt die Liste, bevor die Analyse in Mongo liegt (Liste „eins versetzt“).
    job["status"] = "running" if status == "done" else status
    job["finalizing"] = status == "done"
    job["error"] = data.get("error")
    # Der Worker kennt seinen eigenen Ausführungsmodus nicht -> hier stempeln,
    # damit die Laufzeit-Anzeige korrekt "lokal" statt "cloud" zeigt.
    res = data.get("result")
    if isinstance(res, dict) and isinstance(res.get("benchmark"), dict):
        res["benchmark"]["execution"] = "local"
        wid = (meta or {}).get("worker_id")
        res["benchmark"]["worker_name"] = (WORKERS.get(wid) or {}).get("name") if wid else None
    # AP11: Evidenz-Hash der Antwort – Provenienz (welcher Auftrag, welcher
    # Worker, welcher Ergebnisstand) wandert additiv mit in die Persistenz.
    if status == "done":
        wid = (meta or {}).get("worker_id")
        evidence = {"input_hash": expected_ih or echoed_ih,
                    "result_hash": canonical_hash(res),
                    "worker_id": wid,
                    "worker_version": (WORKERS.get(wid) or {}).get("version") if wid else None,
                    "received_at": _iso()}
        job["evidence"] = evidence
        if isinstance(res, dict):
            res["evidence_hash"] = evidence["result_hash"]
            res["input_hash"] = evidence["input_hash"]
    if status == "done":
        job["progress"] = 100
        job["phase"] = "Speichere Ergebnis…"
    elif status == "cancelled":
        job["phase"] = "Abgebrochen"
    else:
        job["phase"] = "Fehler"

    if kind == "backtest":
        job["result"] = data.get("result")
        rows = data.get("export_trades") or []
        job["export_trades"] = rows[:MAX_RESULT_TRADES]
        if status == "done" and db is not None:
            try:
                await db.backtests.insert_one({"id": job_id, "params": job.get("params"),
                                               "created_at": job.get("created_at"),
                                               "result": job["result"]})
                await db.backtest_trades.insert_one({"job_id": job_id,
                                                     "created_at": job.get("created_at"),
                                                     "rows": rows[:MAX_RESULT_TRADES]})
                # RAM-Schutz (Render 512 MB): Trades sind jetzt in Mongo –
                # In-Memory-Kopie freigeben, Endpoints lesen per DB-Fallback.
                job.pop("export_trades", None)
            except Exception as e:
                logger.warning(f"local backtest persist failed: {e}")
    elif kind == "optimizer":
        job["result"] = data.get("result")
        if data.get("best") is not None:
            job["best"] = data["best"]
        rows_opt = data.get("export_trades") or []
        if rows_opt:
            job["export_trades"] = rows_opt[:25000]
        if status == "done" and db is not None:
            try:
                await db.optimizer_runs.insert_one({"id": job_id, "params": job.get("params"),
                                                    "created_at": job.get("created_at"),
                                                    "result": job["result"]})
                if rows_opt:
                    await db.optimizer_trades.insert_one(
                        {"job_id": job_id, "created_at": job.get("created_at"),
                         "rows": rows_opt[:25000]})
                    # RAM-Schutz: In-Memory-Kopie freigeben (DB-Fallback greift)
                    job.pop("export_trades", None)
            except Exception as e:
                logger.warning(f"local optimizer persist failed: {e}")
            try:
                from services import learning
                await learning.record_run(db, job["result"])
            except Exception as e:  # noqa: BLE001
                logger.warning(f"learning record failed: {e}")
            try:
                if isinstance(job.get("result"), dict) and job["result"].get("explore"):
                    from services import deep_explore
                    await deep_explore.persist_best(db, job["result"])
            except Exception as e:  # noqa: BLE001
                logger.warning(f"explore persist failed: {e}")
    elif kind == "regime_lab":
        job["result"] = data.get("result")
        if status == "done" and db is not None:
            try:
                await rlab.persist_worker_result(db, job_id, job)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"local regime_lab persist failed: {e}")
    else:  # Daten-Jobs
        if data.get("summary") is not None:
            job["summary"] = data["summary"]
    if status == "done":
        job["phase"] = "Fertig (lokal berechnet)"
        job["status"] = status
        job.pop("finalizing", None)
    _delete_meta_bg(job_id)  # Neustart-Meta aufräumen (Job ist beendet)
    logger.info(f"local_exec: job {job_id} ({kind}) finished with status {status}")
    # RAM-Schutz: nach großen Result-Uploads freien Heap sofort ans OS zurückgeben
    try:
        from services import ram_guard
        ram_guard.trim_now()
    except Exception:  # noqa: BLE001
        pass


# ---------------- Watchdog (Geister-Job-Schutz) ----------------
def _mark_error(job: Dict, msg: str, keep_meta: bool = False):
    job["status"] = "error"
    job["error"] = msg
    job["phase"] = "Fehler"
    if keep_meta:
        # Verbindungsverlust: Meta in Mongo behalten – meldet sich der Worker
        # später doch noch (er rechnet weiter), wird der Job wiederbelebt.
        job["_conn_lost"] = True
    elif job.get("id"):
        _delete_meta_bg(job["id"])


OFFLINE_HINT = ("⚠ Verbindung zum lokalen Worker unterbrochen – der Worker "
                "rechnet weiter, Ergebnis kommt nach der Wiederverbindung")


def _set_offline_hint(meta: Dict, job: Dict):
    if meta.get("offline_hint"):
        return
    meta["offline_hint"] = True
    meta["phase_before_offline"] = job.get("phase")
    job["phase"] = OFFLINE_HINT


def _clear_offline_hint(meta: Dict, job: Dict):
    """Worker meldet sich wieder (Heartbeat/Fortschritt/Ergebnis): Hinweis
    sofort entfernen und die letzte echte Phase zurückholen. Vorher blieb
    „Verbindung unterbrochen“ stehen, bis die nächste Phasen-Meldung kam –
    bei ausbleibenden Fortschritts-Meldungen also dauerhaft."""
    if not meta.pop("offline_hint", None):
        return
    before = meta.pop("phase_before_offline", None)
    if job.get("phase") == OFFLINE_HINT:
        job["phase"] = before or "Läuft auf lokalem Worker..."


def _revive_if_conn_lost(job_id: str, job: Dict, kind: Optional[str] = None):
    """Selbstheilung: als 'Verbindung verloren' beendeter Job meldet sich
    wieder (Progress/Result vom Worker) -> zurück auf 'running'."""
    if job.get("status") == "error" and job.get("_conn_lost"):
        job["status"] = "running"
        job["error"] = None
        job.pop("_conn_lost", None)
        LOCAL_JOBS.setdefault(job_id, {"kind": kind, "state": "claimed",
                                       "enqueued_at": _now(), "last_update": _now()})
        logger.info(f"local_exec: Job {job_id} nach Wiederverbindung des Workers fortgesetzt")


def _finalize_cancel(jid: str, job: Dict):
    job["status"] = "cancelled"
    job["phase"] = "Abgebrochen"
    COMPUTE_QUEUE[:] = [i for i in COMPUTE_QUEUE if i["job_id"] != jid]
    if jid in DATA_QUEUE:
        DATA_QUEUE.remove(jid)
    LOCAL_JOBS.pop(jid, None)
    _delete_meta_bg(jid)


def check_stale():
    for jid, meta in list(LOCAL_JOBS.items()):
        job = _get_job(jid, meta["kind"])
        if job is None or job.get("status") not in ("running", "queued"):
            LOCAL_JOBS.pop(jid, None)
            continue
        # ---- Abbruch: wartende Jobs sofort, laufende nach kurzer Frist hart ----
        if job.get("cancel"):
            if meta.get("state") == "queued":
                _finalize_cancel(jid, job)
                continue
            if not meta.get("cancel_at"):
                meta["cancel_at"] = _now()
            elif _now() - meta["cancel_at"] > CANCEL_GRACE:
                _finalize_cancel(jid, job)
                continue
        if meta.get("state") == "queued":
            if _now() - meta.get("enqueued_at", 0) > QUEUED_TIMEOUT and not worker_online():
                _mark_error(job, "Kein lokaler Worker verbunden – Job abgebrochen. "
                                 "Worker starten oder Cloud-Ausführung wählen.")
                COMPUTE_QUEUE[:] = [i for i in COMPUTE_QUEUE if i["job_id"] != jid]
                if jid in DATA_QUEUE:
                    DATA_QUEUE.remove(jid)
                LOCAL_JOBS.pop(jid, None)
        elif meta.get("state") == "claimed":
            w = WORKERS.get(meta.get("worker_id") or "")
            offline = not w or _now() - w.get("last_seen", 0) > WORKER_TIMEOUT
            # Lebenszeichen = letzte Fortschritts-Meldung ODER Heartbeat, in dem
            # der Worker diesen Job als laufend meldet.
            alive_at = max(meta.get("last_update", 0), meta.get("worker_alive_at", 0))
            silent_for = _now() - alive_at
            if silent_for > OFFLINE_JOB_TIMEOUT:
                _mark_error(job, f"{CONN_LOST_MSG} (> {OFFLINE_JOB_TIMEOUT // 3600} h ohne "
                                 "Rückmeldung). Meldet sich der Worker wieder, wird der "
                                 "Job automatisch fortgesetzt.", keep_meta=True)
                LOCAL_JOBS.pop(jid, None)
            elif offline and silent_for > OFFLINE_HINT_AFTER:
                _set_offline_hint(meta, job)
            elif not offline and meta.get("offline_hint"):
                _clear_offline_hint(meta, job)


async def _watchdog_loop():
    while True:
        try:
            check_stale()
        except Exception as e:
            logger.warning(f"local_exec watchdog: {e}")
        await asyncio.sleep(5)


def ensure_watchdog():
    global _watchdog_task
    if _watchdog_task is None or _watchdog_task.done():
        try:
            _watchdog_task = asyncio.get_event_loop().create_task(_watchdog_loop())
        except RuntimeError:
            pass  # kein Event-Loop (z.B. in Tests ohne Server)


# ---------------- Status für die UI ----------------
def data_jobs_public() -> Dict:
    active = next((j for j in DATA_JOBS.values() if j["status"] == "running"), None)
    queued = [DATA_JOBS[j] for j in DATA_QUEUE if j in DATA_JOBS]
    recent = sorted([j for j in DATA_JOBS.values()
                     if j["status"] in ("done", "error", "cancelled")],
                    key=lambda x: x["created_at"], reverse=True)[:5]
    return {"active": active, "queued": queued, "recent": recent}


def queue_public() -> List[Dict]:
    return [{"job_id": i["job_id"], "kind": i["kind"]} for i in COMPUTE_QUEUE]
