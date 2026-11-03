import React from 'react';

/** Kandidaten für „Beste Strategie je Phase“ – leer = alle Strategien. */
export default function DynamicStrategyCandidates({ strategies, selected, onChange, disabled }) {
  const sel = selected || [];
  const toggle = (id) => onChange(sel.includes(id) ? sel.filter(x => x !== id) : [...sel, id]);
  return (
    <div className="opt-row" data-testid="dwb-candidates">
      <div className="opt-label">
        STRATEGIEN ZUM VERGLEICHEN ({sel.length ? `${sel.length} gewählt` : `alle ${Math.min(strategies.length, 20)}`} · max. 20)
      </div>
      <div className="opt-chips">
        {strategies.map(s => (
          <button key={s.id} className={`opt-chip ${sel.includes(s.id) ? 'on' : ''}`} disabled={disabled}
            onClick={() => toggle(s.id)} data-testid={`dwb-candidate-${s.id}`}>
            {sel.includes(s.id) ? '☑' : '☐'} {s.name}
          </button>
        ))}
        <button className="opt-chip" disabled={disabled} onClick={() => onChange([])} data-testid="dwb-candidates-all">alle</button>
      </div>
      <div className="opt-small" style={{ opacity: 0.75 }}>
        Stufe 1: jede Strategie wird mit ihren Einstellungen je Phase getestet (Training + Walk-Forward). Stufe 2: die besten 2 je Phase
        bekommen eine Parameter-Suche (Iterationen/Runden wie unten) – übernommen wird nur, was robust besser ist.
      </div>
    </div>
  );
}
