# Prüfbericht 24.09.2026 – POL-Liquidation, Shadow-Widerspruch, Regime-Lab-Benchmarks

Basis: Branch `conflict_230926_1951`. Produktiv-DB und Bitunix-API wurden **nur lesend** genutzt.
Tests: `backend/tests/test_pruefung_2409.py` (15 Unit-Tests, ohne Netz/DB) + bestehende Regime-/Watchdog-Suiten grün.

---

## 1. POL-Trade 23.09. (POLUSDT-1790173213520) – warum Liquidation statt SL? ✅ behoben

**Beleg (Bitunix-Positions-Historie):**

| | lokal (Website) | Börse (Wahrheit) |
|---|---|---|
| Entry | 0.10318 (Signalpreis = Close der 5m-Kerze) | **0.10144** (Market-Fill im Flash-Wick 14:20 UTC, 1m-Tief 0.09885) |
| SL | 0.106694 (+3,4 % vom geplanten Entry) | +5,2 % vom echten Fill |
| Hebel / Liq | 22,7x → Liq 0.107209 (hinter dem SL ✓) | 23x → **Liq 0.1052 (vor dem SL ✗)** |
| Ergebnis | – | `liqQty` gesetzt = Zwangsliquidation, −1,64 $ = gesamte Marge (SL hätte −1,28 $ gekostet) |

**Ursache:** Bitunix liefert in `get_order_detail` bei Market-Orders **kein `avgPrice`** → der Bot behielt den
Signalpreis als Entry und rechnete die Liq vom falschen Entry. Die knappe Marge (Risikobudget verkleinerte
19 → 1,66 $, dadurch hoher Hebel) ließ nur ~0,5 % Puffer zwischen SL und Liq – die Fill-Abweichung von −1,7 %
hat ihn komplett aufgefressen. Die Anzeige „Exit 0.1052“ war die Liquidation, nicht dein SL.

**Fix (neues Modul `services/fill_liq_guard.py`, eingebunden in `bitunix_trade.py`):**
- Direkt nach jedem Live-Open **und** in jedem Watchdog-Zyklus: echte Position lesen (`avgOpenPrice`,
  `liqPrice`, `margin`). Entry wird auf den echten Fill korrigiert (Risiko/R neu berechnet, Event „FILL-ABGLEICH“).
- Liegt der SL nicht mit Puffer (Standard 0,3 %) vor der **echten** Börsen-Liq:
  1. **Marge nachschießen** (Liq wandert hinter den SL, dein Strategie-SL bleibt) – wenn freies Kapital reicht,
  2. sonst **SL vor die Liq ziehen** (kleinerer Verlust als Liquidation),
  3. liegt der Kurs schon jenseits → **schließen**. Jede Aktion: Event „LIQ-SCHUTZ …“ + Benachrichtigung.
- Liquidationen aus der Börsen-Historie werden als solche verbucht (`liquidated: true`, Event „LIQUIDATION an der Börse“).
- Hinweis: die aktuell offene POL-Position (379 Stk) hat SL 0.106018 vs. Börsen-Liq 0.10621 – nur 0,18 %
  Puffer; nach dem Deploy greift der Guard beim nächsten Watchdog-Lauf (Marge +~0,05 $).

## 2. Widerspruch Copilot ↔ Shadow-Trades ✅ behoben

Du hattest recht: die **122 Shadow-Trades gehören komplett zur gelöschten Analyse `ra_2e8723cf`**
(17× „bulle“, 105× „seitwärts“, 18.–23.09.). Deine aktuelle Analyse „Regime Krypto 1h“ (`ra_d41ad11b`) ist gar
nicht freigegeben (Stufe none) → 0 Shadow-Trades. `GET /api/regime-lab/releases` zählte alle Rewards der letzten
90 Tage ohne Analyse-Filter, die Oberfläche gab diese Zahl als „Shadow-Trades dieser Analyse“ an Copilot/Badges.

Fix: Zählung je Analyse (`shadow_by_aid`), Trades gelöschter Analysen separat (`orphan_shadow`, zählen für keine),
Copilot-Kontext/Prompt entsprechend. Beim Löschen einer Analyse wird der Struktur-Kontext des KI-Traders sofort
geleert (vorher stand dort noch die gelöschte Freigabe mit Stufe „shadow“).

## 3. Freigabe – deine Fragen

- **Ein Klick reicht.** „Behalten vorschlagen“ in der geöffneten Analyse markiert alle Regime mit ≥ 5 Abschnitten
  des **kombinierten** Modells. Die Freigabe (Shadow/Wirksam) läuft bei Scope „both“ immer über das kombinierte
  Modell – je Coin einzeln ist dafür **nicht** nötig (je-Coin-Modelle liefern bei Engine v2 identische Labels).
- **Wo sehe ich es?** Neu: am Knopf steht „✓ N behalten“. Bei „Regime Krypto 1h“ sind 9 Regime behalten.
- Freigabe gilt je Assetklasse **und** Horizont-Band (Intraday ≤ 1h / Swing ≥ 2h); eine neue Freigabe ersetzt nur
  die des gleichen Bands.

## 4. Regime-Lab – sind die Benchmarks sinnvoll? **Nein, in drei Punkten verzerrt** ✅ behoben (Referenz v2)

**4a. Die „unabhängige“ Referenz war nicht unabhängig.** Fenster = mittlerer Horizont **des geprüften Modells**,
Mindestlänge = 0,8 % des Zeitraums. Folge (BTC 1h, 810 Tage, gemessen):

