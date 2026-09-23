"""KI-Kontext „Regime-Bilanz & Erkennungs-Qualität“ (PLAN_REGIME_COCKPIT B4).

Gibt dem KI-Trader das, was ihm bisher fehlte:
  1. Winrate/PnL je Kurzfrist-Regime (30 d, ai_rewards.by_regime)
  2. Winrate/PnL je Struktur-Regime (Lab), sofern Freigabe vorhanden
  3. Vorwärts-Trefferquote der Erkennung je Symbol (regime_cockpit, gecacht)
  4. Erkennungs-Note der freigegebenen Lab-Analyse je Anlageklasse (regime_quality)

Nur Text-Block für den Prompt, keine Handelswirkung. Reine Formatierung ist
unit-testbar (`format_block`), IO in `prompt_block`.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_TRADES_ROW = 3
MAX_ROWS = 8
LOW_HIT_PCT = 50.0
UNRELIABLE_HIT_PCT = 52.0


def annotate_label(label: Optional[str], row: Optional[Dict]) -> str:
    """Rein: Kurzfrist-Label um seine Vorwärts-Trefferquote ergänzen (P2-3).
    Unter UNRELIABLE_HIT_PCT wird das Label ausdrücklich als unzuverlässig
    markiert – die KI soll es dann nicht als Einstiegsbegründung nutzen."""
    lab = str(label or "–")
    if not row or row.get("error") or not row.get("observer_reliable") or row.get("observer_hit_pct") is None:
        return lab
    hit = float(row["observer_hit_pct"])
    if hit < UNRELIABLE_HIT_PCT:
        return f"{lab} (⚠ Trefferquote {hit:.0f} % – unzuverlässig, nicht als Begründung nutzen)"
    return f"{lab} (Trefferquote {hit:.0f} %)"


def _pct(v) -> str:
    return "–" if v is None else f"{float(v):.0f}%"


def format_block(by_regime: List[Dict], by_structural: List[Dict], cockpit_rows: List[Dict],
                 quality: Dict[str, Dict], days: int = 30) -> str:
    """Rein: Kennzahlen -> Prompt-Block. Leer, wenn es nichts Belastbares gibt."""
    lines: List[str] = []
    rows = sorted([r for r in by_regime if int(r.get("trades") or 0) >= MIN_TRADES_ROW],
                  key=lambda r: -int(r.get("trades") or 0))[:MAX_ROWS]
    if rows:
        lines.append(f"Kurzfrist-Regime (Symbolzeilen, letzte {days} Tage, echte Trades):")
        for r in rows:
            lines.append(f"  - {r.get('regime')}: {r.get('trades')} Trades · WR {_pct(r.get('win_rate'))}"
                         f" · PnL {float(r.get('pnl') or 0):+.2f} · Ø Reward {float(r.get('avg_reward') or 0):+.2f}")
    srows = [r for r in by_structural if str(r.get("regime")) != "unbekannt"
             and int(r.get("trades") or 0) >= MIN_TRADES_ROW]
    if srows:
        lines.append(f"Struktur-Regime (Lab-Modell, letzte {days} Tage):")
        for r in srows[:MAX_ROWS]:
            lines.append(f"  - {r.get('regime')}: {r.get('trades')} Trades · WR {_pct(r.get('win_rate'))}"
                         f" · PnL {float(r.get('pnl') or 0):+.2f}")
    crow = [r for r in cockpit_rows if not r.get("error") and r.get("observer_reliable")]
    if crow:
        lines.append("Vorwärts-Trefferquote der Kurzfrist-Erkennung (Label damals vs. Kurs 4 h später, "
                     f"{crow[0].get('days')} Tage):")
        for r in crow[:12]:
            warn = " ⚠ kaum besser als Zufall" if (r.get("observer_hit_pct") or 0) < LOW_HIT_PCT else ""
            agree = f" · Ebenen einig {_pct(r.get('agreement_pct'))}" if r.get("agreement_pct") is not None else ""
            struct = f" · strukturell {r['structural_current']}" if r.get("structural_current") else ""
            lines.append(f"  - {r.get('symbol')}: {_pct(r.get('observer_hit_pct'))} ({r.get('observer_n')} Punkte)"
                         f" · aktuell {r.get('observer_current') or '–'}{struct}{agree}{warn}")
    qrows = []
    for cls, q in (quality or {}).items():
        ov = (q or {}).get("overall") or {}
        if ov.get("grade"):
            qrows.append(f"  - {q.get('label') or cls}: Note {ov.get('grade')} ({_pct(ov.get('pct'))} Live=Final, "
                         f"Basis {ov.get('basis')}, Stufe {q.get('stage')})")
    if qrows:
        lines.append("Erkennungs-Qualität der freigegebenen Lab-Analyse je Anlageklasse:")
        lines.extend(qrows)
    if not lines:
        return ""
    lines.append("Regel: Regime-Labels sind Hypothesen, keine Fakten. Bei Trefferquote < 50 % oder "
                 "widersprüchlichen Ebenen das Regime NICHT als Begründung für Einstiege nutzen, "
                 "sondern Preis-Struktur/Levels entscheiden lassen. Lektionen zu Regimen immer mit "
                 "Ebene ('Kurzfrist …' / 'strukturell …') und Trefferquote begründen.")
    return "=== REGIME-BILANZ & ERKENNUNGS-QUALITÄT (echte Ergebnisse, kein Backtest) ===\n" + "\n".join(lines)


async def _quality_by_class(db, symbols: List[str]) -> Dict[str, Dict]:
    from services import regime_quality, regime_release
    from services.setup_asset_class import LABELS, asset_class_of
    out: Dict[str, Dict] = {}
    for cls in sorted({asset_class_of(s) for s in symbols}):
        try:
            doc = await regime_release.released_for_class(db, cls)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"regime_context release {cls}: {e}")
            doc = None
        if not doc:
            continue
        q = regime_quality.summarize(doc)
        scope = q.get("combined") or next(iter(q.values()), None)
        if scope:
            out[cls] = {"label": LABELS.get(cls, cls), "stage": (doc.get("release") or {}).get("stage"),
                        "overall": scope.get("overall")}
    return out


async def prompt_block(db, symbols: List[str], days: int = 30) -> str:
    """IO-Hülle: alle Teile fail-soft einsammeln, dann formatieren."""
    if db is None:
        return ""
    from services import ai_rewards, regime_cockpit
    by_regime: List[Dict] = []
    by_struct: List[Dict] = []
    cockpit_rows: List[Dict] = []
    quality: Dict[str, Dict] = {}
    try:
        by_regime = await ai_rewards.by_regime(db, days)
        by_struct = await ai_rewards.by_structural_regime(db, days)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"regime_context rewards: {e}")
    try:
        cockpit_rows = regime_cockpit.overview_cached(db, list(symbols)[:12], 14)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"regime_context cockpit: {e}")
    try:
        quality = await _quality_by_class(db, list(symbols))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"regime_context quality: {e}")
    return format_block(by_regime, by_struct, cockpit_rows, quality, days)
