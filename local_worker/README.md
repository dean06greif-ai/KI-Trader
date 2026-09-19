# Lokaler Worker (v1.12.0)

Führt Backtests, Optimierungen (inkl. Endlos-Suche), Regime-Lab-Jobs (inkl.
Regime-Autopilot) und Daten-Downloads auf deinem eigenen Rechner aus. Der
Worker verbindet sich per Outbound-Polling mit der Website – es sind KEINE
Portfreigaben nötig.

## Installation

1. Python 3.10+ installieren (https://python.org)
2. Abhängigkeiten installieren:

   ```
   pip install -r requirements.txt
   ```

## Starten

```
python worker.py
```

Beim ersten Start wirst du nach der Server-URL und dem Worker-Token gefragt
(beides findest du auf der Website unter Ausführung → Lokal → ⚙ Verwalten).
Die Angaben werden in `worker_config.json` gespeichert – zusätzlich im
Benutzerordner (`~/.ki_trader_worker/worker_config.json`). Danach verbindet
sich der Worker bei jedem Start (Doppelklick auf `start_worker.bat` /
`start_worker.sh`) automatisch – auch aus einem frisch heruntergeladenen Paket,
ohne erneute Token-Eingabe.

Alternativ per Umgebungsvariablen / Argumenten:

```
python worker.py --server https://deine-website.example --token DEIN_TOKEN
# oder
WORKER_SERVER_URL=... WORKER_TOKEN=... python worker.py
```

## Einstellungen

CPU-Kerne, RAM-Limit, GPU, parallele Jobs und der Daten-Ordner werden auf der
Website verwaltet (Ausführung → Lokal → ⚙ Verwalten) und beim Polling
automatisch übernommen. Kerzendaten liegen standardmäßig in `./worker_data`.

## Wichtig

- Dieses Paket wird immer vom Server heruntergeladen (Download-Button) und
  enthält den EXAKT gleichen Berechnungs-Code wie die Website
  (`core/`, `services/`, `strategies/`, `models/`) – identische Ergebnisse.
- Bei einer Versionswarnung auf der Website: Paket neu herunterladen und den
  Worker neu starten.
