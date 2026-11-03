"""Lektions-Attribution (PLAN_LEKTIONS_BILANZ, Baustein A) – reine Funktionen.

Die KI nennt je Entscheidung die Lektionen, die sie maßgeblich beeinflusst
haben (`applied_lessons`), und bei einem durch Lektionen blockierten HOLD die
Richtung, die sie ohne diese Lektionen gehandelt hätte (`would_be`).
Attribution läuft über die STABILE Lektions-ID (`les_…`), nie über die
Prompt-Nummer (`no`), weil sich die Reihenfolge mit Gewicht/locked ändert.

Kein DB-Zugriff, kein Netz – unit-testbar. Alle Parser sind fail-open: bei
Unsinn liefern sie leere Attribution und ändern das Entscheidungsverhalten nicht.
"""
from typing import Dict, Iterable, List, Optional

from services import setup_asset_class

SHORT_LEN = 6
MAX_APPLIED = 6

PROMPT_ADDENDUM = (
    "\nLEKTIONS-ATTRIBUTION: Jede Lektion trägt eine Kurz-ID [id:xxxxxx]. Gib je Entscheidung "
    "zusätzlich \"applied_lessons\": [\"xxxxxx\", …] an – NUR die Lektionen, die diese Entscheidung "
    "maßgeblich beeinflusst haben (max. 6, leere Liste erlaubt; auch bei HOLD angeben). "
    "Hat eine Lektion einen sonst gültigen Trade VERHINDERT, ergänze beim HOLD "
    "\"would_be\": {\"action\": \"LONG|SHORT\", \"sl_pct\": …, \"tp1_pct\": …, \"blocked_by\": [\"xxxxxx\"]} "
    "(Richtung/Level, die du OHNE diese Lektionen gehandelt hättest). Ohne blockierende Lektion "
    "KEIN would_be."
)


def short_id(lesson_id: Optional[str]) -> str:
    """`les_3f9a1c2b7d` -> `3f9a1c` (Kurz-ID im Prompt)."""
    s = str(lesson_id or "")
    if s.startswith("les_"):
        s = s[4:]
    return s[:SHORT_LEN]


def short_id_map(lessons: Iterable[Dict]) -> Dict[str, str]:
    """Kurz-ID und Voll-ID -> Voll-ID (beide Schreibweisen der KI werden akzeptiert)."""
    out: Dict[str, str] = {}
    for l in lessons or []:
        lid = str((l or {}).get("id") or "")
        if not lid:
            continue
        out[lid] = lid
        out.setdefault(short_id(lid), lid)
    return out


def _resolve(raw, id_map: Dict[str, str]) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for item in raw:
        key = str(item or "").strip()
        if key.startswith("[id:") and key.endswith("]"):
            key = key[4:-1]
        full = id_map.get(key) or id_map.get(short_id(key))
        if full and full not in out:
            out.append(full)
    return out


def parse_applied(dec_raw: Dict, id_map: Dict[str, str]) -> List[str]:
    """Nur IDs des aktuellen aktiven Bestands; max. MAX_APPLIED, Reihenfolge der KI."""
    return _resolve((dec_raw or {}).get("applied_lessons"), id_map)[:MAX_APPLIED]


def parse_would_be(dec_raw: Dict, asset_class: str, swing: bool,
                   applied: List[str], id_map: Dict[str, str]) -> Optional[Dict]:
    """`would_be` nur bei HOLD mit gültiger Richtung; Level werden wie echte
    Entscheidungen durch `clamp_levels` geführt. `blocked_by` ⊆ applied
    (unbekannte IDs fallen weg; ohne blockierende Lektion -> None)."""
    d = dec_raw or {}
    if str(d.get("action", "HOLD")).upper() != "HOLD":
        return None
    wb = d.get("would_be")
    if not isinstance(wb, dict):
        return None
    action = str(wb.get("action") or "").upper()
    if action not in ("LONG", "SHORT"):
        return None
    blocked = [x for x in _resolve(wb.get("blocked_by"), id_map) if x in applied]
    if not blocked:
        return None
    try:
        sl = float(wb.get("sl_pct") or 0.6)
        tp1 = float(wb.get("tp1_pct") or 0.9)
    except (TypeError, ValueError):
        sl, tp1 = 0.6, 0.9
    lv = setup_asset_class.clamp_levels(asset_class, sl, tp1, max(tp1 * 2, 1.8), swing)
    return {"action": action, "sl_pct": lv["sl_pct"], "tp1_pct": lv["tp1_pct"],
            "blocked_by": blocked[:MAX_APPLIED]}


def attribution_fields(dec_raw: Dict, lessons: Iterable[Dict], asset_class: str,
                       swing: bool) -> Dict:
    """Additive Felder für das `dec`-Dict: leer, wenn die KI nichts geliefert hat
    (dann bleibt das gespeicherte Dokument exakt wie bisher)."""
    id_map = short_id_map(lessons)
    applied = parse_applied(dec_raw, id_map)
    out: Dict = {}
    if applied:
        out["applied_lessons"] = applied
    wb = parse_would_be(dec_raw, asset_class, swing, applied, id_map)
    if wb:
        out["would_be"] = wb
    return out
