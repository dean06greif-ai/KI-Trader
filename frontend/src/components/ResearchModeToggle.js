import React from 'react';
import { toast } from '../lib/toast';
import { isAdmin } from '../auth';
import { postJson } from '../lib/postJson';

const EVIDENCE = [
  ['robust', 'Robust (empfohlen): Untergrenze, Glaubwürdigkeit je Regime, Bootstrap-Vergleich'],
  ['classic', 'Klassisch (bisher): 3 Gewinn-Trades = bestanden, 30 Trades je Regime'],
];
const VAL = [
  ['time', 'Zeitlich (empfohlen): spätere Phasen + Abstand'],
  ['phase', 'Phasen-Ende (bisher): letzte 25 % jeder Phase'],
];

/** Globaler Forschungs-Modus für NEUE Läufe (Dynamik, Werkbank, Regime-Strategie-Suche, Walk-Forward). */
export default function ResearchModeToggle({ mode, onSaved }) {
  const save = async (body) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try { await postJson('/api/research-mode', body); toast.success('Bewertung gespeichert – gilt für neue Läufe'); onSaved(); }
    catch (e) { toast.error(e.message); }
  };
  return (
    <div className="opt-small" data-testid="research-mode" style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
      <b>Bewertung neuer Strategie-Läufe:</b>
      <select value={mode?.evidence_mode || 'robust'} onChange={e => save({ evidence_mode: e.target.value })} data-testid="research-evidence-mode">
        {EVIDENCE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <select value={mode?.regime_val_mode || 'time'} onChange={e => save({ regime_val_mode: e.target.value })} data-testid="research-val-mode">
        {VAL.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}
