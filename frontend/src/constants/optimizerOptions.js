// Gemeinsame Auswahl-Listen des Strategie-Optimizers (klassische Modi UND
// Dynamik-Werkbank nutzen dieselben Ziele, Einstellungs-Gruppen und Zeiträume).
export const OBJECTIVES = [
  { v: 'combo', l: 'Kombi (PnL × Winrate)' },
  { v: 'win_rate', l: 'Höchste Win-Rate' },
  { v: 'pnl', l: 'Höchster PnL' },
];

export const OPT_GROUPS = [
  { k: 'tpsl', l: 'TP/SL optimieren', d: 'TP1/Full-CRV, SL-Modus (Struktur/ATR/Fest), SL-Lookback, ATR-Puffer, TP1-%' },
  { k: 'breakeven', l: 'Break-Even optimieren', d: 'BE-Modus (TP1/CRV/Gewinn-%) + Trigger' },
  { k: 'trail', l: 'Trail-SL optimieren', d: 'ATR-Trailing nach TP1: An/Aus + Trail-Abstand (1.0–4.0 ATR) – findet den Sweetspot, wenn der Trail zu eng sitzt' },
  { k: 'profit_secure', l: 'Gewinnsicherung optimieren', d: 'An/Aus, Auslöser-%, gesicherter Anteil' },
  { k: 'leverage', l: 'Hebel optimieren', d: 'Fester Hebel 3x–50x' },
  { k: 'auto_leverage', l: 'Auto-Leverage optimieren', d: 'An/Aus, Modus (% oder Ticks hinter Stop), Abstand, Max-Hebel' },
  { k: 'sessions', l: 'Zeitfenster optimieren', d: '24/7 vs. typische Handelsfenster' },
  { k: 'time_exit', l: 'Zeit-Exit optimieren', d: 'Trade nach 30 min – 12 h automatisch schließen (aus/30/60/120/240/360/720 min)' },
  { k: 'entry_order', l: 'Entry-Ordertyp optimieren', d: 'Market (Taker-Fee) vs. Limit (Maker-Fee, verfällt nach n Kerzen)' },
  { k: 'direction', l: 'Richtung optimieren', d: 'Beide Seiten vs. nur Long vs. nur Short' },
];

export const DAY_OPTIONS = [1, 2, 3, 5, 7, 14, 30, 60, 90, 180, 360, 540, 720, 900, 1080, 1440,
  1800, 2160, 2520, 2880, 3240, 3600, 3960, 4320, 4680, 5040, 5400];
