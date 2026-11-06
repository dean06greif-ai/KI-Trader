import React from 'react';

// Edge-Filter (Backend: services/edge_filter.py) – blockt LIVE-Einstiege ohne
// Kosten-/Trend-Vorteil; Paper-/Sammel-Trades werden nur markiert.
const onOff = (v, def = true) => ((v ?? def) ? 'on' : 'off');

const Toggle = ({ k, label, title, cfg, updateConfig, def = true, onText = 'an', offText = 'aus' }) => (
  <label title={title}>
    <span>{label}</span>
    <select value={onOff(cfg[k], def)} onChange={e => updateConfig({ [k]: e.target.value === 'on' })} data-testid={`ai-${k.replace(/_/g, '-')}-select`}>
      <option value="on">{onText}</option>
      <option value="off">{offText}</option>
    </select>
  </label>
);

export const EdgeFilterSettings = ({ cfg, updateConfig }) => {
  const on = cfg.edge_filter_enabled !== false;
  return (
    <>
      <Toggle k="edge_filter_enabled" label="Edge-Filter (Live)" cfg={cfg} updateConfig={updateConfig}
        title="Auswertung 11/2026: Nur Trades mit Gebühren ≤ 10 % des Risikos, mit dem Tagestrend und auf Krypto lagen etwa bei 0 R bzw. im Plus – alle anderen klar im Minus. Der Filter blockt nur LIVE-Einstiege; Paper- und Sammel-Trades werden nur markiert (Vergleich im Edge-Bericht der Diagnose)." />
      {on && (
        <>
          <label title="Höchster Gebührenanteil am riskierten Betrag (Roundtrip-Fees ÷ SL-Abstand). 10 % bei 0,06 % Taker-Fee heißt: SL mindestens ~1,2 % vom Einstieg.">
            <span>Max. Gebühren je Risiko</span>
            <select value={cfg.edge_filter_max_fee_r ?? 0.1} onChange={e => updateConfig({ edge_filter_max_fee_r: Number(e.target.value) })} data-testid="ai-edge-filter-max-fee-select">
              {[...new Set([0, 0.05, 0.08, 0.1, 0.15, 0.2, 0.3, cfg.edge_filter_max_fee_r ?? 0.1])].sort((a, b) => a - b).map(v => <option key={v} value={v}>{v === 0 ? 'aus' : `${Math.round(v * 100)} %`}</option>)}
            </select>
          </label>
          <Toggle k="edge_filter_require_bias" label="Nur mit Tagestrend" cfg={cfg} updateConfig={updateConfig}
            title="LONG nur bei steigendem, SHORT nur bei fallendem Tages-Bias. Ohne Bias-Daten kein Live-Trade." />
          <Toggle k="edge_filter_crypto_only" label="Live nur Krypto" cfg={cfg} updateConfig={updateConfig}
            title="Rohstoffe, Forex und Indizes lagen deutlich im Minus (Slippage/Feed) – laufen weiter als Paper/Sammlung." />
          <Toggle k="edge_filter_apply_paper" label="Auch Paper blocken" cfg={cfg} updateConfig={updateConfig} def={false}
            onText="ja" offText="nein (nur markieren)"
            title="Standard: nein – Paper-Trades laufen weiter und zeigen im Edge-Bericht, ob der Filter wirklich hilft (Out-of-sample)." />
        </>
      )}
    </>
  );
};
