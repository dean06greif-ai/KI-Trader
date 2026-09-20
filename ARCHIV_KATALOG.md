# Archiv-Katalog (AP13 – dokumentierte Archivierung, kein blindes Löschen)

> Stand: 26.06.2026. Veraltete Experimental-/Review-Dateien werden hier
> katalogisiert. Verschobene Dateien liegen unter `archive/` – Original-Inhalt
> unverändert. Nichts davon wird von Code importiert oder vom Deploy benötigt
> (vor dem Verschieben per Referenz-Suche geprüft).

## Nach `archive/backend_fixes/` verschoben (Fix längst im Code, Doku historisch)

| Datei (vorher) | Inhalt | Warum archiviert |
|---|---|---|
| `backend/README_FIX.md` | Notiz zu einem frühen Bitunix-Fix | Fix seit langem im Code (`services/bitunix_trade.py`), Notiz veraltet |
| `backend/README_FIX_30027.md` | Doku zum Precision-/Code-30027-Fix | Fix aktiv im Code; Regressionstest existiert (`backend/test_bitunix_precision_fix.py`, bleibt am Ort) |
| `backend/README_FIX_WATCHDOG_PNL_SETUPS.md` | Doku zu Watchdog-/PnL-/Setup-Fixes | Durch AP01/AP02 (siehe `PROGRESS.md`) überholt |
| `backend/bitunix_30027_fix.patch` | Roh-Patch | Bereits angewendet – Patchdatei nur historisch |
| `backend/bitunix_live_fix.patch` | Roh-Patch | Bereits angewendet – Patchdatei nur historisch |

## Nach `archive/reviews/` verschoben (durch Analysepaket überholt)

| Datei (vorher) | Inhalt | Warum archiviert |
|---|---|---|
| `KI_TRADER_ANALYSE_0609.md` | Frühe externe Analyse (06.09.) | Vollständig überholt durch `analysis_paket/` (15.09.) |
| `KI_TRADER_REVIEW_0709.md` | Externes Review (07.09.) | Vollständig überholt durch `analysis_paket/` (15.09.) |

## Historisch, aber AM ORT BELASSEN (weiter referenziert bzw. noch nützlich)

| Datei | Status | Grund |
|---|---|---|
| `KI_TRADER_AUDIT.md` | historisch | Plan-Quelle von `UMSETZUNG_FORTSCHRITT.md` (Phasen 1–3/T) – Verweise bleiben gültig |
| `UMSETZUNG_FORTSCHRITT.md` | abgeschlossen | Protokoll der Audit-Umsetzung (alle Phasen ✅) |
| `PROGRESS.md` | AKTIV | Lebendes Protokoll des Analyseplan-Umsetzung (AP00–AP13) |
| `analysis_paket/` | Referenz | Maßgeblicher Plan + Befunde + Original-Charakterisierungstests |
| `KI_TRADER_SETTINGS_CHECK.md`, `STRATEGIEN_BEWERTUNG.md`, `STRATEGIE_LABOR_GUIDE.md`, `BACKTEST_SEEDING_PLAN.md`, `IBKR_SETUP_ANLEITUNG.md`, `UMSETZUNGSPLAN_LIVE_QUALITAET.md` | Nutzer-Doku | Fachliche Anleitungen/Bewertungen – kein Code-Bezug, Entscheidung über Archivierung beim Nutzer |
| `backend/test_bitunix_precision_fix.py` | Regressionstest | Funktionierender Standalone-Test (nicht Teil der `tests/`-Suite) – Code bleibt, nur die zugehörige README wurde archiviert |
| `backend_test.py`, `test_result.md`, `test_reports/` | Test-Infrastruktur | Wird von Test-Agenten der Plattform genutzt |

## Regeln

- `archive/` wird nicht deployt und nicht importiert – reines Ablagefach.
- Vor jeder weiteren Archivierung: `grep -rn "<dateiname>"` über backend/,
  frontend/, scripts/, .github/ (keine Referenzen = verschiebbar).
- Löschen bleibt bewusst Nutzer-Entscheidung.
