import React from 'react';
import { STRATEGY_PRESETS, presetById } from '../lib/searchPresets';

/** Preset-Leiste für Strategie-Suchen (Optimizer, Regime-Optimierung, Dynamik-Werkbank).
 *  onApply(preset) übernimmt die Werte ins jeweilige Formular; activeId markiert
 *  das Preset, dessen Indikator-Auswahl gerade exakt aktiv ist. */
export default function SearchPresetBar({ onApply, activeId, suggestedId, testPrefix = 'opt', hint }) {
  const suggested = presetById(suggestedId);
  return (
    <div className="opt-row" data-testid={`${testPrefix}-preset-bar`}>
      <div className="opt-label">
        PRESETS FÜR DIE SUCHE (setzt passende Indikatoren, Mitoptimierung &amp; Ziel – danach frei anpassbar)
      </div>
      <div className="opt-chips">
        {STRATEGY_PRESETS.map(p => (
          <button key={p.id} type="button" title={p.desc}
            className={`opt-chip ${activeId === p.id ? 'on' : ''}`}
            onClick={() => onApply(p)} data-testid={`${testPrefix}-preset-${p.id}`}>
            {p.label}{suggestedId === p.id ? ' ★' : ''}
          </button>
        ))}
      </div>
      {(suggested || hint) && (
        <div className="opt-small" data-testid={`${testPrefix}-preset-hint`} style={{ marginTop: 4 }}>
          {suggested && <>★ Empfohlen für diese Marktphase: <b>{suggested.label}</b>. </>}
          {hint}
        </div>
      )}
    </div>
  );
}
