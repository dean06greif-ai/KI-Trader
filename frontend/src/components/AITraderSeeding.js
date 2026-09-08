import React, { useEffect, useRef, useState } from 'react';
import { Play, X, Repeat, ArrowRight, ArrowCounterClockwise, Clock } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import useInstruments from '../hooks/useInstruments';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const CLASS_LABELS = { crypto: 'Krypto', indices: 'Indizes', resources: 'Rohstoffe', forex: 'Forex' };
// Sidebar-Gruppen (core/instruments.py) -> Anlageklassen (services/setup_asset_class.py)
const GROUP_CLASS = { 'TOP 10 COINS': 'crypto', RESOURCES: 'resources', INDICES: 'indices', FOREX: 'forex' };
const STATUS = {
  passed: { l: 'Edge bestätigt', c: '#00FF66' },
  tuned: { l: 'Edge nach Feintuning', c: '#00E5A0' },
  failed: { l: 'kein Edge → nächste Variante', c: '#FFB020' },
  exhausted: { l: 'alle Varianten + Feintuning ohne Edge', c: '#FF3366' },
  live: { l: 'schon live (übersprungen)', c: '#8FB3FF' },
  no_data: { l: 'keine Historie', c: '#FF3366' },
};
const fmtTs = (ts) => (ts ? String(ts).slice(0, 16).replace('T', ' ') : '—');

