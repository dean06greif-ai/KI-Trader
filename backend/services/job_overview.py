"""Bereichsübergreifende Übersicht pausierter Rechen-Jobs (Header-Anzeige).

Jeder Bereich hält seine Jobs weiter in seinem eigenen JOBS-Dict; hier wird
nur gelesen. Fortsetzen/Abbrechen läuft bewusst über die bestehenden
Bereichs-Endpoints (gleiche Logik wie in den Panels) – die Pfade werden
mitgeliefert, damit die Oberfläche nichts doppelt kennen muss.
"""
import importlib
from typing import Dict, List

# area -> (Anzeigename, Modul, Pfad-Präfix für resume/cancel)
AREAS = {
    "optimizer": ("Strategie-Optimizer", "services.optimizer", "/api/optimizer"),
    "backtest": ("Backtester", "services.backtester", "/api/backtest"),
    "regime_lab": ("Regime-Lab", "services.regime_lab", "/api/regime-lab"),
    "dynamic": ("Dynamische Strategie", "services.dynamic_workbench", "/api/dynamic-workbench"),
    "ai_lab": ("KI-Trader-Lab", "services.setup_backtest.runner", "/api/ai/playbook/backtest"),
}


def _paths(prefix: str, jid: str) -> Dict[str, str]:
    return {"resume": f"{prefix}/resume/{jid}", "cancel": f"{prefix}/cancel/{jid}"}


def paused_jobs() -> List[Dict]:
    """Laufende Jobs mit angeforderter oder aktiver Pause, über alle Bereiche (rein lesend)."""
    out: List[Dict] = []
    for area, (label, mod_name, prefix) in AREAS.items():
        try:
            jobs = getattr(importlib.import_module(mod_name), "JOBS", {}) or {}
        except Exception:  # noqa: BLE001 – Bereich optional
            continue
        for jid, j in list(jobs.items()):
            if j.get("status") != "running" or j.get("cancel"):
                continue
            if not (j.get("pause") or j.get("paused")):
                continue
            if (j.get("params") or {}).get("workbench_job"):
                continue  # Unterjob der Dynamik-Werkbank – erscheint dort
            params = j.get("params") or {}
            out.append({"id": jid, "area": area, "label": label,
                        "kind": j.get("kind"), "phase": j.get("phase"),
                        "progress": j.get("progress") or 0,
                        "paused": bool(j.get("paused")),
                        "local": (j.get("execution") or params.get("execution")) == "local",
                        **_paths(prefix, jid)})
    return out
