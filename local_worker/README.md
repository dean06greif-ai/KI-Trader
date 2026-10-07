# Lokaler Worker (v1.22.0)

## Neu in 1.22.0
- Ergebnis-Backtest dynamischer Strategien läuft lokal wieder (Fehler „No module named 'telegram'“ behoben).
- Realistische Limit-Order-Simulation und Ausreißer-Filter je Regime (gleicher Code wie die Website).
- Bitte das Worker-Paket neu herunterladen und den Worker neu starten.

## Neu in 1.15.0
- Gespeicherte Regime-Analysen lokal neu bewerten (Referenz v2 / Macro-F1 / Regime-Nutzen).


## Neu in 1.14.0
- Regime-Lab-Ablation kann lokal laufen (Ausführung „Lokal“): nutzt den Kerzen-Cache des Workers,
  statt in der Cloud alle 1m-Kerzen neu zu laden.


Führt Backtests, Optimierungen (inkl. Endlos-Suche), Regime-Lab-Jobs (inkl.
Regime-Autopilot) und Daten-Downloads auf deinem eigenen Rechner aus. Der
Worker verbindet sich per Outbound-Polling mit der Website – es sind KEINE
Portfreigaben nötig.

## Mehrere Worker gleichzeitig (max. 2)
Zwei PCs können parallel rechnen (z.B. dein PC + PC eines Freundes):
1. Auf beiden PCs dasselbe Worker-Paket + dasselbe Token verwenden, aber einen
   eigenen Namen setzen (`--name Kumpel-PC` bzw. `--name Mein-PC`).
2. Sind zwei Worker verbunden, erscheint neben „Ausführung → Lokal“ die Auswahl
   **Worker**: dort festlegen, welcher PC den Job rechnet (Optimizer/Endlos-Suche,
   Backtester, Regime-Lab, Dynamik-Werkbank). „Automatisch“ = wie bisher, der
   nächste freie Worker übernimmt.
3. Jobs auf verschiedenen Workern laufen parallel (z.B. Endlos-Suche auf dem
   Kumpel-PC + Regime-Lab-Suche auf deinem PC). Cloud-Jobs bleiben einzeln.
4. Ein dritter Worker wartet ohne Jobs, bis einer der beiden beendet wird.
Das Worker-Verhalten selbst ist unverändert – kein neues Paket nötig.

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
  Worker neu starten. Beim Neu-Entpacken nur `worker.py`, `core/`, `services/`,
  `strategies/`, `models/` ersetzen – `worker_data/` (Kerzendaten) und
  `worker_config.json` bleiben erhalten; die Verbindung (Server-URL + Token)
  liegt zusätzlich in `~/.ki_trader_worker/worker_config.json`.

## Neu in 1.17.0 (Lücken reparieren)

- Website → Lokal → Daten: neuer Knopf 🔧 je Symbol „Lücken reparieren“. Der Worker
  lädt fehlende 1m-Kerzen erst erneut aus der Primärquelle und danach aus einer
  zweiten Quelle nach (Krypto: Binance ↔ Bitunix, FX/Metalle/Indizes: Dukascopy,
  auf das Preisniveau der Primärquelle skaliert). Ergebnis: Lücken vorher → nachher.
- Hinweis: stammen Server- und Worker-Kerzen aus verschiedenen Quellen, kann eine
  Analyse trotz gleicher Kerzenanzahl an der Candle-Checksum scheitern – dann die
  Analyse in der Cloud rechnen bzw. neu erstellen.

## Neu in 1.16.2 (ruhigeres Log – Verhalten unverändert)

- Kurze Verbindungsaussetzer (< 20 s, z.B. DNS-Wackler des eigenen Internets oder
  ein Render-Neustart) werden nicht mehr gemeldet – gerechnet und gepollt wird wie
  bisher weiter.
- Poll-Schleife und Fortschritts-Meldungen teilen sich EINEN Verbindungszustand:
  pro Ausfall genau eine Zeile „Server nicht erreichbar (Grund)“ und eine Zeile
  „wiederhergestellt (nach N s)“ statt 3–5 Zeilen je Job.
- Kurze Gründe („Server-Adresse nicht auflösbar“, „Zeitüberschreitung“,
  „Verbindung vom Server getrennt“) statt der langen HTTPSConnectionPool-Meldung.
- Meldungen der Rechen-Module haben jetzt ebenfalls einen Zeitstempel.
- (Server-Code, kommt automatisch mit) Symbole, deren Kerzen dauerhaft nicht zum
  Datensatz-Manifest passen (z.B. `HYPEUSDT: Kerzenanzahl 8644 ≠ Manifest 8759`),
  werden weiterhin erklärt ausgeschlossen, aber nur noch einmal je 6 h gemeldet
  und nicht bei jedem Autopilot-Job erneut „repariert“. Dukascopy-Abbrüche
  (HTTP 503 / Timeout) erscheinen als EINE Zeile je Instrument und Tag, max. alle 6 h.

## Neu in 1.16.0 (Regime-Walk-Forward robust)

- Passt ein Symbol nicht mehr exakt zum Datensatz-Manifest einer Analyse (z.B.
  Lücke im lokalen 1m-Cache), werden die fehlenden Kerzen gezielt nachgeladen.
  Bleibt die Abweichung, wird nur dieses Symbol erklärt ausgeschlossen, statt dass
  der ganze Walk-Forward abbricht (Abbruch nur, wenn < 75 % der Symbole passen).

## Neu in 1.13.0 (Dauerbetrieb)

- Der Worker beendet sich bei unerwarteten Fehlern in der Verbindungsschleife
  nicht mehr, sondern loggt und verbindet sich weiter (sanfter Backoff 5–30 s).
- Fertige Ergebnisse werden vor dem Upload in `worker_data/pending_results/`
  gesichert und nach einem Neustart/Reconnect automatisch nachgeliefert
  (Upload-Wiederholung bis 6 h).
- Wird der Worker neu gestartet, während ein Job lief, erkennt der Server das
  am nächsten Heartbeat und reiht den Job automatisch neu ein (statt bis zu 6 h
  „hängen bei 10 %“).
