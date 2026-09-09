"""KI-Revision im Setup-Backtest: der KI-Trader überarbeitet Setups OHNE Edge.

Nach einem Backtest-Durchlauf bekommt die KI (Rolle research_analyst) je
gescheitertem Setup
  * die getesteten Parameter-Sätze mit In-/Out-of-Sample-Ergebnis,
  * eine TIEFEN-DIAGNOSE des besten Satzes (Exit-Verteilung, Long/Short,
    Uhrzeit, Profit-Faktor, Drawdown, MFE/MAE, Equity-Kurve – analysis.py),
  * die LERNSCHLEIFE: was ihre bisherigen Revisionen geändert haben, welche
    Hypothese dahinterstand und ob das Ergebnis besser/schlechter wurde,
und schlägt EINEN neuen Parameter-Satz plus Hypothese und Erwartung vor.

Stellschrauben = Basis-Parameter der Varianten (Bereich [0.5×Min, 2×Max])
+ optionale Filter/Exit-Parameter je Setup (detectors.optional_params:
Teilgewinn tp1_r, Zeit-Exit max_bars, Seite, Volatilitäts-Regime, Uhrzeit,
1h-Trend, setup-spezifische Puffer). Defaults = bisheriges Verhalten.

  * `propose()`   – LLM-Aufruf, Rückgabe eines geprüften Parameter-Satzes
  * `sanitize()`  – reine Prüfung/Klemmung des Vorschlags (testbar)
  * Modus "ai_loop" im Runner testet den Vorschlag sofort (Schleife bis
    Ziel erreicht oder max. Runden), sonst wird er als `ai_proposal` für den
    nächsten Lauf vorgemerkt. Basis jeder Runde ist der BESTE bekannte Satz.
Die Textrevision wird zusätzlich über ai_playbook.revise_setup an den Live-
Prompt gegeben (nur wenn das Setup dort rückgestuft/inaktiv ist – sonst nur Log).
"""
import logging
from typing import Dict, List, Optional, Tuple

from services import setup_asset_class as ac
from services.setup_backtest import analysis, detectors

logger = logging.getLogger(__name__)

ROLE = "research_analyst"
MAX_ROUNDS = 10
DEFAULT_ROUNDS = 3
DEFAULT_TARGET = 3
# Erlaubter Bereich je Basis-Parameter: [0.5 x Minimum, 2 x Maximum] über alle Varianten
RANGE_LO, RANGE_HI = 0.5, 2.0
INT_KEYS = ("lookback", "lookback_h", "min_bars", "slope_bars", "pivot_k",
            "max_bars", "sides", "hour_from", "hour_to", "htf_trend")
# Overfitting-Bremse: höchstens so viele Schlüssel je Revision ändern (weitere
# Änderungen in Reihenfolge des Vorschlags werden verworfen)
MAX_CHANGES = 4
REASON_MAX = 400
EXPECT_MAX = 160

SYSTEM = (
    "Du bist der Forschungs-Analyst eines KI-Traders. Du überarbeitest regelbasierte "
    "Trading-Setups, die im Backtest (5m, In-Sample/Out-of-Sample, nach Gebühren) KEINEN Edge hatten. "
    "Ziel ist langfristige Profitabilität: positiver Erwartungswert je Trade, Profit-Faktor > 1.3, "
    "moderater Drawdown – nicht maximale Trade-Anzahl.\n"
    "Arbeitsweise: 1) Diagnose lesen – wo geht das Geld verloren (Exit-Art, Seite, Uhrzeit, Symbol, "
    "Gebührenanteil, Payoff)? 2) Lernschleife beachten – Änderungen, die zuletzt SCHLECHTER wurden, nicht "
    "wiederholen; erfolgreiche Richtung fortsetzen. 3) EINE klare Hypothese, 1–3 Stellschrauben ändern "
    f"(hart begrenzt auf {MAX_CHANGES} – weitere Änderungen werden verworfen). Filter nicht stapeln: "
    "nicht gleichzeitig Seite, Uhrzeit, Trend und Volatilität einschränken, sonst bleiben 0 Trades.\n"
    "Heuristiken: Verlierer liefen weit ins Plus -> tp1_r senken oder tp_r senken; Gewinner liefen tief ins "
    "Minus/viele SL mit >=0.5R MFE -> sl_atr erhöhen; hoher Gebührenanteil -> weniger, bessere Signale (Filter "
    "strenger, vol_min, htf_trend=1); eine Seite/Uhrzeit klar negativ -> sides bzw. hour_from/hour_to; viele "
    "Zeit-Exits -> max_bars anpassen; kaum Signale -> lockern. Vermeide Extremwerte und Overfitting.\n"
    "Antworte NUR mit JSON: {\"params\": {<key>: <zahl>, ...}, \"desc\": \"überarbeitete Regelbeschreibung "
    "(max. 300 Zeichen, deutsch)\", \"reason\": \"Hypothese: Befund aus der Diagnose -> Änderung -> warum\", "
    "\"expect\": \"messbare Erwartung, z.B. 'weniger Trades, WR > 55 %, PF > 1.3'\"}. "
    "Nutze AUSSCHLIESSLICH die genannten Parameter-Schlüssel und bleibe in den erlaubten Bereichen."
)


def param_ranges(setup: str) -> Dict[str, Tuple[float, float]]:
    """Erlaubter Bereich je numerischem Parameter eines Setups (rein):
    Basis-Parameter aus den Varianten + optionale Stellschrauben."""
    out: Dict[str, Tuple[float, float]] = {}
    for v in detectors.VARIANTS.get(setup, []):
        for k, val in v.items():
            if k == "name" or not isinstance(val, (int, float)):
                continue
            lo, hi = out.get(k, (val, val))
            out[k] = (min(lo, val), max(hi, val))
    ranges = {k: (round(lo * RANGE_LO, 4), round(hi * RANGE_HI, 4)) for k, (lo, hi) in out.items()}
    for k, (_d, lo, hi, _txt) in detectors.optional_params(setup).items():
        ranges.setdefault(k, (lo, hi))
    return ranges


