# Regime-Lab Prüfbericht (23.09.2026) – Erkennung, Forschungsstand, Brücke zum KI-Trader

Branch-Basis `conflict_230926_0659`. Geprüft wurden Code (`services/regime_*.py`, `structural_regime.py`,
`regime_gate.py`, `regime_lab.py`, `regime_quality.py`), die **7 gespeicherten Analysen in der Produktiv-DB**
(nur lesend) und die Live-Brücke mit echten BTC-1h-Kerzen. Der frühere Befundkatalog (`analysis_paket/`,
R01–R17) ist im Code weitgehend umgesetzt (Marker `R0x` in den Modulen) – dieser Bericht setzt darauf auf.

Legende: **P0** = verfälscht heute Ergebnisse/Live-Verhalten · **P1** = Forschungsergebnis nicht belastbar ·
**P2** = Wartbarkeit/Klarheit. ✅ = in diesem Branch behoben · 📝 = dokumentiert, bewusst offen.

---

## 1. Befund P0 ✅ – Live-Regime ≠ Lab-Regime (Historien-Fenster der Brücke)

**Ort:** `services/structural_regime.py::_compute` (DETECT_DAYS = 30), `regime_engine.classify_series`.

**Was passiert:** Das Lab bewertet die kausale Live-Sicht auf der vollen Historie (z.B. 1080 Tage). Der
KI-Trader berechnete das Struktur-Regime aber auf pauschal **30 Tagen**. Vola-z (`vol_ref_days` bis 200 Tage),
EMA-Anker (50 Tage), Stärke-Achse (seit Phasenstart) und das Pivot-Gedächtnis des reaktiven Detektors sind
auf 30 Tagen nicht eingeschwungen → **anderes Label zur selben Zeit**.

**Messung (echte BTC-1h-Kerzen, 90 Tage, alle 6 h; `scripts/probe_live_window_dependence.py`):**

| Modell (gespeicherte Analyse) | 30-Tage-Fenster: Regime-ID ≠ Lab | Richtung ≠ Lab | mit Detektor-Warmup |
|---|---|---|---|
| `ra_0d9f787d` EMA · 9 Regime · 1h | **55,6 %** | 1,4 % | **0,0 % / 0,0 %** |
| `ra_5c8e1115` EMA · 5 Regime · 1h | 15,6 % | 5,3 % | **0,0 % / 0,0 %** |
| reactive · 5 Regime · 1h (frisch) | 33,1 % | **27,2 %** | 3,1 % / 3,1 % |

**Folge:** Jede Regime→Strategie-Zuordnung, jeder Walkforward und jede Freigabe im Lab bezog sich auf Labels,
die der Trader live so nie gesehen hätte. Dynamische Strategien hätten auf einem anderen Regime umgeschaltet.

**Fix (rückwärtskompatibel):**
- `regime_engine.detector_warmup_bars / required_history_bars / required_history_days` (rein): Mindest-Historie
  je Detektor (EMA-Spannen, Vola-Referenz + Glättung, Pivot-Gedächtnis, EMA-Anker), 30 ≤ Tage ≤ 600.
- `structural_regime._compute` lädt genau diese Tage; `context_from_model` liefert `history_bars`,
  `history_required_bars`, `history_ok` und setzt bei zu wenig Kerzen **`state = stale` mit Grund**
  (Gate/Prompt ohne Struktur, wie bei veraltetem Stand) statt eines stillen Fremdlabels.
- Cockpit-Health: neuer Check `structural_short_history` (`GET /api/regime-cockpit/health`).
- Restrisiko reaktiver Detektor (~3 %): Pivot-Zustand ist pfadabhängig; für 0 % müsste die Brücke bis zum
  Analyse-Anfang (`bounds.start_ts`) laden – bewusst nicht getan (Datenlast), 📝 siehe Abschnitt 6.
- Historienbedarf der gespeicherten Analysen (statt bisher 30 Tage): 1h/5 Regime 122 d · 4h/5 Regime 110 d ·
  1d/5 Regime 114 d · 1h/9 Regime 308 d · 4h+1d/9 Regime („grob“, Vola-Referenz 307 d) ≈ 400 d · 15m/reactive 152 d.
  Der 9er-Modus ist wegen der Vola-Referenz teuer – für die Brücke ist der 3er/5er-Modus deutlich robuster.

