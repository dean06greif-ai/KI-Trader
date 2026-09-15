"""Anlageklassen des Setup-Systems (rein & testbar) – EINE Quelle der Wahrheit.

Das Playbook (services/ai_playbook.py) führt seit 06/2026 pro Anlageklasse
einen eigenen Lebenszyklus (Reife-Gate, Rückstufung, Parameter-Profile):
  crypto     – Krypto-Perps (24/7, Funding, Liquidations-Daten)
  indices    – Index-Perps (QQQ/SPY: Session-getrieben, Gaps, US-Open)
  resources  – Rohstoffe (Gold/Silber/Öl: Makro-/News-getrieben, US-Session)
  forex      – Devisen (24/5, geringe Volatilität, Pips statt Prozent)

Beim Wechsel wurden ALLE bestehenden Setups in jede Klasse kopiert; nur
offensichtlich sinnlose Kombinationen sind ausgeschlossen (EXCLUDED).
Die Zuordnung der Symbole kommt aus core/instruments.py (Sidebar-Gruppen).
"""
from typing import Dict, List, Optional, Set

from core import instruments

CRYPTO, INDICES, RESOURCES, FOREX = "crypto", "indices", "resources", "forex"
CLASSES: List[str] = [CRYPTO, INDICES, RESOURCES, FOREX]

LABELS: Dict[str, str] = {CRYPTO: "Krypto", INDICES: "Indizes",
                          RESOURCES: "Rohstoffe", FOREX: "Forex"}

_GROUP_TO_CLASS: Dict[str, str] = {
    instruments.GROUP_CRYPTO: CRYPTO,
    instruments.GROUP_INDICES: INDICES,
    instruments.GROUP_RESOURCES: RESOURCES,
    instruments.GROUP_FOREX: FOREX,
}

# Setups, die in einer Anlageklasse fachlich keinen Sinn ergeben:
#  * funding_fade braucht Perp-Funding-Raten der Krypto-Crowd (FUNDING-FADE-
#    RADAR liefert nur Krypto-Daten) -> außerhalb von Krypto nicht handelbar.
#  * fomc_event zielt auf die stärkste Event-Reaktion (Krypto + US-Indizes);
#    Rohstoffe/Forex reagieren indirekter (USD) -> dort nicht handelbar.
#    cpi_event/nfp_event/ppi_event/pce_event: gleiche Logik (US-Datenveröffentlichungen).
EXCLUDED: Dict[str, Set[str]] = {
    CRYPTO: set(),
    INDICES: {"funding_fade"},
    RESOURCES: {"funding_fade", "fomc_event", "cpi_event", "nfp_event",
                "ppi_event", "pce_event"},
    FOREX: {"funding_fade", "fomc_event", "cpi_event", "nfp_event",
            "ppi_event", "pce_event"},
}

# Kompakte Klassen-Hinweise für den Prompt (bewusst 1 Zeile – Token-Budget).
HINTS: Dict[str, str] = {
    CRYPTO: "24/7, hohe Vol; Funding/Liquidations-Daten nutzen; BTC-Korrelation beachten.",
    INDICES: ("Session-getrieben: Edge v.a. US-Open/Close, Gaps zum Vortag, "
              "Übernacht dünn -> keine Breakouts außerhalb der US-Session."),
    RESOURCES: ("Makro-/News-getrieben (USD, Zinsen, Lager-/Förderdaten); Edge in "
                "London/US-Session; SL an Struktur, nicht an Prozent-Standardwerten."),
    FOREX: ("Geringe Vol (Tagesrange oft <1%); "
            "Range/Mean-Reversion stärker als Breakouts; Session-Überlappung London/NY."),
}

# Analyse-Gruppen (ai_engine._analysis_groups) -> enthaltene Anlageklassen
GROUP_CLASSES: Dict[str, List[str]] = {
    "Krypto": [CRYPTO],
    "Forex": [FOREX],
    "Indizes & Rohstoffe": [INDICES, RESOURCES],
    "Alle Assets": list(CLASSES),
}

