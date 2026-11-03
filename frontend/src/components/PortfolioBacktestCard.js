import React, { useEffect, useRef, useState } from 'react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

const NumInput = ({ label, value, onChange, step = 1, title }) => (
  <label className="pbt-field" title={title}>
    <span>{label}</span>
    <input type="number" value={value} step={step}
      onChange={e => onChange(e.target.value)}
      data-testid={`pbt-input-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`} />
  </label>
);

const ScenarioRow = ({ name, s, base }) => (
  <tr data-testid={`pbt-scenario-${name}`}>
    <td className="bt-name">{name === 'base' ? 'Basis' : name === 'slippage_stress'
      ? `Slippage-Stress (+${s.slippage_pct}%/Fill)` : `Ausfall (${s.windows}× ${s.outage_hours}h offline)`}</td>
    <td className="mono">{s.trades}{s.candidates != null ? `/${s.candidates}` : ''}</td>
    <td className="mono">{fmt(s.win_rate, 1)}%</td>
    <td className={`mono ${s.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(s.pnl)} ({fmt(s.return_pct, 1)}%)</td>
    <td className="mono neg">-{fmt(s.max_drawdown)} ({fmt(s.max_drawdown_pct, 1)}%)</td>
    <td className="mono">{base && name !== 'base'
      ? <span className={s.pnl - base.pnl >= 0 ? 'pos' : 'neg'}>{fmt(s.pnl - base.pnl)}</span> : '–'}</td>
  </tr>
);

export default function PortfolioBacktestCard({ selStrats, selCoins, days, capital, fee }) {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState({ start_capital: 1000, max_open_trades: 5,
    max_portfolio_risk_pct: 6, max_cluster_risk_pct: 4, slippage_pct: 0.05,
    outage_count: 3, outage_hours: 12 });
  const [job, setJob] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => () => clearTimeout(pollRef.current), []);

  const poll = (id) => {
    clearTimeout(pollRef.current);
    fetch(`${API_URL}/api/portfolio-backtest/status?job_id=${id}`)
      .then(r => r.json())
      .then(j => {
        setJob(j);
        if (j.status === 'running') pollRef.current = setTimeout(() => poll(id), 2000);
        else if (j.status === 'error') toast.error(`Portfolio-Backtest: ${j.error || 'Fehler'}`);
      })
      .catch(() => { pollRef.current = setTimeout(() => poll(id), 4000); });
  };

  const start = async () => {
    if (!selStrats.length || !selCoins.length) {
      toast.error('Erst oben Strategien & Coins auswählen');
      return;
    }
    try {
      const res = await fetch(`${API_URL}/api/portfolio-backtest/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          strategy_ids: selStrats, symbols: selCoins, days,
          max_capital: capital, fee_percent: fee, portfolio: cfg,
        }),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
      setJob({ id: d.job_id, status: 'running', progress: 0, phase: 'Startet...' });
      poll(d.job_id);
    } catch { toast.error('Verbindungsfehler'); }
  };

  const running = job?.status === 'running';
  const result = job?.status === 'done' ? job.result : null;
  const p = result?.portfolio;
  const corr = result?.correlation;
  const set = (k) => (v) => setCfg(c => ({ ...c, [k]: v === '' ? '' : Number(v) }));

  return (
    <div className="pbt-card" data-testid="portfolio-backtest-card">
      <button className="pbt-head" onClick={() => setOpen(o => !o)} data-testid="pbt-toggle">
        <span className="bt-section-title">PORTFOLIO-BACKTEST · GEMEINSAMES KAPITAL</span>
        <span className="pbt-chevron">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="pbt-body">
          <div className="bt-hint">
            Spielt alle Trades der oben gewählten Strategien/Coins chronologisch gegen EINEN
            Kapitaltopf: max. gleichzeitige Positionen, Risikobudget wie im Live-Guard,
            Korrelation der Tages-PnL sowie Slippage-/Ausfall-Szenarien.
          </div>
          <div className="pbt-grid">
            <NumInput label="Startkapital" value={cfg.start_capital} onChange={set('start_capital')} step={100}
              title="Gemeinsamer Kapitaltopf (USDT)" />
            <NumInput label="Max Positionen" value={cfg.max_open_trades} onChange={set('max_open_trades')}
              title="Max. gleichzeitig offene Trades" />
            <NumInput label="Risiko %" value={cfg.max_portfolio_risk_pct} onChange={set('max_portfolio_risk_pct')} step={0.5}
              title="Offenes Gesamtrisiko ≤ x% der Equity (0 = aus, wie Live-Risikobudget)" />
            <NumInput label="Cluster %" value={cfg.max_cluster_risk_pct} onChange={set('max_cluster_risk_pct')} step={0.5}
              title="Risiko je Anlageklasse ≤ x% der Equity (0 = aus)" />
            <NumInput label="Slippage %" value={cfg.slippage_pct} onChange={set('slippage_pct')} step={0.01}
              title="Szenario: Aufschlag je Fill-Seite" />
            <NumInput label="Ausfälle" value={cfg.outage_count} onChange={set('outage_count')}
              title="Szenario: Anzahl Offline-Fenster im Zeitraum" />
            <NumInput label="Ausfall Std" value={cfg.outage_hours} onChange={set('outage_hours')} step={1}
              title="Dauer je Offline-Fenster (Stunden)" />
            {isAdmin() && (
              <button className="bt-run-btn pbt-run" onClick={start} disabled={running}
                data-testid="pbt-run">
                {running ? 'Läuft…' : 'Portfolio testen'}
              </button>
            )}
          </div>
          {running && (
            <div className="pbt-progress" data-testid="pbt-progress">
              <div className="bt-progress-bar"><div style={{ width: `${job.progress || 0}%` }} /></div>
              <div className="bt-progress-info">{job.phase} · {job.progress || 0}%</div>
            </div>
          )}
          {result && p && (
            <div className="pbt-result" data-testid="pbt-result">
              <div className="pbt-kpis">
                <div data-testid="pbt-kpi-equity"><span>Endkapital</span>
                  <b className={`mono ${p.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(p.final_equity)} USDT</b></div>
                <div><span>Trades genommen</span><b className="mono">{p.trades}/{p.candidates}</b></div>
                <div><span>Max gleichzeitig</span><b className="mono">{p.max_concurrent} (Ø {fmt(p.avg_concurrent, 1)})</b></div>
                <div><span>Max Drawdown</span><b className="mono neg">-{fmt(p.max_drawdown)} ({fmt(p.max_drawdown_pct, 1)}%)</b></div>
              </div>
              {Object.values(p.skipped || {}).some(v => v > 0) && (
                <div className="bt-hint" data-testid="pbt-skipped">
                  Übersprungen: {p.skipped.max_positions} Max-Positionen · {p.skipped.kapital} Kapital
                  · {p.skipped.risikobudget} Risikobudget · {p.skipped.cluster} Cluster
                </div>
              )}
              <div className="bt-table-wrap">
                <table className="bt-table" data-testid="pbt-scenario-table">
                  <thead><tr><th>Szenario</th><th>Trades</th><th>WR</th><th>PnL</th><th>Max DD</th><th>Δ Basis</th></tr></thead>
                  <tbody>
                    <ScenarioRow name="base" s={{ ...p }} base={null} />
                    {result.scenarios?.slippage_stress &&
                      <ScenarioRow name="slippage_stress" s={result.scenarios.slippage_stress} base={p} />}
                    {result.scenarios?.outage &&
                      <ScenarioRow name="outage" s={result.scenarios.outage} base={p} />}
                  </tbody>
                </table>
              </div>
              {corr?.pairs?.length > 0 && (
                <div className="pbt-corr" data-testid="pbt-correlation">
                  <div className="bt-section-title">KORRELATION TAGES-PNL (Ø {fmt(corr.avg_corr, 2)})</div>
                  <div className="pbt-corr-list">
                    {corr.pairs.slice(0, 6).map(pair => (
                      <span key={`${pair.a}-${pair.b}`} className={`pbt-corr-pill ${pair.corr > 0.5 ? 'warn' : ''}`}>
                        {pair.a.replace('USDT', '')}·{pair.b.replace('USDT', '')} {fmt(pair.corr, 2)}
                      </span>
                    ))}
                  </div>
                  <div className="bt-hint">&gt; 0.5 = die Paare gewinnen/verlieren oft am selben Tag –
                    hohes Klumpenrisiko bei gleichzeitigen Positionen.</div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
