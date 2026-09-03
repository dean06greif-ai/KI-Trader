"""Iteration 36 – unabhängige Verifikation:
  * BUG-FIX: KI-Warnung nur für Team-Modelle (notifications.notify_model_failure)
  * GET /api/ai/sweep-trigger + Config-Keys in /api/ai/status + Admin-Clamps
  * services.sweep_trigger.detect_sweep / SweepBudget (eigene Fälle)
  * AIEngine.run_analysis(only_symbols, trigger) Signatur
  * Lektions-Qualitätsguards (is_sizing_rule, small_sample_n, lessons_text)
  * Regression: /api/health, /api/ai/playbook (13 Setups)
KEINE LLM-Calls, KI bleibt aus.
"""
import asyncio
import os
import sys

import pytest
import requests
from dotenv import dotenv_values, load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

_fe = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or _fe.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")

ADMIN = {"username": "Admin", "password": "Dean06Greif!/Admin"}


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_token(client):
    r = client.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"Admin login failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("token") or r.json().get("access_token")
    if not tok:
        pytest.fail(f"no token in login response: {r.text[:300]}")
    return tok


# ----------------------------------------------------------------- Regression
def test_health(client):
    r = client.get(f"{BASE_URL}/api/health", timeout=30)
    assert r.status_code == 200, r.text[:300]


def test_playbook_13_setups(client):
    r = client.get(f"{BASE_URL}/api/ai/playbook", timeout=30)
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    setups = data.get("setups") if isinstance(data, dict) else data
    assert len(setups) == 13, f"{len(setups or [])} Setups"
    assert "liquidity_sweep" in setups


# ----------------------------------------------------------------- Sweep-API
def test_sweep_trigger_endpoint(client):
    r = client.get(f"{BASE_URL}/api/ai/sweep-trigger", timeout=30)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["enabled"] is True
    assert isinstance(d["used_today"], int) and d["used_today"] == 0
    assert d["daily_cap"] == 20
    assert int(d["cooldown_min"]) == 30
    assert d["recent"] == []


def test_ai_status_contains_sweep_config(client):
    r = client.get(f"{BASE_URL}/api/ai/status", timeout=30)
    assert r.status_code == 200, r.text[:300]
    cfg = r.json().get("config") or {}
    assert cfg.get("sweep_trigger_enabled") is True
    assert cfg.get("sweep_trigger_daily_cap") == 20
    assert int(cfg.get("sweep_trigger_cooldown_min")) == 30
    assert cfg.get("enabled") is False, "KI darf nicht laufen"


def _cfg(client):
    return client.get(f"{BASE_URL}/api/ai/status", timeout=30).json()["config"]


