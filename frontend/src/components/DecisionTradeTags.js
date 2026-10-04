import React from 'react';

const MODE = {
  live: { label: 'LIVE', cls: 'live', title: 'Echtgeld-Live-Trade eröffnet' },
  paper: { label: 'PAPER', cls: 'paper', title: 'Paper-Trade eröffnet (kein Echtgeld)' },
  collection: { label: 'DATENSAMMLUNG', cls: 'collection', title: 'Datensammel-Trade: reiner Paper-Trade fürs ML-Training, zählt nicht in PnL/Statistik' },
};

// Trade-Modus + Setup einer KI-Entscheidung (nur wenn wirklich ein Trade eröffnet wurde)
export const DecisionTradeTags = ({ d }) => {
  const mode = d?.trade_opened ? MODE[d?.trade_mode || (d?.data_collection ? 'collection' : '')] : null;
  const showSetup = d?.setup && String(d?.action || '').toUpperCase() !== 'HOLD';
  if (!mode && !showSetup) return null;
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
    </>
  );
};

export default DecisionTradeTags;
