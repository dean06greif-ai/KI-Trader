// Such-Profile für den Regime-Autopiloten (Erkennungs-Suche). Setzen nur den
// Sweet Spot der Ø Richtungs-Phase und die Lauf-Steuerung – Detektor-Parameter
// sucht der Autopilot selbst. Die Untergrenze der vollen Regime-Phase skaliert
// das Backend je Regime-Anzahl (3: 100 %, 5: 60 %, 9: 50 % – services/regime_phase.py).
export const AUTOPILOT_PRESETS = [
  { id: 'daytrading', label: 'Daytrading (Standard)', minPhase: 5, maxPhase: 15, plateau: 300, searchDet: true,
    desc: 'Ø Richtungs-Phase 5–15 Tage: genug Zeit für mehrere Intraday-Trades je Phase, trotzdem reaktiv. Benchmark „sehr gut“ nutzt dasselbe Band.' },
  { id: 'reaktiv', label: 'Reaktiv (kurze Phasen)', minPhase: 3, maxPhase: 10, plateau: 300, searchDet: true,
    desc: 'Ø Richtungs-Phase 3–10 Tage: schnellere Umschaltung für Scalping/Momentum. Achtung: unter 5 Tagen gibt es kein „sehr gut“ im Benchmark.' },
  { id: 'swing', label: 'Ruhig / Swing', minPhase: 8, maxPhase: 25, plateau: 300, searchDet: true,
    desc: 'Ø Richtungs-Phase 8–25 Tage: wenige, lange Phasen für Trendfolge mit weiten Zielen – weniger Strategie-Wechsel, mehr Lag.' },
  { id: 'lang', label: 'Lange Endlos-Suche', minPhase: 5, maxPhase: 15, plateau: 1500, searchDet: true,
    maxMin: 0, maxRounds: 0, warmStart: true, gradeLock: true,
    desc: 'Daytrading-Band, aber Plateau-Stopp erst nach 1500 Runden ohne Gewinn, mit Warmstart und Note-Schutz – für Nacht-/Wochenend-Läufe.' },
];
