"""Regime-Kern – EINE dokumentierte Eintrittsstelle für die gesamte Regime-Familie.

Konsolidierung (Juni 2026, ohne Verhaltensänderung): Die 6 Regime-Module sind
bewusst getrennte, aktiv genutzte Bausteine – sie wurden NICHT zusammengelegt
(hohes Regressionsrisiko, kein Nutzen). Stattdessen bündelt dieser Kern die
öffentliche API und dokumentiert, welches Modul wofür zuständig ist. Neuer
Code importiert bevorzugt aus `services.regime_core`, Bestandscode bleibt
unverändert gültig.

ARCHITEKTUR-KARTE
=================
services/regime.py          (v1/Dispatch)  K-Means-Erkennung (Altbestand) +
                                           Dispatcher `detect_regimes` (wählt v2),
                                           Modell-Migration `is_v2`/`relabel_regimes`.
services/regime_engine.py   (v2-Kern)      Deterministische Marktphasen: Features
                                           (`compute_matrix`), Klassifikation
                                           (`classify_arrays`/`classify_series`),
                                           Modellbau (`build_model`), Taxonomie/IDs
                                           (`regime_id`/`regime_label`/`taxonomy`).
services/regime_reactive.py (Detector)     Reaktiver Umkehrpunkt-Detector (ZigZag/EMA)
                                           für Engine v2 (`detect`, `classify`, `report`).
services/regime_lab.py      (Lab/Storage)  Regime-Analysen erstellen, speichern,
                                           Payloads fürs Frontend (`run_analysis`,
                                           `persist_analysis`, `model_for`).
services/regime_opt.py      (Optimizer)    Regime-gezielte Strategie-Suche +
                                           Walk-Forward (`run_regime_optimizer`,
                                           `run_walkforward`).
services/regime_truth.py    (Referenz)     Ground-Truth-Labels (zentrierte OLS/HMM,
                                           dürfen Zukunft sehen – NIE live) +
                                           Kalibrierung (`truth_labels`, `calibrate`).

HINWEIS: `segments_from_labels` existiert in regime.py (v1, ignoriert None) und
regime_engine.py (v2, ignoriert None UND ids < 0) mit bewusst leicht anderem
Verhalten – NICHT zusammenführen, ohne beide Aufrufer-Pfade zu prüfen.
"""
# Modul-Aliasse: ein Import-Punkt für die ganze Familie
from services import regime as kmeans_v1            # noqa: F401
from services import regime_engine as engine        # noqa: F401
from services import regime_reactive as reactive    # noqa: F401
from services import regime_lab as lab              # noqa: F401
from services import regime_opt as optimizer        # noqa: F401
from services import regime_truth as truth          # noqa: F401

# Meistgenutzte Funktionen direkt am Kern (stabile öffentliche API)
from services.regime import (                       # noqa: F401
    detect_regimes, current_regime, bars_per_day, is_v2, relabel_regimes,
)
from services.regime_engine import (                # noqa: F401
    build_model, resolve_config, classify_series, final_labels,
    regime_id, regime_label, regime_key, taxonomy, norm_mode,
)
from services.regime_truth import truth_labels, calibrate  # noqa: F401

__all__ = [
    "kmeans_v1", "engine", "reactive", "lab", "optimizer", "truth",
    "detect_regimes", "current_regime", "bars_per_day", "is_v2", "relabel_regimes",
    "build_model", "resolve_config", "classify_series", "final_labels",
    "regime_id", "regime_label", "regime_key", "taxonomy", "norm_mode",
    "truth_labels", "calibrate",
]
