import React, { useEffect, useRef, useState } from 'react';
import { Play, StopCircle, X, ArrowsClockwise } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { DynBadge } from './DynamicTradeTag';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const TABS = [
  { id: 'refine', title: 'Bestehende optimieren', desc: 'Gesamt oder nur ausgewählte Regime einer dynamischen Strategie verbessern – optional endlos. Ergebnis ist eine neue Version, die live gehandelte bleibt unberührt.' },
  { id: 'create', title: 'Neu aus Strategien', desc: 'Regime-Erkennung wählen und jedem Regime eine bestehende Strategie zuordnen (leer = Regime nicht handeln).' },
  { id: 'discover', title: 'Neue Strategie je Regime', desc: 'Für eine Regime-Erkennung je Regime eine komplett neue Regel-Strategie suchen (wie Discovery) – optional endlos.' },
];
const MODE_LABEL = { params: 'Trade- & Strategie-Parameter', combo: 'Neue Regeln + Parameter', discovery: 'Nur neue Regeln' };

async function post(path, body) {
  const r = await fetch(`${API_URL}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body || {}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || 'Fehler');
  return d;
}

function RegimePicker({ regimes, selected, onToggle, onAll, testPrefix }) {
  return (
    <div className="opt-small" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', margin: '6px 0' }}>
      Regime:
      {regimes.map(r => (
        <label key={r.id} className={`opt-chip ${selected.includes(r.id) ? 'on' : ''}`} data-testid={`${testPrefix}-regime-${r.id}`}>
          <input type="checkbox" checked={selected.includes(r.id)} onChange={() => onToggle(r.id)} style={{ marginRight: 4 }} />
          {r.label || `#${r.id + 1}`}{r.strategy_name ? ` · ${r.strategy_name}` : ''}
        </label>
      ))}
      <button className="opt-chip" onClick={onAll} data-testid={`${testPrefix}-regime-all`}>alle</button>
    </div>
  );
}

function StrategyMapping({ regimes, strategies, mapping, setMapping }) {
  return (
    <div className="dwb-map" data-testid="dwb-mapping">
      {regimes.map(r => (
        <label key={r.id} className="opt-field">{r.label || `Regime #${r.id + 1}`}
          <select value={mapping[r.id] || ''} data-testid={`dwb-map-${r.id}`}
            onChange={e => setMapping(m => ({ ...m, [r.id]: e.target.value }))}>
            <option value="">– nicht handeln –</option>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
      ))}
    </div>
  );
}

function JobView({ job, onStop, onCancel }) {
  if (!job) return null;
  const running = job.status === 'running';
  const regimes = Object.entries(job.regimes || {});
  return (
    <div className="rl-wf-box" data-testid="dwb-job" style={{ marginTop: 10 }}>
      <div className="opt-small" data-testid="dwb-job-phase">
        <b>{running ? `Läuft · Runde ${job.round || 1}` : { done: 'Fertig', error: 'Fehler', cancelled: 'Abgebrochen' }[job.status]}</b> · {job.phase}
      </div>
      {running && (
        <div className="opt-progress">
          <div className="opt-progress-bar"><div style={{ width: `${job.sub_progress || 0}%`, height: '100%', background: '#00e5a0' }} /></div>
          <div className="opt-progress-text">{job.sub_phase || ''} · {Number(job.sub_progress || 0).toFixed(1)}%</div>
        </div>
      )}
      {regimes.length > 0 && (
        <div className="opt-small" style={{ margin: '6px 0' }} data-testid="dwb-job-regimes">
          Bestes je Regime: {regimes.map(([rid, r]) => (
            <span key={rid} className="opt-param-pill" style={{ marginRight: 4 }}>
              {r.label}: {r.note || <>Score <b>{r.score}</b> · PnL <b className={(r.pnl || 0) >= 0 ? 'pos' : 'neg'}>{r.pnl}</b> ({r.trades} T.){r.validation_passed ? ' ✓' : ''}</>}
            </span>
          ))}
        </div>
      )}
      {job.status === 'done' && job.result && (
        <div className="opt-small" data-testid="dwb-job-result">
          Neue dynamische Strategie <DynBadge /> <b>{job.result.dynamic_id}</b> angelegt.
          {job.result.walkforward?.verdict && <> Walk-Forward: <b>{job.result.walkforward.verdict.recommendation}</b></>}
          {' '}Sie ist jetzt unter „Strategien verwalten“ als Reiter wählbar (Blitz = Live/Paper je Coin).
        </div>
      )}
      {job.status === 'error' && <div className="rl-verdict bad" data-testid="dwb-job-error">{job.error}</div>}
      {running && (
        <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
          <button className="opt-btn-sm" onClick={onStop} data-testid="dwb-stop"><StopCircle size={12} /> Suche beenden & Bestes übernehmen</button>
          <button className="opt-btn-sm" onClick={onCancel} data-testid="dwb-cancel"><X size={12} /> Abbrechen</button>
        </div>
      )}
    </div>
  );
}

