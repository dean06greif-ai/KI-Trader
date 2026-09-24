import React from 'react';

// Mindest-SL je Anlageklasse (Backend: services/min_sl_rule.py) – feste Regel,
// unabhängig vom (autonom justierbaren) Fee-Wächter.
const CLASSES = [
  { key: 'crypto', label: 'Krypto', def: 0.4 },
  { key: 'resources', label: 'Rohstoffe', def: 0.5 },
  { key: 'indices', label: 'Indizes', def: 0.35 },
  { key: 'forex', label: 'Forex', def: 0.25 },
];
const OPTIONS = [0, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0];

export const MinSlRuleSettings = ({ cfg, updateConfig }) => {
  const vals = cfg.min_sl_pct_by_class || {};
  const on = cfg.min_sl_rule_enabled !== false;
  return (
    <>
      <label title="Feste Regel: KI-Trades mit SL-Abstand unter dem Minimum der Anlageklasse werden gar nicht erst gesendet (Daten: Krypto < 0,4 % = Trefferquote < 35 %). Unabhängig vom Fee-Wächter.">
        <span>Mindest-SL-Regel</span>
        <select value={on ? 'on' : 'off'} onChange={e => updateConfig({ min_sl_rule_enabled: e.target.value === 'on' })} data-testid="ai-min-sl-enabled-select">
          <option value="on">an</option>
          <option value="off">aus</option>
        </select>
      </label>
      {on && CLASSES.map(c => {
        const v = vals[c.key] ?? c.def;
        const opts = OPTIONS.includes(v) ? OPTIONS : [...OPTIONS, v].sort((a, b) => a - b);
        return (
          <label key={c.key} title={`Mindest-SL-Abstand für ${c.label} (Standard ${c.def}%). 0 = keine Regel für diese Klasse.`}>
            <span>{`Mindest-SL ${c.label}`}</span>
            <select value={v} onChange={e => updateConfig({ min_sl_pct_by_class: { [c.key]: Number(e.target.value) } })}
              data-testid={`ai-min-sl-${c.key}-select`}>
              {opts.map(o => <option key={o} value={o}>{o === 0 ? 'aus' : `${o}%`}</option>)}
            </select>
          </label>
        );
      })}
      {on && (
        <label title="Regel auch für Datensammel-Trades anwenden (empfohlen: Sammel-Trades mit engem SL verzerren die Lern-Daten mit Rausch-Stops).">
          <span>Mindest-SL für Sammlung</span>
          <select value={cfg.min_sl_apply_collection === false ? 'off' : 'on'} onChange={e => updateConfig({ min_sl_apply_collection: e.target.value === 'on' })} data-testid="ai-min-sl-collection-select">
            <option value="on">ja</option>
            <option value="off">nein</option>
          </select>
        </label>
      )}
    </>
  );
};

export default MinSlRuleSettings;
