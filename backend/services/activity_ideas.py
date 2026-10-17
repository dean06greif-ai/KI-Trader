"""Ideen-Runde des Aktivitäts-Wächters (Stufe 4 nach der Schwellen-Lockerung).

Problem: Sind alle Lockerungs-Stufen ausgereizt und der KI Trader macht
trotzdem zu wenige Trades, ist NICHT die Schwelle das Problem, sondern das
Setup-Angebot (Lücke im Playbook). Reines Weiter-Lockern würde nur
schlechtere Trades erzeugen.

Lösung: Eine LLM-Ideen-Runde (Rolle research_analyst, max. 1x je
idea_gap_hours) bekommt die Evidenz der Flaute – verpasste starke Bewegungen
(Bewegungs-Scanner), die häufigsten HOLD-Begründungen der letzten 48h, das
Playbook und die aktuellen Schwellen – und darf GENAU EINE Aktion vorschlagen:
  * new_setup: neues Datensammel-Setup (ai_playbook.propose_custom_setup –
    Paper/Shadow, Live erst nach Reife-Gate)
  * revise:    bestehendes Setup überarbeiten (ai_playbook.revise_setup)
  * none:      Flaute ist marktbedingt (dokumentieren, nichts ändern)
Alles additiv: bestehende Guards/Reife-Gates bleiben unangetastet.
"""
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ROLE = "research_analyst"
MAX_MOVES = 8
MAX_HOLDS = 25

