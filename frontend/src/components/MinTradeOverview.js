import React, { useEffect, useState } from 'react';
import { Coins } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const money = (v) => `${(v || 0) >= 0 ? '+' : ''}${Number(v || 0).toFixed(2)} $`;
const pnlColor = (v) => ((v || 0) >= 0 ? '#00FF66' : '#FF3366');
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '–');

const StatCard = ({ title, s, accent, testid }) => (
  <div className="mt-stat" style={{ '--mt': accent }} data-testid={testid}>
    <span className="mt-stat-title">{title}</span>
    <span className="mt-stat-pnl" style={{ color: pnlColor(s?.pnl) }}>{money(s?.pnl)}</span>
    <span className="mt-stat-meta">
      {s?.trades || 0} Trades{s?.open ? ` (+${s.open} offen)` : ''} · WR {s?.winrate || 0}% · Ø {money(s?.avg_pnl)}
      {s?.r_multiple != null ? ` · ${s.r_multiple >= 0 ? '+' : ''}${s.r_multiple}R` : ''} · Fees {Number(s?.fees || 0).toFixed(2)} $
    </span>
  </div>
);

// Mindest-Trades (nur Echtgeld-Live) mit eigenem Ergebnis – lohnen sich die
// Minimal-Positionen? Vergleich mit normalen Live-Trades im selben Zeitraum.
export const MinTradeOverview = () => {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(90);

  useEffect(() => {
    fetch(`${API_URL}/api/min-trade/stats?days=${days}`).then(r => r.json()).then(setData).catch(() => setData(null));
  }, [days]);

  const mt = data?.min_trades;
  const verdict = !mt || !mt.trades ? null
    : mt.trades < 10 ? { cls: 'wait', txt: `Noch zu wenige abgeschlossene Mindest-Trades (${mt.trades}/10) für ein Urteil` }
      : (mt.r_multiple ?? mt.pnl) > 0 ? { cls: 'good', txt: 'Mindest-Trades lohnen sich bisher (positives Ergebnis)' }
        : { cls: 'bad', txt: 'Mindest-Trades verlieren bisher – ggf. im Kapital-Fenster (Live) deaktivieren' };

  return (
    <div className="mt-overview" data-testid="min-trade-overview">
      <div className="mt-head">
        <span className="ai-learn-title"><Coins size={14} weight="fill" /> Mindest-Trades (Echtgeld-Live) – lohnen sie sich?</span>
        <select value={days} onChange={e => setDays(Number(e.target.value))} data-testid="min-trade-overview-days">
          {[30, 90, 180, 365].map(d => <option key={d} value={d}>{`${d} Tage`}</option>)}
        </select>
      </div>
      {!data && <div className="mt-empty">Lade…</div>}
      {data && (
        <>
          <div className="mt-stats">
            <StatCard title="Mindest-Trades" s={data.min_trades} accent="#FACC15" testid="min-trade-overview-min" />
            <StatCard title="Normale Live-Trades" s={data.normal} accent="#00E08A" testid="min-trade-overview-normal" />
          </div>
          {verdict && <div className={`mt-verdict ${verdict.cls}`} data-testid="min-trade-overview-verdict">{verdict.txt}</div>}
          {!(data.rows || []).length ? (
            <div className="mt-empty" data-testid="min-trade-overview-empty">
              Noch keine Mindest-Trades im Zeitraum. Sie entstehen nur live, wenn Kapital-Grenze oder Risikobudget einen normalen Trade verhindern würden.
            </div>
          ) : (
            <table className="mt-table" data-testid="min-trade-overview-table">
              <thead><tr><th>Eröffnet</th><th>Asset</th><th>Seite</th><th>Strategie / Setup</th><th>Status</th><th>PnL</th><th>Grund</th></tr></thead>
              <tbody>
                {data.rows.map(r => (
                  <tr key={r.id} data-testid={`min-trade-row-${r.id}`}>
                    <td>{fmtTime(r.opened_at)}</td>
                    <td>{String(r.symbol || '').replace('USDT', '')}</td>
                    <td className={r.side === 'LONG' ? 'pos' : 'neg'}>{r.side}</td>
                    <td>{r.strategy_name || r.strategy_id}{r.setup ? ` · ${r.setup}` : ''}</td>
                    <td>{r.status === 'open' ? 'offen' : (r.result || 'geschlossen')}</td>
                    <td style={{ color: r.status === 'open' ? undefined : pnlColor(r.realized_pnl) }}>
                      {r.status === 'open' ? '–' : money(r.realized_pnl)}
                    </td>
                    <td className="mt-note">{r.note || '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
};

export default MinTradeOverview;