## 2. Befund P1 ✅ – „Erkennungs-Qualität: gut“ war beim EMA-Detektor eine Selbst-Bestätigung

**Ort:** `regime_quality.py`, `regime_lab._live_agreement`, EMA-Vergleich (`run_ema_compare`).

**Was passiert:** Hauptkennzahl „Live=Final“ misst, wie oft die kausale Sicht dieselbe Richtung sieht wie die
Final-Sicht **desselben Detektors**. Beim Detektor `ema` ist Final nur die um ein halbes Steigungs-Fenster
zentrierte Steigung (bei `ema_regime_smooth_days = 0.5` auf 1h: 6 Kerzen). Live und Final sind damit fast
identisch → **98–99 % „gut“**, egal wie schlecht der Detektor die echten Phasen trifft.

**Belege aus der Produktiv-DB (BTCUSDT, jeweils combined):**

| Analyse | Detektor | Live=Final Holdout | Richtungs-Treffer gg. Referenz (zentrierte Rückblick-Phasen) | Auffälligkeit |
|---|---|---|---|---|
| `ra_0d9f787d` 1h · 9 Regime · EMA 30d | ema | 98,5 % | Final: 68 % · exakt 37 % | 35-Tage-Segment „Seitwärts“ mit −19,5 % (Validierung schlägt an) |
| `ra_5c8e1115` 1h · 5 Regime · EMA 30d | ema | 98,5 % | Final: 70 % · exakt 63 % | 10-Tage „Seitwärts“ mit +14,6 % |
| `ra_87ecd9af` 4h · 9 Regime · EMA 26d | ema | 97,5 % | Final: 61 % · exakt 32 % | – |
| `ra_e73b3585` 1d · 9 Regime · EMA 25d | ema | 99,3 % | Final: 62 % · exakt 28 % | – |
| `ra_9fbe22f1` 15m · 5 Regime · reactive | reactive | 48,6 % | 42 % | 2725 Live- vs. 130 Final-Segmente, Ø Lag 16 Tage |

Für ETH liegen die Referenz-Treffer bei 52–58 %. Die Note „gut“ aller EMA-Analysen ist also **nicht** durch
eine unabhängige Kennzahl gedeckt; realistisch ist „mittel“. Ursache der langen EMA-Perioden (25–30 Tage statt
Standard 14): der **EMA-Vergleich wählte die Periode nach Live=Final** – dieses Maß bevorzugt systematisch die
längste/trägste EMA (dort sind Live- und Final-Steigung am ähnlichsten).

**Fix:**
- Neues reines Modul `services/regime_reference.py`: Live-Sicht gegen die **detektor-unabhängige Referenz**
  (`regime_truth.centered_labels`, dieselbe wie in der wissenschaftlichen Kalibrierung) – gesamt, Holdout,
  innere Validierung, Erkennungs-Lag, verpasste Phasen.
- Jede neue Analyse speichert je Symbol `reference` (additiv, `_symbol_payload`); `run_analysis` übergibt jetzt
  auch den inneren Validierungs-Anker (`bounds.inner_start_ts`, bisher nur im EMA-/Kombi-Vergleich).
- `regime_quality`: Note wird durch die Referenz **nach oben begrenzt** (Schwellen 65/55 %), Klartext erklärt
  den Fall „mit sich selbst einig, verpasst aber Phasen“. Bestandsanalysen ohne `reference` bleiben unverändert.
- EMA-Vergleich: Auswahl nach `inner_reference_pct` (Kette: Referenz → Live=Final → Training), Spalten
  „Referenz innere Val. ★ / Referenz Holdout / Lag·verpasst“; Banner nennt die Referenz.
- UI `RegimeQualityCard`: Referenz-Treffer, Lag, verpasste Phasen, Tooltips korrigiert („ehrlichste Kennzahl“
  ist jetzt die Referenz).

## 3. Bewertung der bisherigen Forschungsergebnisse (7 Analysen, keine Kalibrierung/Ablation/Freigabe)

- **Keine Analyse hat Kalibrierung, Ablation, Walk-Forward oder Freigabe** (`kept` nur bei 2 Analysen).
  Das Nachweis-Gate der Brücke (`regime_release.validate_release`) ist damit für keine Analyse erfüllt – gut so,
  denn der Live-Fensterfehler (1.) hätte jede Freigabe entwertet.