# Harte SL/TP-Grenzen (% vom Preis) je Klasse – die KI bekommt sie als Hinweis,
# durchgesetzt werden sie regelbasiert (clamp_levels): ein Krypto-SL von 0.6 %
# ist in Forex das Dreifache der Tagesrange. Swing-Trades: Obergrenze ×2.
LIMITS: Dict[str, Dict[str, float]] = {
    CRYPTO: {"sl_min": 0.2, "sl_max": 3.0, "tpf_max": 8.0},
    INDICES: {"sl_min": 0.1, "sl_max": 1.5, "tpf_max": 4.0},
    RESOURCES: {"sl_min": 0.15, "sl_max": 2.0, "tpf_max": 5.0},
    FOREX: {"sl_min": 0.05, "sl_max": 0.5, "tpf_max": 1.5},
}


def clamp_levels(asset_class: str, sl_pct: float, tp1_pct: float, tpf_pct: float,
                 is_swing: bool = False) -> Dict:
    """SL/TP-Prozente in die Klassen-Grenzen zwingen (rein & testbar). Wird der
    SL geklemmt, skalieren die TPs im gleichen Verhältnis (CRV bleibt erhalten);
    tpf zusätzlich an tpf_max gedeckelt. Liefert sl_pct/tp1_pct/tpf_pct und
    levels_clamp (Notiz oder None)."""
    lim = LIMITS.get(asset_class) or LIMITS[CRYPTO]
    mult = 2.0 if is_swing else 1.0
    sl_min, sl_max, tpf_max = lim["sl_min"], lim["sl_max"] * mult, lim["tpf_max"] * mult
    sl = max(0.01, float(sl_pct or 0))
    tp1 = max(0.0, float(tp1_pct or 0))
    tpf = max(0.0, float(tpf_pct or 0))
    note = None
    new_sl = min(max(sl, sl_min), sl_max)
    if new_sl != sl:
        ratio = new_sl / sl
        tp1, tpf = tp1 * ratio, tpf * ratio
        note = f"SL {sl:.2f}%→{new_sl:.2f}% ({LABELS.get(asset_class, asset_class)}-Grenze, TPs ×{ratio:.2f})"
        sl = new_sl
    if tpf > tpf_max:
        note = (note + "; " if note else "") + f"TPf {tpf:.2f}%→{tpf_max:.2f}%"
        tpf = tpf_max
        tp1 = min(tp1, tpf)
    return {"sl_pct": round(sl, 4), "tp1_pct": round(tp1, 4), "tpf_pct": round(tpf, 4),
            "levels_clamp": note}


def asset_class_of(symbol: Optional[str]) -> str:
    """Anlageklasse eines Symbols; unbekannte (extern adoptierte Bitunix-
    Kontrakte) gelten als Krypto."""
    inst = instruments.get(symbol or "")
    return _GROUP_TO_CLASS.get(inst.group, CRYPTO) if inst else CRYPTO


def symbols_of(asset_class: str) -> List[str]:
    return [i.symbol for i in instruments.INSTRUMENTS
            if _GROUP_TO_CLASS.get(i.group) == asset_class]


def classes_for_group(group_label: str) -> List[str]:
    return list(GROUP_CLASSES.get(group_label) or CLASSES)


def setup_allowed(asset_class: str, setup: Optional[str]) -> bool:
    return bool(setup) and setup not in EXCLUDED.get(asset_class, set())


def allowed_setups(asset_class: str, library: Dict[str, str]) -> Dict[str, str]:
    """Playbook-Bibliothek gefiltert auf die Anlageklasse (rein)."""
    ex = EXCLUDED.get(asset_class, set())
    return {sid: desc for sid, desc in library.items() if sid not in ex}


def excluded_reason(asset_class: str, setup: Optional[str]) -> Optional[str]:
    if setup and setup in EXCLUDED.get(asset_class, set()):
        return (f"Setup '{setup}' ist für die Anlageklasse {LABELS.get(asset_class, asset_class)} "
                f"nicht vorgesehen")
    return None
