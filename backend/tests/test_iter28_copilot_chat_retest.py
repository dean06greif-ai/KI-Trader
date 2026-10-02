"""Iteration 28 – Retest NUR des gefixten Copilot-Chat-Flows.

Fix unter Test:
  * services/strategy_copilot.py: PER_CALL_TIMEOUT=28s, TOTAL_DEADLINE=52s,
    FAST_MODEL_ORDER, user-Message wird erst nach erfolgreichem LLM-Call gespeichert.
"""
import os
import time

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL fehlt")
BASE_URL = base_url.rstrip("/")

CREDS = {"username": "Admin", "password": "Dean06Greif!/Admin"}


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json=CREDS, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"Login fehlgeschlagen {r.status_code}: {r.text[:300]}")
    token = r.json().get("token")
    assert token, "Kein Token in Login-Antwort"
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def _history(client):
    r = client.get(f"{BASE_URL}/api/copilot/history", timeout=30)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    msgs = body if isinstance(body, list) else (body.get("messages") or body.get("history") or [])
    assert isinstance(msgs, list), f"Unerwartete History-Struktur: {str(body)[:200]}"
    return msgs


# --- Modul: /api/copilot/chat (Zeitbudget + JSON-Antwort) ---
def test_copilot_chat_responds_fast_with_json(client):
    payload = {
        "message": "Bewerte kurz: trades=10, wins=6, losses=4, win_rate=60",
        "context": {"panel": "optimizer",
                    "result": {"metrics": {"trades": 10, "wins": 6, "losses": 4,
                                           "win_rate": 60}}},
    }
    t0 = time.monotonic()
    r = client.post(f"{BASE_URL}/api/copilot/chat", json=payload, timeout=90)
    elapsed = time.monotonic() - t0
    print(f"\n[copilot/chat] status={r.status_code} elapsed={elapsed:.1f}s")
    print(f"[copilot/chat] body head: {r.text[:400]}")

    assert r.status_code == 200, f"HTTP {r.status_code} nach {elapsed:.1f}s: {r.text[:300]}"
    assert elapsed < 55, f"Antwort zu langsam: {elapsed:.1f}s (Ingress kappt ~60s)"
    ct = r.headers.get("content-type", "")
    assert "application/json" in ct, f"Kein JSON-Content-Type: {ct}"
    data = r.json()
    reply = data.get("reply") or ""
    assert isinstance(reply, str) and len(reply.strip()) > 10, f"Leere/kurze reply: {reply!r}"
    assert isinstance(data.get("checks", []), list)
    assert data.get("model"), "Kein Modell in Antwort"
    print(f"[copilot/chat] model={data.get('model')} provider={data.get('provider')}")
    print(f"[copilot/chat] reply={reply[:300]}")


# --- Modul: /api/copilot/history (keine NEUEN verwaisten user-Messages) ---
def test_copilot_history_pairs_after_new_chat(client):
    before = len(_history(client))
    r = client.post(f"{BASE_URL}/api/copilot/chat",
                    json={"message": "TEST_iter28: sag nur kurz Hallo."}, timeout=90)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"

    msgs = _history(client)
    assert all("_id" not in m for m in msgs), "MongoDB _id in History-Antwort"
    new = msgs[before:]
    print(f"\n[history] +{len(new)} neue Nachrichten: {[m.get('role') for m in new]}")
    assert len(new) == 2, f"Erwartet genau 1 user+1 assistant, bekam {len(new)}"
    assert new[0].get("role") == "user"
    assert new[1].get("role") == "assistant"
    assert new[1].get("content", "").strip(), "Leere assistant-Antwort gespeichert"

    # Vollstaendige History: Alt-Datensaetze VOR dem Fix (interleaved) nur reporten
    roles = [m.get("role") for m in msgs]
    legacy_orphans = [(i, msgs[i].get("ts")) for i, ro in enumerate(roles)
                      if ro == "user" and (roles[i + 1] if i + 1 < len(roles) else None) != "assistant"]
    if legacy_orphans:
        print(f"[history] INFO Alt-Datensaetze (vor Fix) ohne direkte Antwort: {legacy_orphans}")