- **15m/reactive (`ra_9fbe22f1`)**: unbrauchbar für Umschaltung – Live flackert 20× häufiger als Final,
  Ø Erkennungs-Verzögerung 16 Tage bei Ø Phasendauer 8 Tage (Lag > Phase). Zusätzlich `warmup_bars = 22 811`
  (237 Tage auf 15m) – die Regressions-Horizonte des adaptiven Profils skalieren mit dem Zeitraum, nicht mit
  dem Timeframe. Empfehlung: 15m nur mit Detektor ema/kombi und `min_phase_days` ≥ 2, oder 1h nutzen.
- **EMA 1h/4h/1d**: Segmentdauer 20–60 Tage, Validierung meist bestanden, aber Referenz-Treffer 52–70 % →
  „mittel“. Für 5-15-Tage-Phasen (Daytrading-Ziel) sind 25–30-Tage-EMAs zu träge; nach dem Auswahl-Fix
  den EMA-Vergleich mit 5/9/14 Tagen wiederholen und die Referenz-Spalte lesen.
- **`per_coin` ist bei Engine v2 redundant**: das Modell ist nur Konfiguration + Statistik; je-Coin-Modelle
  liefern identische Labels wie „kombiniert“ (siehe DB: exakt gleiche Kennzahlen), kosten aber doppelte
  Rechenzeit und Speicher. 📝 Scope `combined` genügt (kein Code-Eingriff, Verhalten bleibt).
- `adapt_applied = None` bei 1h/15m trotz `auto_adapt`: die `engine_config` der Oberfläche enthält bereits
  absolute Fenster (Horizonte 10,8/25,2/… Tage, `min_hold_days` 4,32) – manuell gesetzte Felder überschreiben
  das Profil (gewollt). Nur `adapt.profile` zeigt dann irreführend „–“. 📝 Kosmetik.

## 4. Weitere Prüfpunkte (Code) – Ergebnis

| Prüfpunkt | Ergebnis |
|---|---|
| Lookahead in Live-Labels (reactive/ema/kombi) | **kein** Lookahead gefunden: Pivots werden erst am Bestätigungs-Bar gesetzt, `argmin(low[j0:i+1])` nur Vergangenheit, Drift-Detektor kausal, EMA-Bestätigung kausal. Final-Sicht nutzt bewusst Zukunft (zentrierte Steigung, Absorption). |
| Modell-Konfiguration Lab ↔ Live | identisch: `model.config` speichert aufgelöste Bars; `classify_series` nutzt sie unverändert. Einzige Fensterabhängigkeit war die Historienlänge (1.). |
| Auto-Profilwahl (`build_model`, `adapt_profile=auto`) | läuft nur auf `train_hist` → kein Holdout-Leck. `_profile_quality` bewertet Final-Labels + Live=Final + Rückblick – Live=Final-Term hat dieselbe Schwäche wie 2., wirkt aber nur auf die Profilwahl (fein/standard/grob). 📝 |
| Kombi-Kalibrierung (`run_kombi_calibrate`) | Score enthält Live=Final (innere Validierung, R10 ✓) – gleiche Schwäche wie 2., Referenz-Anteil fehlt. 📝 nächster Schritt. |
| Reaktiver Detektor – `warm` | nur ATR-Warmup; EMA-Anker/Vola-Referenz werden in den ersten ~150 Tagen einer Analyse ungewärmt genutzt (Lab-Labels dort minderwertig). Nicht maskiert, um Bestandsanalysen nicht zu verändern. 📝 |
| Regime-Gate Quelle `own` (`regime_gate._detect`) | baut je Aufruf ein eigenes Modell auf 30 Tagen (adaptives Profil → andere Taxonomie als das Lab). Ist als Legacy-Kurzfrist-Gate dokumentiert; Quelle `lab` ist der korrekte Weg (jetzt mit 1.). 📝 |
| K-Means (Engine v1) | R16 behoben (`regime.py`), nur Altbestand. |

## 5. Regressionstests