def sanitize(setup: str, proposed: Optional[Dict], base: Dict, version: int) -> Optional[Dict]:
    """Vorschlag prüfen: nur bekannte Schlüssel, in Bereich klemmen, Ganzzahlen
    erhalten, mindestens EIN Wert muss sich von der (effektiven) Basis unter-
    scheiden (rein). Optionale Schlüssel werden nur aufgenommen, wenn sie vom
    Default abweichen oder schon in der Basis standen – Sätze bleiben kompakt."""
    if not isinstance(proposed, dict):
        return None
    ranges = param_ranges(setup)
    defaults = detectors.param_defaults(setup)
    out = {k: v for k, v in base.items() if k != "name"}
    effective = {**defaults, **out}
    changed = 0
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
        if val != effective.get(k):
            if changed >= MAX_CHANGES:
                continue
            changed += 1
        if k in defaults and val == defaults[k] and k not in out:
            continue
        out[k] = val
    if not changed:
        return None
    out["name"] = f"KI-Rev.{int(version)}"
    return out


def history_lines(history: List[Dict]) -> List[str]:
    rows = []
    for h in (history or [])[-8:]:
        i, o = h.get("is") or {}, h.get("oos") or {}
        d = (h.get("diag") or {}).get("oos") or {}
        extra = f" · PF {d.get('pf')}/DD {d.get('max_dd')}" if d.get("n") else ""
        rows.append(f"- {h.get('name')}: IS {i.get('trades', 0)}T/WR {i.get('winrate', 0)}%/"
                    f"{float(i.get('pnl') or 0):+.2f} · OOS {o.get('trades', 0)}T/WR {o.get('winrate', 0)}%/"
                    f"{float(o.get('pnl') or 0):+.2f}{extra} · Score {analysis.score(i, o):+.3f}"
                    f"{' · BESTANDEN' if h.get('passed') else ''}")
    return rows


def _base_diag(base: Dict, history: List[Dict]) -> Optional[Dict]:
    """Diagnose des Historien-Eintrags, dessen Parameter der Basis entsprechen."""
    want = {k: v for k, v in base.items() if k != "name"}
    for h in reversed(history or []):
        if isinstance(h.get("params"), dict) and h["params"] == want:
            return h.get("diag")
    return None


def build_prompt(asset_class: str, setup: str, desc: str, base: Dict, history: List[Dict],
                 rules: Dict) -> str:
    ranges = param_ranges(setup)
    help_txt = detectors.param_help(setup)
    defaults = detectors.param_defaults(setup)
    rng = "\n".join(f"  {k}: {lo}–{hi}" + (f" (Default {defaults[k]})" if k in defaults else "")
                    + (f" – {help_txt[k]}" if help_txt.get(k) else "") for k, (lo, hi) in ranges.items())
    base_txt = ", ".join(f"{k}={v}" for k, v in base.items() if k != "name")
    diag = _base_diag(base, history) or {}
    diag_lines = analysis.describe(diag.get("is"), "In-Sample") + analysis.describe(diag.get("oos"), "Out-of-Sample") \
        if diag else ["(keine Diagnose zur Basis gespeichert)"]
    lesson_lines = analysis.lessons(history, defaults) or ["- noch keine KI-Revision getestet"]
    return (
        f"SETUP '{setup}' in {ac.LABELS.get(asset_class, asset_class)} – Regel: {desc}\n"
        f"Bestehen-Kriterium: IS >= {rules.get('min_is_trades')} Trades und OOS >= "
        f"{rules.get('min_oos_trades')} Trades, PnL in BEIDEN Fenstern > 0 nach Gebühren, "
        f"OOS zusätzlich PnL>0 oder Winrate >= 55 %. Score = PnL je Trade (OOS 60 %, IS 40 %).\n\n"
        f"Bisher getestete Parameter-Sätze (chronologisch):\n" + "\n".join(history_lines(history) or ["- keine"])
        + f"\n\nBASIS (bester bekannter Satz): {base_txt}\nDIAGNOSE der Basis:\n" + "\n".join(diag_lines)
        + "\n\nLERNSCHLEIFE – bisherige KI-Revisionen und ihr Ergebnis:\n" + "\n".join(lesson_lines)
        + f"\n\nParameter-Schlüssel, erlaubte Bereiche und Bedeutung:\n{rng}\n"
        "Gib EINEN neuen Parameter-Satz (nur geänderte Schlüssel genügen) als JSON zurück."
    )


def _best_base(setup: str, entry: Dict, history: List[Dict]) -> Dict:
    """Ausgangspunkt der Revision: bester Historien-Eintrag mit Parametern
    (Score), sonst vorgemerkter/getunter Satz, sonst beste IS-Variante."""
    best = analysis.best_entry(history)
    if best:
        return {**best["params"], "name": best.get("name") or "Basis"}
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
    base_eff = {**detectors.param_defaults(setup), **{k: v for k, v in base.items() if k != "name"}}
    new_eff = {**detectors.param_defaults(setup), **{k: v for k, v in params.items() if k != "name"}}
    out = {"params": params, "desc": str(data.get("desc") or "").strip()[:300],
           "reason": str(data.get("reason") or "").strip()[:REASON_MAX],
           "expect": str(data.get("expect") or "").strip()[:EXPECT_MAX],
           "changes": analysis.param_diff(base_eff, new_eff), "base": base.get("name"),
           "model": model, "version": int(version)}
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
