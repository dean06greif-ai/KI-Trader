# Analyse: Dynamische Strategien & Regime Lab (06/2026)

Grundlage: Branch `conflict_051126_1912` (Code gelesen, keine Änderungen). Geprüft wurden u. a.
`services/dynamic_strategy.py`, `optimizer.py` (Dynamik-Modus), `regime_opt.py`, `regime_evidence.py`,
`regime_engine.py`, `regime_jump.py`, `regime_selection.py`, `regime_release.py`, `structural_regime.py`,
`dynamic_runtime.py`, `dynamic_live.py`, `regime_dwell.py`.

---

## 1. Kurzfazit

- **Deine Idee ist richtig.** Strategien nur in passenden Marktphasen handeln zu lassen, ist ein bewährter Ansatz.
  Eure eigenen Daten zeigen das schon: TrendFolge2 macht +0,27 R in `trend_down_volatil` und −0,32 R in `trend_up_volatil`.
- **Die technische Basis ist gut.** Es gibt kein Lookahead im Live-Pfad, nur abgeschlossene Kerzen, eine
  Haltedauer-Bremse, ein Jump-Modell, einen Holdout und Freigabestufen. Das muss nicht neu gebaut werden.
- **Das Problem liegt bei der Bewertung, nicht bei der Erkennung.**
  1. Das Lab bewertet Regime danach, wie gut die Live-Erkennung eine Rückblick-Linie trifft (Macro-F1 gegen die Referenz).
     Eine Live-Erkennung reagiert immer verzögert. Deshalb landen fast alle Kandidaten bei 45–55 %.
     Darum findest du keine „guten“ Regime, obwohl einige trotzdem nützlich sein können.
  2. Für die dynamischen Strategien sind die Prüfungen zugleich **zu weich und zu hart.**
     - Zu weich: Schon 3 Trades mit Gewinn gelten als „bestanden“.
     - Zu hart: Erst ab 30 Trades je Regime ist eine Freigabe möglich.
     Ergebnis: Viele Zufallstreffer entstehen in der Suche, und trotzdem wird keine Strategie live freigegeben (0 dynamische Trades).
  3. Es gibt nur **einen Timeframe je Regime** und keinen übergeordneten Filter (Makro-Regime). Altcoins sehen den BTC-Kontext nicht.
- **Der KI-Trader-Bereich ist leer**, weil `structural_regime` nur freigegebene Lab-Analysen je Anlageklasse nutzt.
  Ohne Freigabe steht dort „unknown“. Es fehlt ein einfacher, fester Weg zu einer Freigabe je Anlageklasse.

---

## 2. Befunde – Dynamische Strategien

| # | Schwere | Befund | Stelle | Wirkung |
|---|---|---|---|---|
| D1 | hoch | Die „Validierung je Regime“ nimmt nur das **Ende jeder Phase** (die letzten 25 % jedes Abschnitts). Diese Daten hängen zeitlich direkt am Trainingsteil und sind immer das Phasen-Ende, wenn der Trend ausläuft. | `dynamic_strategy.split_segments` | Die Validierung ist nicht unabhängig und hat eine systematische Schieflage. |
| D2 | hoch | Die Auswahl nutzt genau diese Validierung (`passed[-1]`). Bestanden heißt nur: mindestens 3 Trades und PnL > 0. Die Suche testet aber rund 40 Kandidaten × 4 Runden. | `discover_regime_strategy`, `validation_passed` | Bei so vielen Versuchen besteht fast sicher irgendeine Variante zufällig (Overfitting durch Auswahl). |
| D3 | mittel | Das Urteil „dynamisch besser als statisch“ vergleicht das absolute Test-PnL. Es reichen 5 Trades und 5 % Vorsprung. | `build_verdict` | Das Urteil ist reines Rauschen. Eine Unsicherheitsrechnung fehlt. |
| D4 | mittel | Die Zielfunktion `pnl × (0,5 + Winrate)` belohnt einzelne Ausreißer. `pnl_ex_top2` wird berechnet, aber nur angezeigt. | `_score` | Strategien mit einem einzigen Glückstreffer gewinnen die Suche. |
| D5 | mittel | Die feste Grenze von 30 Holdout-Trades je Regime hat keine Abstufung. | `regime_evidence.MIN_TRADES` | Dadurch ist alles entweder blockiert oder wird von Hand freigeschaltet. Einen Mittelweg gibt es nicht. |
| D6 | mittel | Regime-Wahrscheinlichkeiten (`probs`, `conf`) werden berechnet, aber nicht genutzt. Positionsgröße und Neueinstiege bleiben in unsicheren Übergängen gleich. | `regime_jump.detect_jump`, `dynamic_runtime` | Gerade in Übergängen entstehen die meisten Fehlsignale. |
| D7 | mittel | Das Regime kommt aus nur einem Timeframe. Ein Makro-Filter (1D) fehlt. | Analyse-Modell | Eine kurze Gegenbewegung wird als echter Regimewechsel behandelt. |
| D8 | niedrig | Altcoin-Regime berücksichtigen das BTC-Regime nicht. | Detektor-Features | Bei einem BTC-Abverkauf wird der Altcoin trotzdem „bullish“ gehandelt. |

