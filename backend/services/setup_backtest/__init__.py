"""Backtest-Seeding der Playbook-Setups (BACKTEST_SEEDING_PLAN.md, Phase 1).

Regelbasierte Detektoren testen die formalisierbaren Setups je Anlageklasse auf
Vergangenheitsdaten; Out-of-Sample-Ergebnisse zählen – gewichtet und gedeckelt –
als Vorab-Evidenz für das Reife-Gate. Echte Paper-Trades bleiben Pflicht.

Module:
  detectors.py  – reine Setup-Detektoren + Parameter-Varianten
  simulator.py  – konservative Trade-Simulation (SL vor TP, Gebühren, MFE/MAE)
  weights.py    – Gewichtung Backtest- vs. echte Trades (rein)
  runner.py     – Job (Kerzen laden, IS/OOS, Varianten-Schleife, Speicherung)
"""
from services.setup_backtest import detectors, simulator, weights, runner  # noqa: F401

ELIGIBLE_SETUPS = list(detectors.VARIANTS.keys())
