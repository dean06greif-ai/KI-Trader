import React, { useState, useEffect, useCallback } from 'react';
import { Heartbeat, ArrowsClockwise } from '@phosphor-icons/react';
import './AIDiagnosisPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const SEV = {
  kritisch: { label: 'KRITISCH', cls: 'diag-crit' },
  warnung: { label: 'WARNUNG', cls: 'diag-warn' },
  info: { label: 'INFO', cls: 'diag-info' },
};
const FRAGE = {
  setups: 'Fehlen Setups?',
  daten: 'Daten/Ausführung ok?',
  blockaden: 'Hindert ihn etwas?',
  allgemein: 'Gesamtlage',
};

const Stat = ({ label, a }) => (
  <div className="diag-stat">
    <div className="diag-stat-label">{label}</div>
    <div className="diag-stat-val">
      {a?.trades ?? 0} Trades · WR {a?.win_rate ?? '–'}% ·{' '}
      <b className={(a?.pnl || 0) >= 0 ? 'pos' : 'neg'}>{(a?.pnl ?? 0).toFixed(2)}$</b>
    </div>
  </div>
);

const AIDiagnosisPanel = () => {
  const [days, setDays] = useState(14);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  const load = useCallback((d) => {
    setLoading(true);
    setErr(null);
    fetch(`${API_URL}/api/ai/diagnosis?days=${d}`)
      .then(r => r.json())
      .then(setData)
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(days); }, [days, load]);

  const grouped = {};
  (data?.findings || []).forEach(f => { (grouped[f.frage] = grouped[f.frage] || []).push(f); });

  return (
    <div className="diag-panel" data-testid="ai-diagnosis-panel">
      <div className="diag-head">
        <span className="diag-title"><Heartbeat size={14} weight="fill" /> KI-TRADER DIAGNOSE</span>
        <div className="diag-range">
          {[7, 14, 30].map(d => (
            <button key={d} className={days === d ? 'active' : ''}
              onClick={() => setDays(d)} data-testid={`diag-days-${d}`}>{d} Tage</button>
          ))}
          <button onClick={() => load(days)} title="Neu laden" data-testid="diag-reload">
            <ArrowsClockwise size={12} className={loading ? 'spin' : ''} />
          </button>
        </div>
      </div>
      <div className="diag-sub">
        Deterministische Auswertung echter Messdaten: Fehlen Setups? Sind die Daten
        korrekt? Blockiert etwas die KI? (kein LLM – nur Zahlen)
      </div>
      {err && <div className="diag-err">Diagnose nicht ladbar: {err}</div>}
      {loading && !data && <div className="diag-empty">Analysiere…</div>}
      {data && (
        <>
          <div className="diag-stats" data-testid="diag-stats">
            <Stat label="LIVE (echtes Risiko)" a={data.live} />
            <Stat label="PAPER" a={data.paper} />
            <Stat label="DATENSAMMLUNG" a={data.collection} />
            <div className="diag-stat">
              <div className="diag-stat-label">SLIPPAGE (LIVE)</div>
              <div className="diag-stat-val">
                {data.slippage?.measured_trades
                  ? <>Ø {data.slippage.avg_slippage_pct}% · Σ <b className={(data.slippage.total_slippage_usdt || 0) >= 0 ? 'pos' : 'neg'}>{data.slippage.total_slippage_usdt}$</b> ({data.slippage.measured_trades} gemessen)</>
                  : 'noch keine Messungen'}
              </div>
            </div>
          </div>

          {Object.entries(FRAGE).map(([k, label]) => (grouped[k] || []).length > 0 && (
            <div className="diag-block" key={k} data-testid={`diag-block-${k}`}>
              <div className="diag-block-title">{label}</div>
              {grouped[k].map((f, i) => (
                <div className={`diag-finding ${SEV[f.severity]?.cls || ''}`} key={i}>
                  <span className="diag-sev">{SEV[f.severity]?.label || f.severity}</span>
                  <span>{f.text}</span>
                </div>
              ))}
            </div>
          ))}

          {(data.setups || []).length > 0 && (
            <div className="diag-block">
              <div className="diag-block-title">SETUP-KATALOG · Live-Reife &amp; echte Ergebnisse</div>
              <table className="diag-table" data-testid="diag-setup-table">
                <thead><tr><th>Setup</th><th>Trades</th><th>PnL</th><th>Urteil</th><th>Status</th></tr></thead>
                <tbody>
                  {data.setups.map(s => (
                    <tr key={s.setup}>
                      <td title={s.beschreibung}>{s.setup}</td>
                      <td className="mono">{s.trades}</td>
                      <td className={`mono ${(s.pnl || 0) >= 0 ? 'pos' : 'neg'}`}>{(s.pnl || 0).toFixed(2)}$</td>
                      <td>{s.verdict || '–'}</td>
                      <td>{s.disabled ? `⛔ gesperrt` : s.live_ready ? '✓ live-reif' : `⏳ ${s.live_reason}`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(data.guards || []).length > 0 && (
            <div className="diag-block">
              <div className="diag-block-title">BLOCKADEN (WÄCHTER) · {data.guards.reduce((a, g) => a + g.count, 0)} verhinderte Einstiege</div>
              <div className="diag-guards" data-testid="diag-guards">
                {data.guards.map(g => <span key={g.key} className="diag-guard">{g.label} <b>{g.count}×</b></span>)}
                {data.live_gate_redirects > 0 && <span className="diag-guard">Live-Gate → Datensammlung <b>{data.live_gate_redirects}×</b></span>}
              </div>
            </div>
          )}

          {(data.data_quality || []).length > 0 && (
            <div className="diag-block">
              <div className="diag-block-title">DATENQUALITÄT · Kerzen-Frische &amp; Orderflow-Quelle</div>
              <table className="diag-table" data-testid="diag-data-table">
                <thead><tr><th>Symbol</th><th>Kerzen</th><th>Letzte Kerze</th><th>Orderflow</th></tr></thead>
                <tbody>
                  {data.data_quality.slice(0, 12).map(d => (
                    <tr key={d.symbol} className={d.last_candle_age_min > 5 ? 'stale' : ''}>
                      <td>{d.symbol}</td>
                      <td className="mono">{d.candles}</td>
                      <td className="mono">{d.last_candle_age_min > 5 ? `⚠ ${d.last_candle_age_min} min alt` : `${d.last_candle_age_min} min`}</td>
                      <td>{d.orderflow_real === true ? '✓ echte Ticks' : d.orderflow_real === false ? 'Proxy (Kerzen)' : '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default AIDiagnosisPanel;
