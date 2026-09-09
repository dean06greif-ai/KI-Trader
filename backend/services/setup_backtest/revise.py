"""KI-Revision im Setup-Backtest: der KI-Trader überarbeitet Setups OHNE Edge.

Nach einem Backtest-Durchlauf bekommt die KI (Rolle research_analyst) je
gescheitertem Setup die getesteten Varianten mit In-/Out-of-Sample-Ergebnis
und schlägt EINEN neuen Parameter-Satz (im erlaubten Rahmen der Detektor-
Parameter) plus eine überarbeitete Regelbeschreibung vor.

  * `propose()`   – LLM-Aufruf, Rückgabe eines geprüften Parameter-Satzes
  * `sanitize()`  – reine Prüfung/Klemmung des Vorschlags (testbar)
  * Modus "ai_loop" im Runner testet den Vorschlag sofort (Schleife bis
    Ziel erreicht oder max. Runden), sonst wird er als `ai_proposal` für den
    nächsten Lauf vorgemerkt.
Die Textrevision wird zusätzlich über ai_playbook.revise_setup an den Live-
Prompt gegeben (nur wenn das Setup dort rückgestuft/inaktiv ist – sonst nur Log).
"""
import logging
from typing import Dict, List, Optional, Tuple

from services import setup_asset_class as ac
from services.setup_backtest import detectors

logger = logging.getLogger(__name__)

ROLE = "research_analyst"
MAX_ROUNDS = 10
DEFAULT_ROUNDS = 3
DEFAULT_TARGET = 3
# Erlaubter Bereich je Parameter: [0.5 x Minimum, 2 x Maximum] über alle Varianten
RANGE_LO, RANGE_HI = 0.5, 2.0
INT_KEYS = ("lookback", "lookback_h", "min_bars", "slope_bars", "pivot_k")

SYSTEM = (
    "Du bist der Forschungs-Analyst eines KI-Traders. Du überarbeitest regelbasierte "
    "Trading-Setups, die im Backtest (5m, In-Sample/Out-of-Sample) KEINEN Edge hatten. "
    "Antworte NUR mit JSON: {\"params\": {<key>: <zahl>, ...}, \"desc\": \"überarbeitete "
    "Regelbeschreibung (max. 300 Zeichen, deutsch)\", \"reason\": \"kurz, warum diese Parameter\"}. "
    "Nutze AUSSCHLIESSLICH die genannten Parameter-Schlüssel und bleibe in den erlaubten "
    "Bereichen. Leite die Änderung aus den Ergebnissen ab (z.B. zu viele Signale mit "
    "niedriger Winrate -> strengere Filter; kaum Signale -> lockerer; SL zu eng -> sl_atr "
    "erhöhen; Ziel zu weit -> tp_r senken). Vermeide Overfitting: eine klare Hypothese, "
    "keine Extremwerte."
)


def param_ranges(setup: str) -> Dict[str, Tuple[float, float]]:
    """Erlaubter Bereich je numerischem Parameter eines Setups (rein)."""
    out: Dict[str, Tuple[float, float]] = {}
    for v in detectors.VARIANTS.get(setup, []):
        for k, val in v.items():
            if k == "name" or not isinstance(val, (int, float)):
                continue
            lo, hi = out.get(k, (val, val))
            out[k] = (min(lo, val), max(hi, val))
    return {k: (round(lo * RANGE_LO, 4), round(hi * RANGE_HI, 4)) for k, (lo, hi) in out.items()}


def sanitize(setup: str, proposed: Optional[Dict], base: Dict, version: int) -> Optional[Dict]:
    """Vorschlag prüfen: nur bekannte Schlüssel, in Bereich klemmen, Ganzzahlen
    erhalten, mindestens EIN Wert muss sich von der Basis unterscheiden (rein)."""
    if not isinstance(proposed, dict):
        return None
    ranges = param_ranges(setup)
    out = {k: v for k, v in base.items() if k != "name"}
    changed = False
    for k, raw in proposed.items():
        if k not in ranges:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        lo, hi = ranges[k]
        val = max(lo, min(hi, val))
        if k in INT_KEYS:
            val = int(round(val))
        else:
            val = round(val, 3)
        if val != out.get(k):
            changed = True
        out[k] = val
    if not changed:
        return None
    out["name"] = f"KI-Rev.{int(version)}"
    return out


