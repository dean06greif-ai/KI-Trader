import React, { useState } from 'react';
import { fmtDateTime } from '../lib/time';

const factorTxt = (f) => (f <= 0 ? '⏸ ausgesetzt' : f >= 1 ? '×1 (voll)' : `×${f}`);

/** Verlauf der Kapital-Stufen je Setup × Asset (schrittweise Drosselung statt Rückstufung). */
export const AssetCapitalLog = ({ events }) => {
  const [open, setOpen] = useState(false);
  if (!events?.length) {
    return (
      <div style={{ fontSize: 11, opacity: 0.5, marginTop: 6 }} data-testid="ai-asset-capital-log-empty">
        Kapital-Verlauf je Asset: noch keine Stufenwechsel.
      </div>
    );
  }
  const shown = open ? events : events.slice(0, 5);
  return (
    <div style={{ marginTop: 8 }} data-testid="ai-asset-capital-log">
      <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 3 }}>
        <b>Kapital-Verlauf je Asset</b> – statt das ganze Setup zurückzustufen, wird das Kapital für ein schwaches
        Asset schrittweise gedrosselt (×0.75 → ×0.5 → ×0.25 → ⏸ nur Paper) und bei Erholung wieder erhöht.
      </div>
      {shown.map((e, i) => (
        <div key={`${e.at}-${e.setup}-${e.symbol}-${i}`} data-testid={`ai-asset-capital-event-${i}`}
          style={{ fontSize: 11, display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid #1E2230' }}>
          <span style={{ opacity: 0.55, minWidth: 92 }}>{fmtDateTime(e.at)}</span>
          <span style={{ minWidth: 150 }}><b>{e.setup}</b> × {String(e.symbol).replace('USDT', '')}</span>
          <span style={{ color: e.direction === 'down' ? '#FFB020' : '#00D68F', minWidth: 150 }}>
            {factorTxt(e.from)} → {factorTxt(e.to)}
          </span>
          <span style={{ opacity: 0.6 }}>{e.note}</span>
        </div>
      ))}
      {events.length > 5 && (
        <button className="ai-action-btn" style={{ marginTop: 4 }} onClick={() => setOpen(v => !v)}
          data-testid="ai-asset-capital-log-toggle">
          {open ? 'Weniger anzeigen' : `Alle ${events.length} anzeigen`}
        </button>
      )}
    </div>
  );
};