def test_admin_config_clamps_sweep(client, admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    try:
        r = client.post(f"{BASE_URL}/api/ai/config", headers=h, timeout=30,
                        json={"sweep_trigger_daily_cap": 999,
                              "sweep_trigger_cooldown_min": 0,
                              "sweep_trigger_enabled": False})
        assert r.status_code == 200, r.text[:300]
        cfg = _cfg(client)
        assert cfg["sweep_trigger_daily_cap"] == 200, cfg["sweep_trigger_daily_cap"]
        assert int(cfg["sweep_trigger_cooldown_min"]) == 1
        assert cfg["sweep_trigger_enabled"] is False
        # Endpoint spiegelt die Änderung
        st = client.get(f"{BASE_URL}/api/ai/sweep-trigger", timeout=30).json()
        assert st["enabled"] is False and st["daily_cap"] == 200
    finally:
        r = client.post(f"{BASE_URL}/api/ai/config", headers=h, timeout=30,
                        json={"sweep_trigger_daily_cap": 20,
                              "sweep_trigger_cooldown_min": 30,
                              "sweep_trigger_enabled": True})
        assert r.status_code == 200
        cfg = _cfg(client)
        assert cfg["sweep_trigger_daily_cap"] == 20
        assert int(cfg["sweep_trigger_cooldown_min"]) == 30
        assert cfg["sweep_trigger_enabled"] is True


def test_config_update_requires_admin(client):
    r = client.post(f"{BASE_URL}/api/ai/config", timeout=30,
                    json={"sweep_trigger_daily_cap": 5})
    assert r.status_code in (401, 403), f"{r.status_code}: {r.text[:200]}"


# ----------------------------------------------------------------- Detektor (Unit)
def _candles(n=140, price=50000.0, ts0=1_750_000_000_000, step=8.0):
    """Kerzen mit konstanter Range -> stabile ATR."""
    out = []
    for i in range(n):
        up = i % 2 == 0
        o = price - (step / 4 if up else -step / 4)
        c = price + (step / 4 if up else -step / 4)
        out.append({"timestamp": ts0 + i * 60_000, "open": o, "close": c,
                    "high": max(o, c) + step / 2, "low": min(o, c) - step / 2,
                    "volume": 5})
    return out


def test_detect_sweep_own_cases():
    from services import sweep_trigger as st
    cs = _candles()
    atr = st._atr(cs[:-1])
    assert atr > 0
    ref_high = max(float(c["high"]) for c in cs[-61:-1])
    ref_low = min(float(c["low"]) for c in cs[-61:-1])

    # SHORT: Docht 1.5 ATR über ref_high, Close darunter + untere Kerzenhälfte
    cs[-1] = {**cs[-1], "open": 50000.0, "high": ref_high + 1.5 * atr,
              "low": 49990.0, "close": 49992.0}
    hit = st.detect_sweep(cs, lookback=60, min_wick_atr=1.2)
    assert hit and hit["side"] == "SHORT"
    assert hit["level"] == round(ref_high, 8) and hit["wick_atr"] >= 1.2
    assert hit["price"] == 49992.0

    # Genau unter der Schwelle (1.0 ATR) -> kein Sweep
    cs[-1] = {**cs[-1], "high": ref_high + 1.0 * atr}
    assert st.detect_sweep(cs, 60, 1.2) is None

    # Close ÜBER dem Level (echter Breakout) -> kein Sweep
    cs[-1] = {**cs[-1], "high": ref_high + 2 * atr, "close": ref_high + 1.0 * atr,
              "open": ref_high, "low": ref_high - 1}
    assert st.detect_sweep(cs, 60, 1.2) is None

    # LONG: Docht 2 ATR unter ref_low, Close darüber + obere Kerzenhälfte
    cs[-1] = {**cs[-1], "open": 50000.0, "high": 50002.0,
              "low": ref_low - 2 * atr, "close": 50001.0}
    hit = st.detect_sweep(cs, 60, 1.2)
    assert hit and hit["side"] == "LONG" and hit["level"] == round(ref_low, 8)

    # zu wenig Kerzen
    assert st.detect_sweep(cs[:60], 60, 1.2) is None


def test_sweep_budget_own_cases():
    from services import sweep_trigger as st
    b = st.SweepBudget()
    cfg = {"sweep_trigger_enabled": True, "sweep_trigger_daily_cap": 3,
           "sweep_trigger_cooldown_min": 30}
    t0 = 1_760_000_000.0
    assert b.allowed("BTCUSDT", cfg, t0) is None
    b.fire("BTCUSDT", {"side": "SHORT", "level": 1, "wick_atr": 1.5}, t0)
    assert b.used == 1
    # Cooldown aktiv (29 min)
    why = b.allowed("BTCUSDT", cfg, t0 + 29 * 60)
    assert why and "Cooldown" in why
    # nach 31 min frei
    assert b.allowed("BTCUSDT", cfg, t0 + 31 * 60) is None
    # Tagesbudget
    b.fire("ETHUSDT", {"side": "LONG"}, t0 + 60)
    b.fire("SOLUSDT", {"side": "LONG"}, t0 + 120)
    why = b.allowed("XRPUSDT", cfg, t0 + 180)
    assert why and "Tagesbudget" in why
    # Tageswechsel setzt Budget zurück
    assert b.allowed("XRPUSDT", cfg, t0 + 86_400 * 1.5) is None
    s = b.status(cfg)
    assert s["enabled"] is True and s["daily_cap"] == 3
    assert s["recent"][0]["symbol"] == "SOLUSDT"
    # cap 0 -> nichts erlaubt
    assert "Tagesbudget" in b.allowed("A", {**cfg, "sweep_trigger_daily_cap": 0}, t0)


def test_run_analysis_signature():
    import inspect
    from services.ai_engine import AIEngine, DEFAULT_AI_CONFIG
    p = inspect.signature(AIEngine.run_analysis).parameters
    assert "only_symbols" in p and "trigger" in p
    assert DEFAULT_AI_CONFIG["sweep_trigger_enabled"] is True
    assert DEFAULT_AI_CONFIG["sweep_trigger_daily_cap"] == 20
    assert DEFAULT_AI_CONFIG["sweep_trigger_cooldown_min"] == 30


# ----------------------------------------------------------------- Lektions-Guards
def test_lesson_sizing_and_sample_guards():
    from services import ai_lessons as L
    assert L.is_sizing_rule("Marge um 25% reduzieren", "")
    assert L.is_sizing_rule("Vorsicht", "nicht mehr als 20 % der Marge einsetzen")
    assert not L.is_sizing_rule("SHORT nur bei bestätigtem Abwärtstrend", "")
    assert L.small_sample_n("x", "5 entschiedene Trades ... 0% Winrate") == 5
    assert L.small_sample_n("x", "374 entschiedene Trades mit Konfidenz <70%") is None
    lessons = [
        {"id": "s1", "title": "Marge um 25% reduzieren", "detail": "bei Konfidenz <70%",
         "weight": 2},
        {"id": "k1", "title": "SHORT nur bei bestätigtem Abwärtstrend",
         "detail": "Trend prüfen", "weight": 2},
    ]
    txt = L.lessons_text([dict(x) for x in lessons], risk_sizing=True)
    assert txt.count("gegenstandslos") == 1
    sizing_line = [ln for ln in txt.splitlines() if "Marge um 25%" in ln][0]
    other_line = [ln for ln in txt.splitlines() if "Abwärtstrend" in ln][0]
    assert "gegenstandslos" in sizing_line and "gegenstandslos" not in other_line
    assert "gegenstandslos" not in L.lessons_text([dict(x) for x in lessons])


def test_ai_learning_skip_reasons_wired():
    """Lernlauf lehnt Größen-Anweisungen und Mini-Stichproben ab."""
    import inspect
    from services import ai_learning
    src = inspect.getsource(ai_learning)
    assert "is_sizing_rule" in src and "small_sample_n" in src
    assert "Größen-Anweisung" in src or "Groessen-Anweisung" in src
    assert "Datenbasis zu klein" in src


# ----------------------------------------------------------------- BUG-FIX Notify
def test_notify_model_failure_only_team_models():
    """Provider-internes Ersatzmodell darf KEINE Warnung erzeugen; Team-Modell schon.
    Prüft _pending_fail UND die tatsächlich geschriebene app_notifications-Zeile."""
    from motor.motor_asyncio import AsyncIOMotorClient

    from core import state
    from services import notifications as nf
    from services.ai_roles import role_manager

    TEAM_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
    GHOST_MODEL = "nvidia/nemotron-3.5-lightning:free"

    async def run():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"],
                                    serverSelectionTimeoutMS=5000)
        dbname = os.environ["DB_NAME"] + "_iter36_notify"
        db = client[dbname]
        await client.drop_database(dbname)
        old_db, old_role = state.db, role_manager.config.get("analyst")
        old_agg = nf.MODEL_FAILURE_AGGREGATE_S
        state.db = db
        role_manager.config["analyst"] = {
            "enabled": True, "provider": "openrouter", "model": TEAM_MODEL,
            "fallback_provider": "groq", "fallback_model": "openai/gpt-oss-120b"}
        nf._pending_fail.clear()
        nf._last_sent.clear()
        nf._cfg_cache, nf._cfg_ts = None, 0.0
        nf.MODEL_FAILURE_AGGREGATE_S = 0.2
        try:
            # 1) Ersatzmodell aus FALLBACK_ORDER -> keine Warnung
            await nf.notify_model_failure("analyst", "openrouter", GHOST_MODEL,
                                          "error", "leer")
            assert nf._pending_fail == [], f"Ghost-Modell gemeldet: {nf._pending_fail}"
            await asyncio.sleep(0.6)
            assert await db.app_notifications.count_documents({}) == 0

            # 2) Team-Modell -> Warnung landet in der Glocke
            await nf.notify_model_failure("analyst", "openrouter", TEAM_MODEL,
                                          "error", "leer")
            assert len(nf._pending_fail) == 1, "Team-Modell muss melden"
            await asyncio.sleep(1.0)
            rows = await db.app_notifications.find({}, {"_id": 0}).to_list(10)
            assert len(rows) == 1, rows
            assert TEAM_MODEL in rows[0]["message"]
            assert GHOST_MODEL not in rows[0]["message"]
            assert rows[0]["type"] == "ai_failure"

            # 3) Fallback-Modell der Rolle gilt ebenfalls als Team-Modell
            assert nf._is_team_model("analyst", "groq", "openai/gpt-oss-120b")
            assert not nf._is_team_model("analyst", "openrouter", GHOST_MODEL)
        finally:
            nf.MODEL_FAILURE_AGGREGATE_S = old_agg
            nf._pending_fail.clear()
            nf._last_sent.clear()
            await client.drop_database(dbname)
            client.close()
            state.db = old_db
            if old_role is None:
                role_manager.config.pop("analyst", None)
            else:
                role_manager.config["analyst"] = old_role

    asyncio.run(run())


def test_bell_endpoint_has_no_ghost_model_warning(client):
    """Website-Glocke enthält keine Warnung für das Ersatzmodell."""
    r = client.get(f"{BASE_URL}/api/notifications?unread_only=false&limit=50",
                   timeout=30)
    assert r.status_code == 200, r.text[:300]
    rows = r.json().get("notifications", [])
    assert isinstance(rows, list)
    ghosts = [n for n in rows
              if "nemotron-3.5-lightning" in (str(n.get("message", ""))
                                              + str(n.get("meta", "")))]
    assert not ghosts, f"Ghost-Modell-Warnung in der Glocke: {ghosts[:2]}"