Was bereits korrekt ist (bleibt unverändert):
- Kausale Live-Labels als Standard (`label_basis_of`).
- Zwangsschluss beim Regimewechsel identisch in Backtest und Live (`on_switch=close`).
- Abgeschlossene Kerzen (`drop_partial`).
- Haltedauer plus 2 Bestätigungen (`regime_dwell`).
- Holdout-Zähler (`research_validation`).

---

## 3. Befunde – Regime Lab

| # | Schwere | Befund | Wirkung |
|---|---|---|---|
| R1 | hoch | Champion und Freigabe richten sich vor allem nach der **Übereinstimmung mit einer Rückblick-Referenz** (zentrierte OLS oder HMM, beide sehen die Zukunft). | Damit wird „Verzögerung“ bestraft, nicht „Nutzlosigkeit“. Ein verzögerter, aber stabiler Detektor kann für Strategien sehr wertvoll sein. |
| R2 | hoch (Bedienung) | Ein 9-Stufen-Prozess mit rund 40 Karten: Autopilot, TF-Kette, Feinsuche, Pooling, Champion, Fairer Vergleich, Kalibrierung, Shadow, Active … | Ohne Fachwissen kaum zu bedienen. Darum steht im KI-Trader nichts. |
| R3 | mittel | Es gibt kein festes Standard-Rezept je Anlageklasse (Timeframe, Zeitraum, Detektor). | Jeder Lauf ist ein neues Experiment, und jedes Experiment erhöht das Overfitting-Risiko. |
| R4 | mittel | Das 9er-Modell mischt Richtung und Volatilität zu einer Regime-ID. | Es flackert zwischen benachbarten Vola-Stufen. Dazu kommen pro Regime weniger Trades. |
| R5 | mittel | Indizes, Rohstoffe und Forex (Perps) haben oft nur kurze Historie. | Zu wenige Phasen für eine verlässliche Erkennung je Asset. |

---

## 4. Einschätzung der anderen KI

| Aussage | Meine Bewertung |
|---|---|
| 8H als Haupt-Regime, 1D als Makro-Filter (Krypto) | **Stimmt als Startpunkt.** Hier ist derzeit 1h üblich, das ist zu nervös. |
| 4H als Bestätigung | **Erst weglassen.** Jede weitere Ebene bringt Parameter und damit Overfitting. Erst einbauen, wenn 1D + 8H nachweislich nicht reichen. |
| Bull/Bear und Volatilität trennen | **Stimmt.** Richtung = Regime, Volatilität = Größen-Faktor (kein eigenes Regime). |
| BTC und ETH getrennt plus Gesamtmarkt | **Stimmt.** Das passt zum vorhandenen Partial Pooling (Gruppe + Coin-Anpassung). |
| HMM mit Wahrscheinlichkeiten | **Teils.** Wahrscheinlichkeiten ja. Live aber das **vorhandene Jump-Modell** nutzen statt eines HMM: Ein HMM springt bei jedem Neu-Training seine Zustände um und flackert mehr. Das Jump-Modell liefert schon Wahrscheinlichkeiten, sie werden nur nicht genutzt. |
| 8 Features (EMA, ADX, RSI, ATR, BTC-Dominanz …) | **Zu viele** für die wenigen Phasen, die es gibt. Ich würde 3–4 nutzen: Trend (vola-normiert), Volatilität, Marktstruktur, BTC-Regime für Alts. BTC-Dominanz braucht eine externe Datenquelle, das wäre später. |
| 4H, 8H, 12H und 1D mit Walk-Forward vergleichen | **Gut.** Aber nur diese 4 Varianten und mit festen Regeln, sonst wird der Vergleich selbst zum Overfitting. |
| „Erkennen oder vorhersagen?“ | **Erkennen.** Eine Vorhersage für 24–72 h ist praktisch eine Kursprognose, und die ist nicht verlässlich. Wichtig ist aber: Die Erkennung wird danach **bewertet**, was danach passiert (Rendite und Strategie-R in den folgenden Tagen). |

---

## 5. Empfehlung – so würde ich es aufbauen

### 5.1 Pro Anlageklasse, nicht ein Modell für alle
Gemeinsame Struktur je Klasse (Pooling) plus kleine Anpassung je Asset. Das gibt es technisch schon; was fehlt, ist das feste Rezept.

| Anlageklasse | Makro-Filter | Haupt-Regime | Handel auf | Leitmarkt-Kontext |
|---|---|---|---|---|
| Krypto | 1D | **8h** | 5m–1h | BTC-Regime für Alts |
| Indizes | 1W | **1D** | 15m–1h | – |
| Rohstoffe | 1W | **1D** | 15m–1h | – |
| Forex | 1D | **4h** | 15m–1h | – |

Für Nicht-Krypto: Wenn die Perp-Historie zu kurz ist, die Erkennung auf längerer Historie des Basiswerts kalibrieren.

