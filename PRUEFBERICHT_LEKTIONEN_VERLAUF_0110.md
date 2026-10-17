# Prüfbericht 01.10.2026 – Lektionen, KI-Verlauf, Regime-Copilot, MarketMaker (MM)

Branch-Basis `conflict_300926_1955`. Preview lief mit separater lokaler Test-DB und
`AI_TRADER_LOCAL_DISABLE=1` (keine Trades, keine Broker-Keys) – die Produktiv-DB wurde nicht angefasst.

## 1. Lektionen (KI-Trader) – wie sie entstehen und ob das Sinn macht

**Ablauf (gut durchdacht, bleibt so):**
1. Lernlauf (`services/ai_learning.run_learning`) nach Trade-Schluss (≥ 15 Min Abstand), täglich oder manuell.
   Das LLM schlägt Lektionen vor; neue Lektionen brauchen ≥ 5 bewertete Ergebnisse, Löschungen ≥ 12
   (`ai_validation`), Wiedererkennung in ≥ 2 Läufen mit neuen, disjunkten Trades (`_bump_lesson_candidate`).
2. Filter gegen Unsinn: Sizing-/Absolut-Regeln, Kleine-Stichproben-Texte, Abgleich mit dem MasterPrompt.
3. Pflege: Duplikate (Titel-Ähnlichkeit), Themen-Konflikte (neueste gesperrte gewinnt, Rest „ersetzt“),
   Ablaufdatum → „zurückgestellt“ statt gelöscht, Reaktivierung verlängert die Gültigkeit (14 → 90 T),
   Löschung erst nach 45 T ohne Reaktivierung.
4. Wirkungsmessung (Lektions-Bilanz): Attribution über `[id:…]` im Prompt, Vergleichsgruppe „ohne“,
   HOLD-Gegenprobe für verhinderte Trades (netto nach Kosten), Urteil EDGE / neutral / hinderlich /
   zu weich / sammelt. Das Urteil ist nur Information – Änderungen laufen weiter nur über die Gates.

**Gefundene Logikfehler – behoben:**

| # | Problem | Fix |
|---|---------|-----|
| L1 | HOLD-Gegenprobe: HOLDs ohne auswertbares `would_be` wurden nie „fällig“ und nie markiert. Die Warteschlange liest die ältesten 36 Einträge – sammeln sich solche Einträge an, blockieren sie **alle** echten Gegenproben dauerhaft. | `_pending` nimmt Einträge ohne Frame mit, sie werden sofort als `no_frame` markiert (ohne Kerzenabruf). |
| L2 | Kosten der Gegenprobe pauschal 0,06 %/Seite. Bei Forex (IBKR ≈ 0,002 %) wirkten verhinderte Trades systematisch schlechter → Lektionen erschienen zu gut („EDGE“). Auch eine geänderte Krypto-Gebühr wurde ignoriert. | `configured_fee_pct`: Krypto = `futures_taker_fee_pct`, Forex = IBKR-Modell (`fee_model`). |
| L3 | Bilanz: Lektion mit 1–4 angewendeten Trades, aber genug verhinderten Trades erreichte die Mindest-Stichprobe, der Netto-Beitrag blieb jedoch `None` → Urteil konnte nie „EDGE“ werden. | `contribution_r`: unter 5 angewendeten Trades zählt die gemessene Gegenprobe. |
| L4 | `PLAN_LEKTIONS_BILANZ.md` stand noch auf „PLAN, nicht umgesetzt“. | Status aktualisiert. |

**Hinweise (bewusst nicht geändert):**
- Paper + Echtgeld werden in der Bilanz gemeinsam gewertet. Das passt, weil beide mit derselben Live-Logik laufen
  und in R (PnL / Risiko) normiert sind. Sammel-Trades bleiben getrennt.
- Die Vergleichsgruppe „ohne“ ist nach Anlageklasse und Zeitraum gefiltert, aber nicht nach Regime. Bei Lektionen,
  die an ein Regime gebunden sind, ist das Urteil deshalb nur ein grober Hinweis. Mögliche nächste Ausbaustufe:
  die Vergleichsgruppe zusätzlich nach Struktur-Regime filtern, sobald genug Shadow-Daten vorliegen.

## 2. KI-Trader-Verlauf (AI-Trading-Panel → Verlauf)

- **Ein Reiter für alles:** Echtgeld | Paper | Live-Logik gesamt (Echtgeld + Paper) | Sammlung steuert jetzt Chart,
  Kennzahlen **und** die Setup-Reife. Die doppelten großen Karten und die eigenen Setup-Reife-Reiter wurden entfernt.
