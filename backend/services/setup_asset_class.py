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
EXCLUDED: Dict[str, Set[str]] = {
    CRYPTO: set(),
    INDICES: {"funding_fade"},
    RESOURCES: {"funding_fade"},
    FOREX: {"funding_fade"},
}

# Kompakte Klassen-Hinweise für den Prompt (bewusst 1 Zeile – Token-Budget).
HINTS: Dict[str, str] = {
    CRYPTO: "24/7, hohe Vol; Funding/Liquidations-Daten nutzen; BTC-Korrelation beachten.",
    INDICES: ("Session-getrieben: Edge v.a. US-Open/Close, Gaps zum Vortag, "
              "Übernacht dünn -> keine Breakouts außerhalb der US-Session."),
    RESOURCES: ("Makro-/News-getrieben (USD, Zinsen, Lager-/Förderdaten); Edge in "
                "London/US-Session; SL an Struktur, nicht an Prozent-Standardwerten."),
    FOREX: ("Geringe Vol (Tagesrange oft <1%): sl_pct 0.1-0.5 statt Krypto-Werte; "
            "Range/Mean-Reversion stärker als Breakouts; Session-Überlappung London/NY."),
}

# Analyse-Gruppen (ai_engine._analysis_groups) -> enthaltene Anlageklassen
GROUP_CLASSES: Dict[str, List[str]] = {
    "Krypto": [CRYPTO],
    "Forex": [FOREX],
    "Indizes & Rohstoffe": [INDICES, RESOURCES],
    "Alle Assets": list(CLASSES),
}


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