const money = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${Number(v ?? 0).toFixed(2)}`;
const cell = (st) => (st && st.trades ? `${st.trades}T · ${st.winrate}% · ${money(st.pnl)}` : '—');

export default function AITraderSeeding({ selCoins, days, dateMode }) {
  const [info, setInfo] = useState(null);
  const [mode, setMode] = useState('single');
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const pollRef = useRef(null);
  const admin = isAdmin();
  const { bySymbol } = useInstruments();

  const classes = [...new Set((selCoins || [])
    .map(s => GROUP_CLASS[bySymbol[s]?.group] || 'crypto'))];

  const load = () => fetch(`${API_URL}/api/ai/playbook/backtest`).then(r => r.json()).then(d => {
    setInfo(d);
    if (d.last_result && !result) setResult(d.last_result);
    if (d.active) { setJob(d.active); poll(d.active.id); }
  }).catch(() => {});

  const poll = (jobId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API_URL}/api/ai/playbook/backtest/status/${jobId}`);
        if (!r.ok) return;
        const j = await r.json();
        setJob(j);
        if (j.status !== 'running') {
          clearInterval(pollRef.current);
          if (j.status === 'done') { setResult(j.result); toast.success('KI-Trader Backtest-Seeding abgeschlossen'); }
          else if (j.status === 'error') toast.error(`Seeding fehlgeschlagen: ${j.error}`);
          else toast.info('Seeding abgebrochen');
          fetch(`${API_URL}/api/ai/playbook/backtest`).then(r2 => r2.json()).then(setInfo).catch(() => {});
        }
      } catch { /* transient */ }
    }, 1500);
  };

  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []);

  const run = async () => {
    if (!classes.length) { toast.error('Mindestens 1 Asset wählen – getestet wird immer die ganze Anlageklasse'); return; }
    const body = { asset_classes: classes, days: dateMode === 'custom' ? 90 : Math.max(14, Math.min(365, days || 90)), mode };
    const r = await fetch(`${API_URL}/api/ai/playbook/backtest/run`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
    setJob({ id: d.job_id, status: 'running', progress: 0, phase: 'Startet' });
    setResult(null);
    poll(d.job_id);
  };

  const cancel = () => job && fetch(`${API_URL}/api/ai/playbook/backtest/cancel/${job.id}`,
    { method: 'POST', headers: authHeaders() });

  const resetCls = async (cls) => {
    if (!window.confirm(`Backtest-Seeding für ${CLASS_LABELS[cls]} zurücksetzen (Trades + Varianten-Stand)?`)) return;
    await fetch(`${API_URL}/api/ai/playbook/backtest/reset`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ asset_class: cls }),
    });
    toast.success('Zurückgesetzt');
    load();
  };

  const running = job?.status === 'running';
  const rules = info?.rules || {};
  const rows = (result?.rows || []).filter(r => r.setup);
  const auto = info?.auto;

  const saveAuto = async (patch) => {
    const r = await fetch(`${API_URL}/api/ai/playbook/backtest/auto`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ ...(auto || {}), ...patch }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast.error(d.detail || 'Speichern fehlgeschlagen'); return; }
    setInfo(prev => ({ ...(prev || {}), auto: d }));
    toast.success(d.enabled ? `Automatik aktiv – nächster Lauf ${fmtTs(d.next_run_at)}` : 'Automatik aus');
  };

  const toggleAutoClass = (c) => {
    const cur = auto?.asset_classes || [];
    const next = cur.includes(c) ? cur.filter(x => x !== c) : [...cur, c];
    if (!next.length) { toast.error('Mindestens eine Anlageklasse'); return; }
    saveAuto({ asset_classes: next });
  };

  return (
    <div className="btc-panel" data-testid="ai-seed-panel" style={{ marginTop: 10 }}>
      <div className="btc-head">
        <span className="btc-title">KI TRADER · Setup-Backtest (Seeding fürs Reife-Gate)</span>
        <span className="btc-hint-inline" data-testid="ai-seed-classes">
          Klassen: {classes.length ? classes.map(c => CLASS_LABELS[c]).join(', ') : '—'} · immer die ganze Anlageklasse
        </span>
      </div>
      <div className="bt-hint" style={{ marginBottom: 8 }}>
        Regelbasierte Detektoren testen die formalisierbaren Setups auf Vergangenheitsdaten (5m, In-Sample {Math.round((rules.is_share || 0.7) * 100)}% wählt die Variante,
        Out-of-Sample bestätigt). Bestandene Setups zählen ×{rules.weight ?? 0.5} und gedeckelt ({rules.max_backtest_weighted ?? 3} gewichtete Trades) fürs Reife-Gate –
        <b> Live erst nach {rules.min_real_trades ?? 2}+ profitablen echten Paper-Trades</b>. Nicht testbar: {Object.keys(info?.not_backtestable || {}).join(', ')}.
      </div>
      <div className="bt-exec" data-testid="ai-seed-mode">
        <span className="bt-exec-label">Modus</span>
        <button className={`bt-exec-btn ${mode === 'single' ? 'on' : ''}`} onClick={() => setMode('single')} data-testid="ai-seed-mode-single"
          title="Ein Durchlauf der aktuellen Variante je Setup; bei Misserfolg wird die nächste Variante vorgemerkt (Anpassung) – dann erneut starten">
          <ArrowRight size={13} weight="bold" /> Einmal-Durchlauf
        </button>
        <button className={`bt-exec-btn ${mode === 'loop' ? 'on' : ''}`} onClick={() => setMode('loop')} data-testid="ai-seed-mode-loop"
          title="Schleife: Varianten automatisch nacheinander testen, bis ein Setup Out-of-Sample besteht oder alle Varianten durch sind">
          <Repeat size={13} weight="bold" /> Auto-Schleife
        </button>
        {admin && (
          <button className="bt-run" onClick={run} disabled={running} data-testid="ai-seed-run">
            <Play size={15} weight="fill" /> {running ? 'Läuft...' : 'KI-Trader Backtest starten'}
          </button>
        )}
      </div>
      {running && (
        <div className="bt-progress" data-testid="ai-seed-progress">
          <div className="bt-progress-bar"><div style={{ width: `${job.progress || 0}%` }} /></div>
          <div className="bt-progress-row">
            <div className="bt-progress-text" data-testid="ai-seed-progress-text">{job.phase} · {job.progress || 0}%</div>
            <button className="bt-cancel" onClick={cancel} data-testid="ai-seed-cancel"><X size={13} weight="bold" /> Abbrechen</button>
          </div>
        </div>
      )}
      {auto && (
        <div className="bt-tools" style={{ marginTop: 8, flexWrap: 'wrap', gap: 8 }} data-testid="ai-seed-auto">
          <label className="bt-ram" style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: admin ? 'pointer' : 'default' }}>
            <input type="checkbox" checked={!!auto.enabled} disabled={!admin} onChange={e => saveAuto({ enabled: e.target.checked })}
              data-testid="ai-seed-auto-toggle" />
            <Clock size={13} weight="bold" /> Automatik: Auto-Schleife (Varianten + Feintuning) regelmäßig ohne Klick
          </label>
          <span className="bt-ram">alle</span>
          <select className="bt-select" value={auto.interval_hours} disabled={!admin} onChange={e => saveAuto({ interval_hours: Number(e.target.value) })}
            data-testid="ai-seed-auto-interval">
            {[6, 12, 24, 48, 72, 168].map(h => <option key={h} value={h}>{h < 48 ? `${h} h` : `${h / 24} Tage`}</option>)}
          </select>
          <span className="bt-ram">Zeitraum</span>
          <select className="bt-select" value={auto.days} disabled={!admin} onChange={e => saveAuto({ days: Number(e.target.value) })}
            data-testid="ai-seed-auto-days">
            {[30, 60, 90, 180, 365].map(d => <option key={d} value={d}>{d} Tage</option>)}
          </select>
          <span className="bt-ram">Klassen:</span>
          {Object.keys(CLASS_LABELS).map(c => (
            <button key={c} className={`bt-tool-btn ${(auto.asset_classes || []).includes(c) ? 'on' : ''}`}
              style={{ opacity: (auto.asset_classes || []).includes(c) ? 1 : 0.45 }}
              disabled={!admin} onClick={() => toggleAutoClass(c)} data-testid={`ai-seed-auto-class-${c}`}>
              {CLASS_LABELS[c]}
            </button>
          ))}
          <span className="bt-ram" data-testid="ai-seed-auto-next">
            {auto.enabled ? `nächster Lauf: ${fmtTs(auto.next_run_at)}` : 'Automatik aus'}
            {auto.last_run_at ? ` · letzter: ${fmtTs(auto.last_run_at)}${auto.last_summary ? ` (${auto.last_summary.passed ?? 0}/${auto.last_summary.tested ?? 0} mit Edge)` : ''}` : ''}
            {auto.last_error ? ` · Fehler: ${auto.last_error}` : ''}
          </span>
          {(auto.history || []).length > 0 && (
            <div style={{ width: '100%' }} data-testid="ai-seed-auto-history">
              <div className="btc-sub" style={{ marginTop: 6 }}>AUTOMATIK-VERLAUF (letzte {Math.min(10, auto.history.length)} Läufe)</div>
              <table className="bt-table" data-testid="ai-seed-auto-history-table">
                <thead><tr><th>Zeit</th><th>Modus</th><th>Zeitraum</th><th>Klassen</th><th title="Setups mit Edge / getestete Setups">Edge-Treffer</th><th>Status</th></tr></thead>
                <tbody>
                  {[...auto.history].reverse().map((h, i) => (
                    <tr key={h.at || i} data-testid={`ai-seed-auto-history-row-${i}`}>
                      <td className="mono">{fmtTs(h.at)}</td>
                      <td>{h.mode === 'loop' ? 'Auto-Schleife' : 'Einmal'}</td>
                      <td className="mono">{h.days} Tage</td>
                      <td>{(h.asset_classes || []).map(c => CLASS_LABELS[c] || c).join(', ')}</td>
                      <td className={`mono ${(h.passed || 0) > 0 ? 'pos' : ''}`}>{h.passed ?? 0}/{h.tested ?? 0}</td>
                      <td style={{ color: h.error ? '#FF3366' : '#00FF66' }}>{h.error ? `Fehler: ${h.error}` : 'ok'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      {result && (
        <div style={{ marginTop: 10 }} data-testid="ai-seed-result">
          <div className="btc-sub">
            ERGEBNIS · {result.mode === 'loop' ? 'Auto-Schleife' : 'Einmal-Durchlauf'}{result.trigger === 'auto' ? ' (Automatik)' : ''} · {result.days} Tage · {result.passed ?? 0}/{result.tested ?? 0} Setups mit Edge
          </div>
          <table className="bt-table" data-testid="ai-seed-table">
            <thead>
              <tr>
                <th>Klasse</th><th>Setup</th><th>Variante</th>
                <th title="In-Sample: Trades · Winrate · PnL (USDT je 100 Notional)">In-Sample</th>
                <th title="Out-of-Sample: Trades · Winrate · PnL">Out-of-Sample</th>
                <th title="Gespeicherte OOS-Trades fürs Reife-Gate">Gespeichert</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={`${r.asset_class}-${r.setup}`} data-testid={`ai-seed-row-${r.asset_class}-${r.setup}`}>
                  <td>{CLASS_LABELS[r.asset_class] || r.asset_class}</td>
                  <td className="bt-name">{r.setup}</td>
                  <td className="mono">{r.variant ? `${r.variant} (${(r.variant_idx ?? 0) + 1}/${r.variants_total})${r.tried > 1 ? ` · ${r.tried} getestet` : ''}` : '—'}</td>
                  <td className="mono">{cell(r.is)}</td>
                  <td className={`mono ${(r.oos?.pnl || 0) > 0 ? 'pos' : (r.oos?.pnl || 0) < 0 ? 'neg' : ''}`}>{cell(r.oos)}</td>
                  <td className="mono">{r.stored ?? 0}</td>
                  <td style={{ color: STATUS[r.status]?.c }} title={r.note || ''} data-testid={`ai-seed-status-${r.asset_class}-${r.setup}`}>
                    {STATUS[r.status]?.l || r.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {info?.classes && Object.keys(info.classes).length > 0 && admin && (
        <div className="bt-tools" style={{ marginTop: 8 }} data-testid="ai-seed-reset-row">
          <span className="bt-ram">Seeding-Stand zurücksetzen:</span>
          {Object.keys(info.classes).map(c => (
            <button key={c} className="bt-tool-btn" onClick={() => resetCls(c)} data-testid={`ai-seed-reset-${c}`}>
              <ArrowCounterClockwise size={12} weight="bold" /> {CLASS_LABELS[c] || c}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