- **Mindest-Trade-Übersicht** aus dem Verlauf entfernt. Backend und Einstellungen (`/api/min-trade/*`) bleiben unverändert.
- **Setup-Reife:** Die Spalte „… n / WR / PnL“ fällt weg. Trades / Winrate / PnL zeigen die gewählte Welt im
  gleichen Zeitfenster wie bisher (aktive Variante, sonst 30 Tage). Neuer Service `services/setup_world_stats.py`,
  Feld `worlds` je Zeile. Die Summe der Welten entspricht der Gesamtzahl. Urteil und Phase bleiben auf der
  Gesamtstatistik (Reife-Gate unverändert), die Gesamtwerte stehen im Tooltip.
- **Seitwärts ziehen am Desktop:** Setup-Reife- und Setup-Nutzungs-Tabelle lassen sich mit der Maus greifen und ziehen
  (`.drag-scroll-x`, gemeinsamer Listener `lib/tableDragScroll.js`). Touch funktioniert wie gewohnt.
- **Korrekturen:**
  - Die Equity-Kurve zählt nur noch Trades mit Status `closed`. Vorher lief der Filter über `≠ open`, dadurch
    konnten wartende oder abgebrochene Einträge mitzählen.
  - Die Kurve bricht nicht mehr bei 5.000 Trades ab. Kennzahlen gelten über alle Trades, die Chart-Punkte werden
    auf 2.000 ausgedünnt.
  - Antworten eines vorher gewählten Reiters werden verworfen, wenn man schnell umschaltet.

## 3. Regime-Copilot

- Neues Modul `services/regime_copilot_knowledge.py` erzeugt das Regime-Wissen **aus dem Code**:
  - Taxonomie 3/5/9 mit Keys und Labels, NNFX-Gruppen
  - Namens-Ebenen: Struktur vs. Kurzfrist vs. Legacy „bulle/bär“
  - Detektoren inkl. `jump`
  - Noten-Schwellen, Sweet Spot 5–15 T
  - Freigabe-Regeln (notenabhängige Shadow-Trades, Bänder, Autonomie)
  - KI-Trader-Anbindung (Prompt-Blöcke, Marktphasen-Filter mit Quelle `lab`)
  - Dynamische Strategien (Label-Basis, skip_regimes, on_switch) und Ausbau-Ideen

  Dazu kommt ein Live-Ist-Stand aus der DB: Freigaben je Klasse, aktuelles Struktur-Regime je Symbol,
  dynamische Strategien.
- Veraltete Aussagen im Prompt korrigiert:
  - „4–14 Tage“ → Sweet Spot aus `regime_quality`
  - „≥ 30 Shadow-Trades“ → notenabhängig 10/15/25/30
  - „Referenz ≥ 60 % gut“ → gestufte Schwellen
  - fehlender Detektor `jump` ergänzt
  - „Bulle/Bär/Seitwärts“ im Strategie-Copilot → aktuelle Regime-Namen
- Der Copilot darf jetzt zu Implementierung und Ausbau (KI-Trader, dynamische Strategien) beraten, bleibt aber
  nachweisgebunden (Shadow → Walk-Forward → Wirksam).
- Bugfix `regime_engine.regime_key`: Im 9er-Modus hießen alle drei Seitwärts-Regime „side“.
  Neu: `side_low` / `side_mid` / `side_high`. Die Labels bleiben gleich, die Keys waren nur informativ.

## 4. Umbenennung MarketMaker (MM)

Geändert:
- Header „MARKETMAKER“, auf sehr schmalen Displays „MM“
- Browser-Titel, Manifest, Favicon (MM)
- Telegram-Verbindungsnachricht
- FastAPI-Titel
- OpenRouter-Header-Default
- `package.json`-Name (`marketmaker-frontend`), README

Unverändert (bewusst): `DB_NAME`, alle Env-Keys, der Funktionsname „KI Trader“ (steht in gespeicherten Trades als
`strategy_name`). Auf Render optional `OPENROUTER_TITLE="MarketMaker"` setzen.

## 5. Tests

- Neu: `backend/tests/test_mm_verlauf_regime_copilot_0110.py` (16 Unit-Tests).
- Unit-Suite: 2.183 bestanden. Die übrigen Fehlschläge gab es schon vorher, sie sind im Original-Branch identisch
  (veraltete Quelltext-/Versions-Asserts und Live-Tests ohne Backend-URL).
- `CI=true yarn build` läuft ohne Fehler durch.