| Referenz-Fenster | Ø Referenz-Phase | Seitwärts-Anteil |
|---|---|---|
| alt 1h „fein“ (40,5 d, Merge 6,5 d) | **42,8 Tage** | 66 % |
| 10 d / Merge 2 d | 15,1 Tage | 74 % |
| **7 d / Merge 2 d (neu, Standard)** | **9,8 Tage** | 72 % |
| 5 d / Merge 2 d | 8,0 Tage | 73 % |

Deine Detektoren (Ziel 4–14 Tage) wurden also gegen ~43-Tage-Trends benotet; 4h/15m/1h-Analysen hatten
jeweils ein anderes Fenster (22 / 58 / 38–40 Tage) → **Timeframe-Vergleiche waren nicht fair**.

**4b. „Immer seitwärts“ schlug die Noten-Schwelle.** Die Referenz ist zu 66–90 % seitwärts; ein Detektor, der nie
umschaltet, erreicht ~70–80 % Roh-Treffer – über der alten „gut“-Schwelle (65 %). Neu: Note über den
**klassen-balancierten** Treffer (konstant = 33 %; gut ≥ 60, mittel ≥ 50) plus **Skill** über der Mehrheits-Baseline.

**4c. „Ø Phasendauer“ zählte Vola-Stufen mit.** Im 9er-Modus ist jeder Wechsel ruhig↔volatil ein neues Segment.
Beispiel „Regime Krypto 1h“: angezeigt 12 Tage – die **Richtung** (auf/seit/ab) wechselt bei BTC aber nur alle
~27 Tage (30 Richtungswechsel in 810 Tagen). Neu: „Ø Richtungs-Phase“ (Benchmark + Autopilot-Strafe).

**Nachmessung deiner besten Analyse mit Referenz v2** (`ra_d41ad11b`, kombi, BTC 1h, Holdout):
Live=Final 94 % · Roh-Referenz 72 % · **balanciert 44 %** · Baseline „immer seitwärts“ 81 % → **Skill −45 %** ·
Lag 2,5 d · 40 % verpasste Phasen. Ehrlich bewertet ist sie **schwach** für ein 4–14-Tage-Ziel (zu träge in der
Richtung). Die Autopilot-Läufe davor haben sie trotzdem gewählt, weil der Score zu 50 % aus Live=Final
(~93–98 % bei allen Varianten, kaum Trennkraft) und zu 50 % aus der verzerrten Referenz bestand.

**4d. Ablation verglich Äpfel mit Birnen.** „kombi 92,6 % vs. ema 72,6 %“ war Live=Final – Selbst-Übereinstimmung
verschiedener Detektoren ist nicht vergleichbar. Neu: Beitrag/Bestwahl/Freigabe-Nachweis über Referenz v2
(neue Spalte „Referenz v2 innen/Holdout“); Altläufe bleiben unverändert lesbar.

**Umsetzung (rückwärtskompatibel, alte Analysen behalten ihre Werte):**
`regime_truth.centered_labels` (optionale Schlüssel `reference_window_days`/`reference_min_days`),
`regime_reference` (v2: `reference_cfg`, Baseline, Skill, balanciert, Richtungs-Phase, Training-balanciert),
`regime_lab` (Referenz v2 je Symbol, Ablation-Spalten/Verdikte), `regime_quality` (Note/Benchmark v2),
`regime_autopilot` (Score: 75 % Referenz v2 balanciert ohne Holdout + 25 % Live=Final; Strafe auf Richtungs-Phase),
`regime_release` (Ablation-Delta v2), UI `RegimeQualityCard`, `RegimeDetectorTools`.

**Ziel-Phasendauer 4–14 Tage:** sinnvoll für Daytrading-Umschaltung – aber nur, wenn sie auf die **Richtung** und
gegen eine Referenz mit passendem Fenster (7 d → Ø ~10 d) gemessen wird. Genau das ist jetzt der Fall.

## 5. Ablation schneller ✅

Ablation lief **immer in der Cloud** (Render 512 MB) und lud dort für jede Variante-Serie alle 1m-Kerzen
(1080 Tage × 13 Coins ≈ 20 Mio.) neu von der Börse – der lokale Worker hat sie nach dem Autopilot schon im
Disk-Cache. Neu: Ausführung „Lokal“ für die Ablation (Worker ≥ **1.14.0**, Paket neu herunterladen).

## 6. Empfehlung – bevor du viele Stunden investierst

1. Deploy + Worker-Paket 1.14.0 laden.
2. **Autopilot neu laufen lassen** (1h, Krypto-Kern, Ziel 4–14 d): Scores sind jetzt ehrlich; alte Bestwerte
   nicht mit neuen vergleichen. Danach Analyse, dann Ablation lokal.
3. Auf der Qualitätskarte zählen: *Referenz balanciert (Holdout)* ≥ 60 %, *Skill* > 0, *Ø Richtungs-Phase* 4–14 d,
   Lag ≤ ⅓ Phase. Live=Final nur noch als Stabilitäts-Hinweis.
4. 15m: Horizonte skalieren mit dem Zeitraum (Warmup 22 811 Kerzen) – für 15m erst nach 1h-Ergebnis testen.
5. Erst dann Shadow-Freigabe.

Skripte (nur lesend): `backend/scripts/reference_window_probe.py`, `backend/scripts/reference_v2_probe.py`.