def history_lines(history: List[Dict]) -> List[str]:
    rows = []
    for h in (history or [])[-8:]:
        i, o = h.get("is") or {}, h.get("oos") or {}
        rows.append(f"- {h.get('name')}: IS {i.get('trades', 0)}T/WR {i.get('winrate', 0)}%/"
                    f"{float(i.get('pnl') or 0):+.2f} · OOS {o.get('trades', 0)}T/WR {o.get('winrate', 0)}%/"
                    f"{float(o.get('pnl') or 0):+.2f}{' · BESTANDEN' if h.get('passed') else ''}")
    return rows


def build_prompt(asset_class: str, setup: str, desc: str, base: Dict, history: List[Dict],
                 rules: Dict) -> str:
    ranges = param_ranges(setup)
    rng = ", ".join(f"{k}: {lo}–{hi}" for k, (lo, hi) in ranges.items())
    base_txt = ", ".join(f"{k}={v}" for k, v in base.items() if k != "name")
    return (
        f"SETUP '{setup}' in {ac.LABELS.get(asset_class, asset_class)} – Regel: {desc}\n"
        f"Bestehen-Kriterium: IS >= {rules.get('min_is_trades')} Trades und OOS >= "
        f"{rules.get('min_oos_trades')} Trades, PnL in BEIDEN Fenstern > 0 nach Gebühren, "
        f"OOS zusätzlich PnL>0 oder Winrate >= 55 %.\n"
        f"Bisher getestete Parameter-Sätze (chronologisch):\n" + "\n".join(history_lines(history) or ["- keine"])
        + f"\nBasis (beste bisher): {base_txt}\n"
        f"Parameter-Schlüssel und erlaubte Bereiche: {rng}\n"
        "Gib EINEN neuen Parameter-Satz (nur geänderte oder alle Schlüssel) als JSON zurück."
    )


def _best_base(setup: str, entry: Dict, history: List[Dict]) -> Dict:
    """Ausgangspunkt der Revision: getunter/KI-Satz, sonst beste IS-Variante."""
    for key in ("ai_proposal", "tuned"):
        if isinstance(entry.get(key), dict):
            return dict(entry[key])
    from services.setup_backtest import runner
    idx = runner.best_base_variant(history)
    variants = detectors.VARIANTS[setup]
    return dict(variants[(idx or 0) % len(variants)])


async def propose(db, asset_class: str, setup: str, entry: Dict, history: List[Dict],
                  rules: Dict, version: int) -> Optional[Dict]:
    """LLM-Vorschlag holen und prüfen. None = kein verwertbarer Vorschlag
    (kein Key, Fehler, unveränderte Parameter)."""
    from services import ai_playbook
    from services.ai_engine import ai_engine
    if not getattr(ai_engine, "key", None):
        logger.info("KI-Revision übersprungen: kein LLM-Key")
        return None
    base = _best_base(setup, entry, history)
    desc = ai_playbook.class_setups(asset_class).get(setup) or ai_playbook.all_setups().get(setup, "")
    prompt = build_prompt(asset_class, setup, desc, base, history, rules)
    try:
        text, _provider, model = await ai_engine.generate_for_role(ROLE, prompt, SYSTEM, temperature=0.3)
        data = ai_engine._parse_json(text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"KI-Revision {setup}@{asset_class} fehlgeschlagen: {e}")
        return None
    params = sanitize(setup, data.get("params"), base, version)
    if not params:
        logger.info(f"KI-Revision {setup}@{asset_class}: kein gültiger Parameter-Vorschlag")
        return None
    out = {"params": params, "desc": str(data.get("desc") or "").strip()[:300],
           "reason": str(data.get("reason") or "").strip()[:200], "model": model,
           "version": int(version)}
    # Textrevision an den Live-Prompt weitergeben (nur wenn dort erlaubt)
    if len(out["desc"]) >= 20:
        try:
            res = await ai_playbook.revise_setup(db, asset_class, setup, out["desc"],
                                                 reason=f"Backtest: {out['reason']}"[:200],
                                                 source="backtest")
            out["live_revision"] = res.get("status")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Live-Revision {setup}@{asset_class} nicht übernommen: {e}")
    return out