IDEA_SYSTEM = (
    "Du bist der 'Forschungs-Analyst' im KI-Team einer Daytrading-Plattform. Der KI Trader "
    "macht anhaltend zu WENIGE Trades, obwohl die Konfidenz-Schwellen bereits maximal gelockert "
    "wurden. Deine Aufgabe: aus der Evidenz (verpasste Bewegungen, HOLD-Begründungen, Playbook) "
    "die Lücke im Setup-Angebot finden. Schlage GENAU EINE Aktion vor. Ein neues Setup nur, wenn "
    "ein WIEDERHOLBARES, regelbasiertes Muster erkennbar ist (Entry/Exit/Filter konkret) – es "
    "läuft dann als Datensammel-Setup im Paper-Modus. Ist die Flaute marktbedingt (z.B. enge "
    "Range ohne Vola), antworte mit action 'none'. Antworte AUSSCHLIESSLICH mit validem JSON "
    "ohne Markdown:\n"
    '{"diagnosis": "1-2 Sätze: warum entstehen zu wenige Trades", '
    '"action": "none|new_setup|revise", '
    '"setup_id": "bei revise: bestehende Setup-ID; bei new_setup: neue snake_case-ID (3-24 Zeichen) oder null", '
    '"asset_class": "crypto|indices|resources|forex", '
    '"desc": "bei revise/new_setup: klare Regelbeschreibung (Entry/Exit/Filter, min. 20 Zeichen) oder null", '
    '"trade_target": erwartete Trades/Woche als Zahl oder null, '
    '"reason": "1 Satz Begründung"}'
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_holds(decisions: List[Dict]) -> Dict:
    """HOLD-Entscheidungen verdichten (rein, testbar): Symbole + häufige Begründungs-Fragmente."""
    syms = Counter(str(d.get("symbol") or "?") for d in decisions)
    reasons = [str(d.get("reasoning") or "").strip()[:140] for d in decisions if d.get("reasoning")]
    seen, uniq = set(), []
    for r in reasons:
        key = r[:60].lower()
        if key and key not in seen:
            seen.add(key)
            uniq.append(r)
    return {"count": len(decisions), "symbols": dict(syms.most_common(8)), "reasons": uniq[:10]}


def parse_idea(data, known_setups: Dict[str, str]) -> Optional[Dict]:
    """LLM-Antwort validieren/normalisieren (rein, testbar). None = unbrauchbar."""
    if not isinstance(data, dict) or not data.get("diagnosis"):
        return None
    action = data.get("action") if data.get("action") in ("none", "new_setup", "revise") else "none"
    sid = str(data.get("setup_id") or "").strip().lower()[:30] or None
    desc = str(data.get("desc") or "").strip()[:300] or None
    cls = data.get("asset_class") if data.get("asset_class") in ("crypto", "indices", "resources", "forex") else "crypto"
    if action == "revise" and (not sid or sid not in known_setups or not desc or len(desc) < 20):
        action = "none"
    if action == "new_setup" and (not sid or not desc or len(desc) < 20):
        action = "none"
    tt = None
    try:
        if data.get("trade_target") is not None:
            tt = max(1, min(50, int(float(data["trade_target"]))))
    except (TypeError, ValueError):
        tt = None
    return {"diagnosis": str(data["diagnosis"])[:400], "action": action, "setup_id": sid,
            "asset_class": cls, "desc": desc, "trade_target": tt,
            "reason": str(data.get("reason") or "")[:200]}


async def _gather_evidence(db) -> Dict:
    since_moves = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    since_holds = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    moves = await db.ai_move_events.find(
        {"ts": {"$gte": since_moves}, "caught": False}, {"_id": 0}) \
        .sort("ts", -1).limit(MAX_MOVES).to_list(MAX_MOVES)
    holds = await db.ai_decisions.find(
        {"ts": {"$gte": since_holds}, "action": "HOLD"},
        {"_id": 0, "symbol": 1, "reasoning": 1, "confidence": 1}) \
        .sort("ts", -1).limit(MAX_HOLDS).to_list(MAX_HOLDS)
    return {"moves": moves, "holds": summarize_holds(holds)}


def build_prompt(evidence: Dict, setups: Dict[str, str], thresholds: Dict, rate: float, target: int) -> str:
    move_lines = []
    for m in evidence.get("moves") or []:
        f, a = m.get("features") or {}, m.get("analysis") or {}
        move_lines.append(
            f"- {m.get('symbol')} ({m.get('asset_class')}) {float(f.get('change_60m_pct') or 0):+.2f}%/60m, "
            f"Regime {f.get('regime')}, Vol x{f.get('volume_ratio')}"
            + (f" · Ursache: {a.get('cause')}" if a.get("cause") else "")
            + (f" · verpasst weil: {a.get('missed_reason')}" if a.get("missed_reason") else "")
            + (f" · passendes Setup: {a.get('setup_match')}" if a.get("setup_match") else ""))
    holds = evidence.get("holds") or {}
    setup_lines = "\n".join(f"- {sid}: {desc[:120]}" for sid, desc in list(setups.items())[:30])
    return (
        f"=== FLAUTE ===\nNur {rate} KI-Trades/Tag (Ziel {target}); alle Lockerungs-Stufen ausgereizt.\n"
        f"Aktuelle Schwellen: {thresholds}\n\n"
        f"=== VERPASSTE STARKE BEWEGUNGEN (7 Tage, Bewegungs-Scanner) ===\n"
        + ("\n".join(move_lines) if move_lines else "keine erfasst") + "\n\n"
        f"=== HOLD-ENTSCHEIDUNGEN (48h): {holds.get('count', 0)} ===\n"
        f"Symbole: {holds.get('symbols')}\nHäufige Begründungen:\n"
        + ("\n".join(f"- {r}" for r in holds.get("reasons") or []) or "-") + "\n\n"
        f"=== PLAYBOOK-SETUPS ===\n{setup_lines}\n\n"
        "Finde die Lücke und schlage GENAU EINE Aktion als JSON vor."
    )


async def apply_idea(db, idea: Dict) -> Optional[Dict]:
    from services import ai_playbook
    action = idea["action"]
    if action == "none":
        return None
    try:
        if action == "new_setup":
            res = await ai_playbook.propose_custom_setup(
                db, idea["setup_id"], idea["desc"], source="activity_guard",
                trade_target=idea.get("trade_target"))
            return {"kind": "new_setup", **res}
        res = await ai_playbook.revise_setup(
            db, idea["asset_class"], idea["setup_id"], idea["desc"],
            reason=f"Aktivitäts-Wächter Ideen-Runde: {idea.get('reason') or 'anhaltende Flaute'}",
            source="activity_guard", trade_target=idea.get("trade_target"))
        return {"kind": "revise", **res}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Ideen-Runde Aktion {action}/{idea.get('setup_id')}: {e}")
        return {"kind": action, "status": "error", "reason": str(e)[:150]}


async def run(engine, rate: float, target: int, thresholds: Dict) -> Optional[Dict]:
    """Eine Ideen-Runde ausführen. Gibt das Ergebnis (inkl. angewendeter
    Aktion) zurück oder None, wenn kein LLM-Key / keine brauchbare Antwort."""
    from services import ai_playbook
    if engine is None or not getattr(engine, "key", None):
        return None
    db = engine.db
    setups = ai_playbook.all_setups()
    evidence = await _gather_evidence(db)
    prompt = build_prompt(evidence, setups, thresholds, rate, target)
    text, provider, model = await engine.generate_for_role(ROLE, prompt, IDEA_SYSTEM, temperature=0.3)
    idea = parse_idea(engine._parse_json(text), setups)
    if idea is None:
        return None
    idea["model"] = f"{provider}/{model}"
    idea["at"] = _now_iso()
    idea["evidence"] = {"moves": len(evidence.get("moves") or []),
                        "holds": (evidence.get("holds") or {}).get("count", 0)}
    idea["action_result"] = await apply_idea(db, idea)
    return idea


def chat_text(idea: Dict) -> str:
    lines = [f"💡 Ideen-Runde (Flaute trotz max. Lockerung): {idea['diagnosis']}"]
    ar = idea.get("action_result")
    if idea["action"] == "none":
        lines.append("Einschätzung: marktbedingt – keine Setup-Änderung." + (f" {idea['reason']}" if idea.get("reason") else ""))
    elif ar and ar.get("status") == "ok":
        lines.append(("Neues Datensammel-Setup angelegt: " if ar["kind"] == "new_setup" else "Setup-Revision gestartet: ")
                     + f"{idea['setup_id']} – {idea.get('desc')}")
    elif ar:
        lines.append(f"Vorschlag ({ar.get('kind')}, {idea.get('setup_id')}) nicht angewendet: {ar.get('reason')}")
    return "\n".join(lines)[:1500]
