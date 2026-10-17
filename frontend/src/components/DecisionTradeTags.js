import React from 'react';

const MODE = {
  live: { label: 'LIVE', cls: 'live', title: 'Echtgeld-Live-Trade eröffnet' },
  paper: { label: 'PAPER', cls: 'paper', title: 'Paper-Trade eröffnet (kein Echtgeld)' },
  collection: { label: 'DATENSAMMLUNG', cls: 'collection', title: 'Datensammel-Trade: reiner Paper-Trade fürs ML-Training, zählt nicht in PnL/Statistik' },
};

const SOURCE = {
  ki_geprueft: { label: 'KI-GEPRÜFT', color: '#00e5a0', title: 'Detektor hat gefeuert, die KI hat bestätigt (nur solche Trades dürfen live)' },
  ki_frei: { label: 'KI-FREI', color: '#ffb340', title: 'KI-Entscheidung ohne Detektor-Signal – nur Paper, getrennt gemessen' },
  regel: { label: 'REGEL', color: '#5ab0ff', title: 'Detektor-Treffer ohne KI (Vergleichsbasis)' },
  regel_live: { label: 'REGEL-LIVE', color: '#5ab0ff', title: 'Lab-validiertes Setup mit Trader-Freigabe' },
};

// Trade-Modus + Setup einer KI-Entscheidung (nur wenn wirklich ein Trade eröffnet wurde)
export const DecisionTradeTags = ({ d }) => {
  const mode = d?.trade_opened ? MODE[d?.trade_mode || (d?.data_collection ? 'collection' : '')] : null;
  const showSetup = d?.setup && String(d?.action || '').toUpperCase() !== 'HOLD';
  const src = showSetup || d?.signal_source ? SOURCE[d?.signal_source] : null;
  if (!mode && !showSetup && !src) return null;
  return (
    <>
      {mode && (
        <span className={`ai-dec-mode ${mode.cls}`} title={mode.title}
          data-testid={`ai-decision-mode-${d?.symbol || 'x'}`}>{mode.label}</span>
      )}
      {showSetup && (
        <span className="ai-dec-setup" title="Setup, nach dem die KI entschieden hat"
          data-testid={`ai-decision-setup-${d?.symbol || 'x'}`}>{d.setup}</span>
      )}
      {src && (
        <span className="ai-dec-setup" style={{ color: src.color, borderColor: src.color }}
          title={`${src.title}${d?.ki_timing ? ` · Timing: ${d.ki_timing} (${d.entry_delay_min} min nach Signal)` : ''}${d?.live_gate ? ` · ${d.live_gate}` : ''}`}
          data-testid={`ai-decision-source-${d?.symbol || 'x'}`}>{src.label}{d?.ki_timing ? ` · ${d.ki_timing}` : ''}</span>
      )}
    </>
  );
};

export default DecisionTradeTags;
