import React, { useCallback, useEffect, useState } from 'react';
import { MagnifyingGlass, ArrowsClockwise, ShieldCheck, CaretDown, CaretUp } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders } from '../auth';
import './AIPostmortemPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const VERDICT = {
  early_exit: { label: 'zu früh raus', cls: 'warn' },
  stopped_before_move: { label: 'SL zu eng', cls: 'warn' },
  good_exit: { label: 'guter Exit', cls: 'pos' },
  right_stop: { label: 'Stop richtig', cls: 'pos' },
  neutral: { label: 'neutral', cls: 'mute' },
  missed_move: { label: 'Bewegung verpasst', cls: 'warn' },
  avoided_loss: { label: 'Verlust vermieden', cls: 'pos' },
};

const fmtR = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${(v ?? 0).toFixed(2)}R`;
const fmtTs = (ts) => {
  try { return new Date(ts).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' }); } catch (e) { return ''; }
};

const VariantChips = ({ variants }) => (
  <div className="pm-variants" data-testid="pm-variants">
    {Object.entries(variants || {}).map(([k, v]) => (
      <span key={k} className={`pm-chip ${v.robust ? 'robust' : ''} ${v.median_delta_r > 0 ? 'pos' : 'neg'}`}
        title={`${v.reason}\nbesser bei ${v.improved ?? 0}/${v.n} Trades`} data-testid={`pm-variant-${k}`}>
        {k.replace('tp_x', 'TP×').replace('sl_x', 'SL×')} {fmtR(v.median_delta_r)}
        {v.robust && <ShieldCheck size={11} weight="fill" />}
      </span>
    ))}
  </div>
);

const SetupRow = ({ s }) => {
  const [open, setOpen] = useState(false);
  const ve = s.verdicts || {};
  return (
    <div className={`pm-setup ${s.finding ? 'has-finding' : ''}`} data-testid={`pm-setup-${s.setup}`}>
      <div className="pm-setup-head" onClick={() => setOpen(!open)}>
        <b>{s.setup}</b>
        <span className="pm-meta">{s.trades} Trades · Exit-Median {fmtR(s.median_base_r)} · Nachlauf-MFE {fmtR(s.median_after_mfe_r)}</span>
        {!s.enough_data && <span className="pm-tag mute">sammelt ({s.trades}/8)</span>}
        {s.finding && <span className="pm-tag pos" data-testid={`pm-finding-tag-${s.setup}`}>robuster Befund</span>}
        {s.backtest_status && <span className={`pm-tag ${s.backtest_confirmed ? 'pos' : 'mute'}`}>Backtest: {s.backtest_status}</span>}
        <span className="pm-caret">{open ? <CaretUp size={12} /> : <CaretDown size={12} />}</span>
      </div>
      {s.finding && <div className="pm-finding" data-testid={`pm-finding-${s.setup}`}>{s.finding}</div>}
      {open && (
        <div className="pm-setup-body">
          <div className="pm-verdicts">
            {Object.entries(ve).map(([k, n]) => (
              <span key={k} className={`pm-tag ${VERDICT[k]?.cls || 'mute'}`}>{VERDICT[k]?.label || k}: {n}</span>
            ))}
          </div>
          <VariantChips variants={s.variants} />
        </div>
      )}
    </div>
  );
};

const RunnerStats = ({ days, mode }) => {
  const [rs, setRs] = useState(null);
  useEffect(() => {
    const q = `days=${days}${mode !== 'all' ? `&mode=${mode}` : ''}`;
    fetch(`${API_URL}/api/ai/runner-stats?${q}`).then(r => r.ok ? r.json() : null).then(setRs).catch(() => {});
  }, [days, mode]);
  if (!rs) return null;
  const Row = ({ label, a, testid }) => (
    <div className="pm-runner-row" data-testid={testid}>
      <b>{label}</b>
      {a.n === 0 ? <span className="pm-meta">noch keine Runner-Trades</span> : (
        <>
          <span>{a.n} Trades</span>
          <span className={a.sum_delta_r >= 0 ? 'pos' : 'neg'}>Δ gesamt {fmtR(a.sum_delta_r)}</span>
          <span className={a.avg_delta_r >= 0 ? 'pos' : 'neg'}>Ø {fmtR(a.avg_delta_r)} / Trade</span>
          <span className="pm-meta">Runner {fmtR(a.sum_real_r)} vs. voller TP {fmtR(a.sum_full_tp_r)} · besser {a.better} / schlechter {a.worse}</span>
        </>
      )}
    </div>
  );
  return (
    <div className="pm-runner" data-testid="pm-runner-stats">
      <div className="pm-runner-title">Runner vs. voller TP <span className="pm-meta">(realisiertes R gegenüber „alles bei TP1 geschlossen“)</span></div>
      <Row label="News-Runner" a={rs.news} testid="pm-runner-news" />
      <Row label="Scalp-Runner" a={rs.scalp} testid="pm-runner-scalp" />
      <Row label="Swing-Runner" a={rs.swing} testid="pm-runner-swing" />
    </div>
  );
};

const AIPostmortemPanel = () => {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState('all');
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [showRecent, setShowRecent] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const q = `days=${days}${mode !== 'all' ? `&mode=${mode}` : ''}`;
      const res = await fetch(`${API_URL}/api/ai/postmortem/summary?${q}`);
      if (res.ok) setData(await res.json());
    } catch (e) { /* silent */ }
    setLoading(false);
  }, [days, mode]);

  useEffect(() => { load(); }, [load]);

  const runNow = async () => {
    setRunning(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/postmortem/run`, { method: 'POST', headers: authHeaders() });
      if (res.status === 401) { toast.error('Admin-Login erforderlich'); return; }
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || 'Fehler');
      toast.success(`Nachanalyse: ${d.trades ?? 0} Trades, ${d.limits ?? 0} Limit-Orders ausgewertet`);
      load();
    } catch (e) { toast.error(e.message); }
    setRunning(false);
  };

  const setups = data?.setups || [];
  const limits = data?.limits || {};
  const recent = data?.recent || [];

  return (
    <div className="ai-postmortem-panel" data-testid="ai-postmortem-panel">
      <div className="pm-head">
        <span className="pm-title"><MagnifyingGlass size={14} weight="bold" /> Nachanalyse – Was wäre gewesen, wenn …?</span>
        <select value={mode} onChange={(e) => setMode(e.target.value)} data-testid="pm-mode-select">
          <option value="all">Live + Paper</option>
          <option value="live">nur Live</option>
          <option value="paper">nur Paper</option>
        </select>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} data-testid="pm-days-select">
          <option value={7}>7 Tage</option>
          <option value={30}>30 Tage</option>
          <option value={90}>90 Tage</option>
        </select>
        <button className="pm-btn" onClick={load} disabled={loading} title="Neu laden" data-testid="pm-reload-btn">
          <ArrowsClockwise size={13} className={loading ? 'spin' : ''} />
        </button>
        <button className="pm-btn pm-run" onClick={runNow} disabled={running} data-testid="pm-run-btn">
          {running ? 'läuft…' : 'Jetzt auswerten'}
        </button>
      </div>
      <div className="pm-intro">
        Nach jedem Trade-Close prüft die Nachanalyse mit echten 1m-Kerzen: Wie lief der Kurs danach? Wäre ein weiterer TP,
        ein weiterer/engerer SL oder ein Runner besser gewesen? Verfallene Limit-Orders: Bewegung verpasst oder Verlust vermieden?
        <b> Overfitting-Schutz:</b> Median statt Mittelwert, ≥60 % Konsistenz, Split-Half (alt/neu), ≥8 Trades, Kosten-Schwelle 0,15R,
        Schritt gedeckelt auf ±20 %, Backtest-Gegenprobe. Nur <ShieldCheck size={11} weight="fill" /> robuste Befunde erreichen den KI-Trader – als Hinweis, nicht als Regel.
      </div>
      <div className="pm-summary" data-testid="pm-summary">
        <span><b>{data?.reviews ?? 0}</b> Trades ausgewertet</span>
        <span><b>{setups.filter(s => s.finding).length}</b> robuste Befunde</span>
        <span><b>{limits.n ?? 0}</b> Limit-Orders</span>
        {data?.last_run && <span>letzter Lauf {fmtTs(data.last_run)}</span>}
        {data?.last_error && <span className="neg">Fehler: {data.last_error}</span>}
      </div>
      {setups.length === 0 && (
        <div className="pm-empty" data-testid="pm-empty">
          Noch keine Nachanalysen – sie entstehen automatisch, sobald geschlossene KI-Trades ihr Nachlauf-Fenster (2× Trade-Dauer, 30 min – 24 h) hinter sich haben.
        </div>
      )}
      {setups.map((s) => <SetupRow key={s.setup} s={s} />)}
      <RunnerStats days={days} mode={mode} />
      {limits.n > 0 && (
        <div className="pm-limits" data-testid="pm-limits">
          <b>Limit-Orders (nicht gefüllt):</b>{' '}
          {Object.entries(limits.verdicts || {}).map(([k, n]) => (
            <span key={k} className={`pm-tag ${VERDICT[k]?.cls || 'mute'}`}>{VERDICT[k]?.label || k}: {n}</span>
          ))}
          {limits.note && <div className="pm-finding">{limits.note}</div>}
        </div>
      )}
      {recent.length > 0 && (
        <div className="pm-recent">
          <button className="pm-link" onClick={() => setShowRecent(!showRecent)} data-testid="pm-recent-toggle">
            {showRecent ? 'Einzel-Reviews ausblenden' : `Letzte ${recent.length} Einzel-Reviews anzeigen`}
          </button>
          {showRecent && (
            <table className="pm-table" data-testid="pm-recent-table">
              <thead><tr><th>Zeit</th><th>Trade</th><th>Setup</th><th>Exit</th><th>Nachlauf</th><th>Urteil</th><th>beste Variante</th></tr></thead>
              <tbody>
                {recent.map((r) => {
                  const best = Object.entries(r.variants || {}).sort((a, b) => b[1].delta_r - a[1].delta_r)[0];
                  return (
                    <tr key={r.trade_id} data-testid={`pm-review-${r.trade_id}`}>
                      <td>{fmtTs(r.closed_at)}</td>
                      <td>{r.symbol} {r.side} <i>({r.mode}{r.data_collection ? ' · Daten' : ''})</i></td>
                      <td>{r.setup}</td>
                      <td className={r.base_r >= 0 ? 'pos' : 'neg'}>{fmtR(r.base_r)}</td>
                      <td>MFE {fmtR(r.after?.mfe_r)} / MAE {fmtR(-(r.after?.mae_r ?? 0))}</td>
                      <td><span className={`pm-tag ${VERDICT[r.verdict]?.cls || 'mute'}`} title={r.verdict_text}>{VERDICT[r.verdict]?.label || r.verdict}</span></td>
                      <td>{best ? `${best[0].replace('tp_x', 'TP×').replace('sl_x', 'SL×')} ${fmtR(best[1].delta_r)}` : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
};

export default AIPostmortemPanel;