- Neu: `backend/tests/test_regime_pruefung_2309.py` (13 Tests, unit, ohne Netz): Historien-Vertrag, Fenster-
  Unabhängigkeit für ema/reactive, `stale` bei zu kurzer Historie, Referenz-Vergleich (Holdout/innen), Noten-
  Deckelung, EMA-Auswahlkette, Nachweis „Live=Final > Referenz + 10 pp“ beim trägen EMA, Health-Check.
- Bestand grün: `test_regime_engine`, `test_regime_lab_clarity`, `test_structural_regime`, `test_regime_gate`,
  `test_regime_release*`, `test_regime_v2_and_gate_domain`, `test_ap13_ablation_uncertainty`,
  `test_regime_bridge_hardening`, `test_engine_split_and_regime_core`, `test_regime_cockpit`, u.a.
- Skripte (nur lesend): `backend/scripts/inspect_regime_research.py` (Forschungsstand aus der DB),
  `backend/scripts/probe_live_window_dependence.py` (Fenster-Beweis; `PROBE_WINDOW_FROM_MODEL=1`).
- Testing-Agent (lesend, Preview gegen Produktiv-Atlas): `backend/tests/test_regime_readonly_iter17.py`
  (list/detail/health/engine-defaults/regime-phase) + UI (Login → Werkzeuge → Regime-Lab → Analyse → Qualitätskarte,
  EMA-Vergleich-Sektion) – alles grün, keine JS-Fehler.
- ⚠️ **Hinweis Live-Tests:** Die Repo-eigenen API-Tests (`test_*api*.py`, `test_ap13_*`, `test_regime_iter*`)
  starten echte Analyse-Jobs. Gegen eine Instanz mit Produktiv-DB erzeugen sie Test-Analysen (in dieser Session
  11 Stück, wieder gelöscht – nur BTC-Einzelanalysen mit Testnamen von 08:39–08:40 UTC). Nur Unit-Tests
  (`-m unit` / ohne Server) oder gegen eine Dev-DB (`DB_NAME=crypto_scanner_dev`) laufen lassen.

## 6. Empfohlene nächste Schritte (Reihenfolge)

1. **Neu analysieren statt Bestand freigeben**: 1h, Krypto-Kern (BTC/ETH/SOL/BNB), 360–720 d, Training 75 %,
   EMA-Vergleich 5/9/14/21 → Periode nach **Referenz** wählen → „Regime suchen & speichern“ → Karte lesen:
   Referenz-Treffer Holdout ≥ 65 % und Lag ≤ ⅓ der Ø Phasendauer.
2. Kombi-Kalibrierung und `_profile_quality` auf die Referenz umstellen (gleicher Baustein `regime_reference`).
3. Brücke: optional Anker bis `bounds.start_ts` (max. 600 d) für den reaktiven Detektor (0 % statt 3 %).
4. `regime_gate` Quelle `own` in der UI als „Kurzfrist-Legacy“ kennzeichnen oder auf `lab` umstellen.
5. Erst danach Shadow-Freigabe → 4 Wochen Reward-Split beobachten → dynamische Strategien.

## 7. Geänderte / neue Dateien

- `backend/services/regime_engine.py` – `detector_warmup_bars`, `required_history_bars`, `required_history_days`
- `backend/services/structural_regime.py` – Historien-Vertrag in `_compute`/`context_from_model`
- `backend/services/regime_reference.py` – NEU (Referenz-Qualität, rein)
- `backend/services/regime_lab.py` – `reference` je Symbol, innerer Anker in `run_analysis`, EMA-Vergleich
- `backend/services/regime_quality.py` – Referenz-Kennzahlen, Noten-Deckelung
- `backend/services/research_validation.py` – `select_best_row(chain=…)`
- `backend/services/regime_bridge_health.py` – Check `structural_short_history`
- `frontend/src/components/RegimeQualityCard.js`, `RegimeDetectorTools.js`, `RegimeBridgeHealth.js`
- `backend/tests/test_regime_pruefung_2309.py`, `backend/scripts/inspect_regime_research.py`,
  `backend/scripts/probe_live_window_dependence.py`

Render-Deploy: keine neuen Abhängigkeiten, keine Ordner-Änderungen, alle DB-Felder additiv. Der lokale Worker
trägt eine Kopie von `services/` → nach dem Deploy Worker-Paket neu laden (sonst fehlen `reference`-Felder).
