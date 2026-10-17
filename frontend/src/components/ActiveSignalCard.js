import React from 'react';

const MODE = {
  live: { label: 'LIVE', cls: 'live', title: 'Echtgeld-Live-Trade läuft' },
  paper: { label: 'PAPER', cls: 'paper', title: 'Paper-Trade läuft (kein Echtgeld)' },
  collection: { label: 'DATENSAMMLUNG', cls: 'collection', title: 'Datensammel-Trade (Paper, nur ML-Daten)' },
};

const fmtTime = (ts) => {
  const d = new Date(ts);
  const today = new Date().toDateString() === d.toDateString();
  return d.toLocaleString('de-DE', {
    timeZone: 'Europe/Berlin', hour: '2-digit', minute: '2-digit',
    ...(today ? {} : { day: '2-digit', month: '2-digit' }),
  });
};

// Ein aktives Signal (Setup + Modus + Level) im Signal-Panel
export const ActiveSignalCard = ({ s, onShowInChart }) => {
  const long = s.type === 'LONG';
  const mode = MODE[s.trade_mode];
  const key = s.id || `${s.type}-${s.timestamp}`;
  return (
    <div className={`current-signal active-signal clickable ${long ? 'signal-long' : 'signal-short'}`}
      data-testid={`active-signal-${key}`} onClick={() => onShowInChart && onShowInChart(s)}
      title="Anklicken: Signal mit Entry/SL/TP-Linien im Chart anzeigen">
      <div className="signal-header">
        <div className="active-signal-tags">
          <span className={`badge ${long ? 'badge-long' : 'badge-short'}`}>{s.type} SIGNAL</span>
          {mode && <span className={`as-mode ${mode.cls}`} title={mode.title} data-testid={`active-signal-mode-${key}`}>{mode.label}</span>}
          {s.active_state === 'no_trade' && (
            <span className="as-mode notrade" title={s.trade_reject_reason || 'Kein Trade eröffnet'}
              data-testid={`active-signal-notrade-${key}`}>KEIN TRADE</span>
          )}
          {s.confluence && (
            <span className="badge badge-confluence" data-testid="confluence-badge"
              title={`Confluence: ${s.confluence.count} Strategien zeigen dieselbe Richtung`}>⚡ CONF ×{s.confluence.count}</span>
          )}
        </div>
        <span className="mono text-muted as-time">{fmtTime(s.timestamp)}</span>
      </div>
      <div className="as-setup" data-testid={`active-signal-setup-${key}`}>
        <span className="as-setup-k">SETUP</span>
        <span className="as-setup-v mono">{s.setup || s.strategy_name || '–'}</span>
        {s.setup_label && <span className="as-setup-l">{s.setup_label}</span>}
      </div>
      {s.active_state === 'no_trade' && s.trade_reject_reason && (
        <div className="as-reason" data-testid={`active-signal-reason-${key}`}>🛡 {s.trade_reject_reason}</div>
      )}
      <div className="signal-prices">
        <div className="price-item"><span className="price-label">ENTRY</span><span className="price-value mono">${s.trade_entry || s.entry_price}</span></div>
        <div className="price-item"><span className="price-label">STOP LOSS</span><span className="price-value mono text-short">${s.stop_loss}</span></div>
        <div className="price-item"><span className="price-label">TP1</span><span className="price-value mono text-long">${s.take_profit_1}</span></div>
        <div className="price-item"><span className="price-label">TP FULL</span><span className="price-value mono text-long">${s.take_profit_full}</span></div>
      </div>
      <div className="signal-crv"><span className="crv-label">CRV</span><span className="crv-value mono">{s.crv}</span></div>
    </div>
  );
};

export default ActiveSignalCard;
