import React from 'react';
import { INDICATOR_GROUPS, INDICATOR_POOL } from '../lib/indicatorPool';

/** Indikator-Auswahl für Discovery-Suchen (Optimizer + Dynamik-Werkbank). */
export default function IndicatorPicker({ indicators, setIndicators, label, testPrefix = 'opt' }) {
  const toggle = (id) => setIndicators(indicators.includes(id)
    ? indicators.filter(x => x !== id) : [...indicators, id]);
  return (
    <div className="opt-row" data-testid={`${testPrefix}-indicator-picker`}>
      <div className="opt-label">{label || 'INDIKATOREN FÜR DIE SUCHE (Häkchen = wird getestet · über die Erklärung hovern)'}</div>
      <div className="opt-ind-master">
        <button className="opt-chip" data-testid={`${testPrefix}-ind-all-on`}
          onClick={() => setIndicators(INDICATOR_POOL.map(i => i.id))}>
          Alle anhaken
        </button>
        <button className="opt-chip" data-testid={`${testPrefix}-ind-all-off`}
          onClick={() => setIndicators([])}>
          Alle abwählen
        </button>
        <span className="opt-small" style={{ alignSelf: 'center' }}>
          Gruppen zeigen den typischen Einsatzzweck – beim Deep-Test / der Endlos-Suche dürfen alle Gruppen gleichzeitig aktiv sein.
        </span>
      </div>
      {INDICATOR_GROUPS.map(g => {
        const ids = g.items.map(i => i.id);
        const allOn = ids.every(id => indicators.includes(id));
        return (
          <div key={g.key} className="opt-ind-group" data-testid={`${testPrefix}-ind-group-${g.key}`}>
            <div className="opt-ind-group-head">
              <span className="opt-ind-group-title" title={g.hint}>{g.label}</span>
              <button className="opt-ind-group-toggle" data-testid={`${testPrefix}-ind-group-toggle-${g.key}`}
                onClick={() => setIndicators(allOn
                  ? indicators.filter(x => !ids.includes(x))
                  : [...new Set([...indicators, ...ids])])}>
                {allOn ? 'abwählen' : 'alle'}
              </button>
            </div>
            <div className="opt-chips">
              {g.items.map(i => (
                <button key={i.id} className={`opt-chip ${indicators.includes(i.id) ? 'on' : ''}`}
                  onClick={() => toggle(i.id)} title={i.desc} data-testid={`${testPrefix}-ind-${i.id}`}>
                  {i.label}
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