### 5.2 Zwei Achsen statt 9 Regime
- **Richtung** (aufwärts, seitwärts, abwärts) steuert, welche Strategie handeln darf.
- **Volatilität** (ruhig, normal, hoch) steuert nur die Positionsgröße.
- **Makro × Haupt** ergibt eine einfache Matrix:

| Makro | Haupt | Bedeutung |
|---|---|---|
| Bull | Bull | volle Größe |
| Bull | Bear | Korrektur: keine Shorts oder halbe Größe |
| Bear | Bull | Gegenbewegung: keine Longs |
| Bear | Bear | volle Größe |

### 5.3 Regime nach Nutzen bewerten
Die neue Hauptnote im Lab setzt sich zusammen aus:
1. Trennschärfe: Rendite danach im Aufwärts- vs. Abwärts-Regime (out-of-sample).
2. Stabilität: Ø Phasendauer und Ping-Pong-Rate.
3. Verzögerung.
4. Spreizung der Strategie-R je Regime.

Die Übereinstimmung mit der Rückblick-Referenz bleibt als Nebeninfo sichtbar.

### 5.4 Overfitting bei wenigen Trades – meine klare Antwort
„Ein bisschen Overfitting“ hilft nicht. Es fühlt sich im Backtest gut an und kostet live Geld. **Wenige Trades
lassen sich aber trotzdem sinnvoll nutzen**, mit einer Glaubwürdigkeits-Gewichtung statt einer harten Grenze:

```
erwarteter Wert je Regime = (n · Ø R im Regime + k · Ø R der Strategie gesamt) / (n + k)      k ≈ 20 Trades
```

| Trades im Regime | Eigenes Gewicht |
|---|---|
| 10 | 33 % |
| 30 | 60 % |
| 80 | 80 % |

Zusätzlich gilt:
- **Positionsgröße nach Evidenz:** wenige Trades = kleine Größe. Freigabe nur, wenn die Untergrenze (Ø − 1 Standardfehler) über 0 liegt.
- **Unabhängige Validierung:** komplette spätere Phasen mit Zeitabstand, nicht das Ende derselben Phase.
- **Wahrscheinlichkeit „dynamisch > statisch“** per Bootstrap, z. B. ≥ 80 %, statt „5 % mehr PnL“.

So sperrt das System nicht mehr alles, und es handelt keine Zufallstreffer mit voller Größe.

---

## 6. Umsetzungsplan (modular, rückwärtskompatibel)

Grundsätze für alle Etappen:
- Jede Etappe ist ein neues, reines Modul mit Tests.
- Bestehende Funktionen bleiben unverändert. Neues läuft über Einstellungen, deren **Standard = altes Verhalten** ist.
- Gespeicherte Analysen und Strategien bleiben gültig.
- Ordnerstruktur bleibt (Render-Deploy).

| Etappe | Inhalt | Neue Bausteine |
|---|---|---|
| E1 | Statistik-Kern: Glaubwürdigkeits-Gewichtung, Untergrenze, Bootstrap. Eingebunden in die Validierung je Regime, das Urteil dynamisch vs. statisch und die Regime-Evidenz (abgestuft statt harter 30er-Grenze). | `services/evidence_stats.py` |
| E2 | Zeitliche Validierung: spätere Phasen plus Embargo statt Phasen-Ende. Ausreißer-robuste Zielfunktion. | Option `regime_val_mode="time"` in `dynamic_strategy` |
| E3 | Multi-Timeframe-Regime: Makro (1D/1W) × Haupt (8h/1D/4h) plus BTC-Kontext für Alts. Neue Ebene `macro` im MarketContext. Optionaler Makro-Filter je dynamischer Strategie. | `services/regime_hierarchy.py` |
| E4 | Nutzen-Note als Hauptbewertung im Lab und beim Champion. Die Referenz-Note bleibt sichtbar. | `services/regime_usefulness.py` |
| E5 | **Regime-Assistent je Anlageklasse:** ein Knopf, festes Rezept aus 5.1, Ergebnis als Ampel. Bei Grün Shadow-Freigabe, damit der KI-Trader-Bereich gefüllt wird. | `RegimeAssistant.js` plus Job, der die vorhandenen Werkzeuge in fester Reihenfolge nutzt |
| E6 | Wahrscheinlichkeit und Evidenz steuern die Positionsgröße. In unsicheren Übergängen kein Neueinstieg (optional). | `dynamic_runtime`, Größen-Faktor |

Vor jeder Etappe:
- Regressionstests der bestehenden Pfade laufen (vorhandene 443 Testdateien plus neue Tests je Modul).
- Ein Vorher/Nachher-Vergleich auf denselben Daten.

---

## 7. Sicherheit (dringend)

Im Chat wurden echte Zugangsdaten im Klartext geteilt: `BITUNIX_API_KEY`, `ADMIN_PASSWORD`, `GATEWAY_TOKEN` und die IBKR-Kontonummer.

Bitte so schnell wie möglich ändern:
1. Bei Bitunix den API-Key löschen und einen neuen erstellen.
2. In Render das Admin-Passwort und den Gateway-Token ändern.