function useWorkbenchJob() {
  const [job, setJob] = useState(null);
  const timer = useRef(null);
  const poll = (id) => {
    clearInterval(timer.current);
    timer.current = setInterval(async () => {
      const d = await fetch(`${API_URL}/api/dynamic-workbench/status/${id}`).then(r => r.json()).catch(() => null);
      if (!d || d.detail) return;
      setJob(d);
      if (d.status !== 'running') clearInterval(timer.current);
    }, 2000);
  };
  useEffect(() => {
    fetch(`${API_URL}/api/dynamic-workbench/active`).then(r => r.json()).then(d => {
      if (d.job) { setJob(d.job); if (d.job.status === 'running') poll(d.job.id); }
    }).catch(() => {});
    return () => clearInterval(timer.current);
  }, []);
  return { job, setJob, poll };
}

export default function DynamicWorkbench({ symbols }) {
  const [tab, setTab] = useState('refine');
  const [dyns, setDyns] = useState([]);
  const [analyses, setAnalyses] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [dynId, setDynId] = useState('');
  const [aid, setAid] = useState('');
  const [selRegimes, setSelRegimes] = useState([]);
  const [mapping, setMapping] = useState({});
  const [mode, setMode] = useState('params');
  const [opts, setOpts] = useState({ iterations: 40, min_trades: 10, endless: false, rounds: 1, walkforward: true, execution: 'cloud', name: '' });
  const { job, setJob, poll } = useWorkbenchJob();

  useEffect(() => {
    fetch(`${API_URL}/api/strategies`).then(r => r.json()).then(d => {
      const all = d.strategies || [];
      setDyns(all.filter(s => s.is_dynamic && s.dynamic?.analysis_id));
      setStrategies(all.filter(s => !s.is_dynamic && s.id !== 'ai_trader'));
    });
    fetch(`${API_URL}/api/regime-lab/list`).then(r => r.json()).then(d => setAnalyses((d.analyses || []).filter(a => (a.regimes || []).length)));
  }, [job?.status]);

  const dyn = dyns.find(d => d.id === dynId);
  const analysis = analyses.find(a => a.id === aid);
  const regimes = tab === 'refine' ? (dyn?.dynamic?.regimes || []) : (analysis?.regimes || []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setSelRegimes(regimes.map(r => r.id)); setMapping({}); }, [dynId, aid, tab]);
  useEffect(() => { setMode(tab === 'discover' ? 'discovery' : 'params'); }, [tab]);

  const toggle = (id) => setSelRegimes(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  const set = (k, v) => setOpts(o => ({ ...o, [k]: v }));
  const running = job?.status === 'running';

  const start = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const body = { kind: tab, mode, ...opts, iterations: Number(opts.iterations), min_trades: Number(opts.min_trades), rounds: Number(opts.rounds), name: opts.name || undefined };
    if (tab === 'refine') Object.assign(body, { dynamic_id: dynId, regime_ids: selRegimes });
    else if (tab === 'create') Object.assign(body, { analysis_id: aid, scope: 'combined', mapping });
    else Object.assign(body, { analysis_id: aid, scope: 'combined', regime_ids: selRegimes });
    try {
      const d = await post('/api/dynamic-workbench/start', body);
      setJob({ id: d.job_id, status: 'running', phase: 'Startet', round: 0 });
      poll(d.job_id);
      toast.success('Werkbank gestartet');
    } catch (e) { toast.error(e.message); }
  };
  const control = async (action) => { try { await post(`/api/dynamic-workbench/${action}/${job.id}`); toast.info(action === 'stop' ? 'Suche endet – Bestes wird übernommen' : 'Abbruch angefordert'); } catch (e) { toast.error(e.message); } };
  const canStart = !running && (tab === 'refine' ? dynId && selRegimes.length : aid && (tab === 'create' ? Object.values(mapping).some(Boolean) : selRegimes.length));

  return (
    <div className="rl-wf-box" data-testid="dynamic-workbench" style={{ marginBottom: 14 }}>
      <div className="opt-section-title"><ArrowsClockwise size={13} /> DYNAMIK-WERKBANK (REGIME-LAB × OPTIMIZER)</div>
      <div className="opt-modes" style={{ marginTop: 6 }}>
        {TABS.map(t => (
          <button key={t.id} className={`opt-mode ${tab === t.id ? 'on' : ''}`} onClick={() => setTab(t.id)} data-testid={`dwb-tab-${t.id}`}>
            <div className="opt-mode-title">{t.title}</div>
            <div className="opt-mode-desc">{t.desc}</div>
          </button>
        ))}
      </div>
      <div className="opt-setup">
        {tab === 'refine' ? (
          <label className="opt-field">Dynamische Strategie
            <select value={dynId} onChange={e => setDynId(e.target.value)} data-testid="dwb-dynamic-select">
              <option value="">– wählen –</option>
              {dyns.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
        ) : (
          <label className="opt-field">Regime-Erkennung (Analyse)
            <select value={aid} onChange={e => setAid(e.target.value)} data-testid="dwb-analysis-select">
              <option value="">– wählen –</option>
              {analyses.map(a => <option key={a.id} value={a.id}>{a.name} · {a.timeframe} · {a.regimes.length} Regime{(a.settings?.train_pct ?? 100) < 100 ? '' : ' · ohne Holdout'}</option>)}
            </select>
          </label>
        )}
        {tab !== 'create' && (
          <label className="opt-field">Was optimieren
            <select value={mode} onChange={e => setMode(e.target.value)} data-testid="dwb-mode">
              {(tab === 'discover' ? ['discovery', 'combo'] : ['params', 'combo', 'discovery']).map(m => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
            </select>
          </label>
        )}
        <label className="opt-field">Ausführung
          <select value={opts.execution} onChange={e => set('execution', e.target.value)} data-testid="dwb-execution">
            <option value="cloud">Cloud</option><option value="local">Lokaler Worker</option>
          </select>
        </label>
        {tab !== 'create' && <>
          <label className="opt-field">Iterationen/Regime<input type="number" min={5} max={500} value={opts.iterations} onChange={e => set('iterations', e.target.value)} data-testid="dwb-iterations" style={{ width: 70 }} /></label>
          <label className="opt-field">Min. Trades<input type="number" min={1} value={opts.min_trades} onChange={e => set('min_trades', e.target.value)} data-testid="dwb-min-trades" style={{ width: 60 }} /></label>
          <label className="opt-field">Runden<input type="number" min={1} max={50} disabled={opts.endless} value={opts.rounds} onChange={e => set('rounds', e.target.value)} data-testid="dwb-rounds" style={{ width: 55 }} /></label>
          <label className="opt-check"><input type="checkbox" checked={opts.endless} onChange={e => set('endless', e.target.checked)} data-testid="dwb-endless" /> Endlos-Suche</label>
        </>}
        <label className="opt-check" title="Am Ende auf dem unangetasteten Holdout prüfen – nur dann gilt die neue Strategie als validiert">
          <input type="checkbox" checked={opts.walkforward} onChange={e => set('walkforward', e.target.checked)} data-testid="dwb-walkforward" /> Finaler Walk-Forward
        </label>
        <label className="opt-field">Name (optional)<input value={opts.name} onChange={e => set('name', e.target.value)} data-testid="dwb-name" style={{ width: 180 }} /></label>
      </div>
      {tab === 'create'
        ? (analysis && <StrategyMapping regimes={regimes} strategies={strategies} mapping={mapping} setMapping={setMapping} />)
        : (regimes.length > 0 && <RegimePicker regimes={regimes} selected={selRegimes} onToggle={toggle} onAll={() => setSelRegimes(regimes.map(r => r.id))} testPrefix="dwb" />)}
      {tab === 'refine' && dyns.length === 0 && <div className="opt-small">Noch keine dynamische Strategie aus dem Regime-Lab vorhanden.</div>}
      <button className="opt-run" onClick={start} disabled={!canStart} data-testid="dwb-start">
        <Play size={14} weight="fill" /> {tab === 'create' ? 'Erstellen & testen' : opts.endless ? 'Endlos-Suche starten' : 'Optimierung starten'}
      </button>
      <JobView job={job} onStop={() => control('stop')} onCancel={() => control('cancel')} />
    </div>
  );
}
