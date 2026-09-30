import React, { useEffect, useRef, useState } from 'react';
import { Play } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { INDICATOR_POOL } from '../lib/indicatorPool';
import IndicatorPicker from './IndicatorPicker';
import DynamicWorkbenchResult from './DynamicWorkbenchResult';
import { RegimePicker, StrategyMapping, CoreSettings, OptGroups, RobustnessRow, ExecutionRow, JobProgress } from './DynamicWorkbenchFields';

const API_URL = process.env.REACT_APP_BACKEND_URL;

export const TABS = [
  { id: 'refine', title: 'Bestehende optimieren', desc: 'Gesamt oder nur ausgewählte Regime einer dynamischen Strategie verbessern – optional endlos. Ergebnis ist eine neue Version, die live gehandelte bleibt unberührt.' },
  { id: 'create', title: 'Neu aus Strategien', desc: 'Regime-Erkennung wählen und jedem Regime eine bestehende Strategie zuordnen (leer = Regime nicht handeln).' },
  { id: 'discover', title: 'Neue Strategie je Regime', desc: 'Für eine Regime-Erkennung je Regime eine komplett neue Regel-Strategie suchen (wie Discovery) – optional endlos.' },
];

const STATE_KEY = 'dwb_ui_state_v2';
const loadState = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; } catch { return {}; } };

const DEFAULT_OPTS = {
  iterations: 40, min_trades: 10, max_rules: 4, rounds: 1, endless: false, walkforward: true,
  result_backtest: true, regime_walk_forward: true, regime_train_pct: 75, direction_bias: 'off',
  objective: 'combo', timeframe: '', days: '', sessions: '', max_capital: 100, leverage: 10,
  fee_percent: 0.06, execution: 'cloud', name: '',
};
// Auswahl je Reiter – bleibt beim Reiter-Wechsel erhalten (nur die Quelle setzt zurück)
const DEFAULT_SEL = {
  refine: { source: '', regimes: [], mode: 'params' },
  create: { source: '', mapping: {} },
  discover: { source: '', regimes: [], mode: 'discovery' },
};

async function post(path, body) {
  const r = await fetch(`${API_URL}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body || {}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || 'Fehler');
  return d;
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

export default function DynamicWorkbench({ lwOnline, onManageLocal }) {
  const saved = useRef(loadState()).current;
  const [tab, setTab] = useState(TABS.some(t => t.id === saved.tab) ? saved.tab : 'refine');
  const [sel, setSel] = useState({ ...DEFAULT_SEL, ...(saved.sel || {}) });
  const [opts, setOpts] = useState({ ...DEFAULT_OPTS, ...(saved.opts || {}) });
  const [optFlags, setOptFlags] = useState(saved.optFlags || { tpsl: true, leverage: true });
  const [indicators, setIndicators] = useState(saved.indicators || INDICATOR_POOL.map(i => i.id));
  const [dyns, setDyns] = useState([]);
  const [analyses, setAnalyses] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const { job, setJob, poll } = useWorkbenchJob();

  useEffect(() => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ tab, sel, opts, optFlags, indicators }));
  }, [tab, sel, opts, optFlags, indicators]);

  useEffect(() => {
    fetch(`${API_URL}/api/strategies`).then(r => r.json()).then(d => {
      const all = d.strategies || [];
      setDyns(all.filter(s => s.is_dynamic && s.dynamic?.analysis_id));
      setStrategies(all.filter(s => !s.is_dynamic && s.id !== 'ai_trader'));
    }).catch(() => {});
    fetch(`${API_URL}/api/regime-lab/list`).then(r => r.json())
      .then(d => setAnalyses((d.analyses || []).filter(a => (a.regimes || []).length))).catch(() => {});
  }, [job?.status]);

  const cur = sel[tab];
  const dyn = tab === 'refine' ? dyns.find(d => d.id === cur.source) : null;
  const analysis = analyses.find(a => a.id === (tab === 'refine' ? dyn?.dynamic?.analysis_id : cur.source));
  const regimes = tab === 'refine' ? (dyn?.dynamic?.regimes || []) : (analysis?.regimes || []);
  const mode = tab === 'create' ? 'params' : cur.mode;
  const running = job?.status === 'running';
  const jobOnThisTab = job && job.kind === tab;

  const patchSel = (patch) => setSel(s => ({ ...s, [tab]: { ...s[tab], ...patch } }));
  const setSource = (source) => {
    // Quelle gewechselt -> Auswahl neu (alle Regime vorbelegt), Mapping leer
    const src = tab === 'refine' ? dyns.find(d => d.id === source) : analyses.find(a => a.id === source);
    const ids = ((tab === 'refine' ? src?.dynamic?.regimes : src?.regimes) || []).map(r => r.id);
    patchSel({ source, regimes: ids, mapping: {} });
  };
  const toggleRegime = (id) => patchSel({ regimes: cur.regimes.includes(id) ? cur.regimes.filter(x => x !== id) : [...cur.regimes, id] });
  const set = (k, v) => (k === 'mode' ? patchSel({ mode: v }) : setOpts(o => ({ ...o, [k]: v })));

  const start = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (mode !== 'params' && indicators.length === 0) { toast.error('Mind. 1 Indikator anhaken'); return; }
    if (opts.execution === 'local' && !lwOnline) { toast.error('Kein lokaler Worker verbunden – Worker starten oder Cloud wählen'); return; }
    const body = {
      kind: tab, mode, iterations: Number(opts.iterations), min_trades: Number(opts.min_trades),
      max_rules: Number(opts.max_rules), rounds: Number(opts.rounds), endless: opts.endless,
      walkforward: opts.walkforward, result_backtest: opts.result_backtest, execution: opts.execution,
      name: opts.name || undefined, objective: opts.objective,
      timeframe: opts.timeframe || undefined, days: opts.days ? Number(opts.days) : undefined,
      sessions: opts.sessions || undefined, max_capital: Number(opts.max_capital),
      leverage: Number(opts.leverage), fee_percent: Number(opts.fee_percent),
      optimize: optFlags, indicators: mode !== 'params' ? indicators : undefined,
      regime_walk_forward: opts.regime_walk_forward, regime_train_pct: Number(opts.regime_train_pct),
      direction_bias: opts.direction_bias,
    };
    if (tab === 'refine') Object.assign(body, { dynamic_id: cur.source, regime_ids: cur.regimes });
    else if (tab === 'create') Object.assign(body, { analysis_id: cur.source, scope: 'combined', mapping: cur.mapping });
    else Object.assign(body, { analysis_id: cur.source, scope: 'combined', regime_ids: cur.regimes });
    try {
      const d = await post('/api/dynamic-workbench/start', body);
      setJob({ id: d.job_id, kind: tab, status: 'running', phase: 'Startet', round: 0, params: { execution: opts.execution } });
      poll(d.job_id);
      toast.success('Werkbank gestartet');
    } catch (e) { toast.error(e.message); }
  };
  const control = async (action) => {
    try { await post(`/api/dynamic-workbench/${action}/${job.id}`); toast.info(action === 'stop' ? 'Suche endet – Bestes wird übernommen' : 'Abbruch angefordert'); } catch (e) { toast.error(e.message); }
  };
  const canStart = !running && cur.source && (tab === 'create' ? Object.values(cur.mapping).some(Boolean) : cur.regimes.length > 0);
  const holdout = analysis ? (analysis.settings?.train_pct ?? 100) < 100 : undefined;

  return (
    <div data-testid="dynamic-workbench">
      <div className="opt-modes" style={{ marginTop: 6 }} data-testid="dwb-tabs">
        {TABS.map(t => (
          <button key={t.id} className={`opt-mode ${tab === t.id ? 'on' : ''}`} onClick={() => setTab(t.id)} data-testid={`dwb-tab-${t.id}`}>
            <div className="opt-mode-title">{t.title}{job && job.kind === t.id && job.status === 'running' ? ' · läuft' : ''}</div>
            <div className="opt-mode-desc">{t.desc}</div>
          </button>
        ))}
      </div>

      <div className="opt-setup">
        {tab === 'refine' ? (
          <label className="opt-field">Dynamische Strategie
            <select value={cur.source} onChange={e => setSource(e.target.value)} data-testid="dwb-dynamic-select">
              <option value="">– wählen –</option>
              {dyns.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
        ) : (
          <label className="opt-field">Regime-Erkennung (Analyse)
            <select value={cur.source} onChange={e => setSource(e.target.value)} data-testid="dwb-analysis-select">
              <option value="">– wählen –</option>
              {analyses.map(a => <option key={a.id} value={a.id}>{a.name} · {a.timeframe} · {a.days} Tage · {a.regimes.length} Regime{(a.settings?.train_pct ?? 100) < 100 ? '' : ' · ohne Holdout'}</option>)}
            </select>
          </label>
        )}
        {analysis && (
          <label className="opt-field">Assets (aus der Analyse)
            <div className="opt-params-list" style={{ margin: 0 }} data-testid="dwb-assets">
              {(analysis.symbols || []).map(s => <span key={s} className="opt-param-pill">{s.replace('USDT', '')}</span>)}
            </div>
          </label>
        )}
      </div>
      {tab === 'refine' && dyns.length === 0 && <div className="opt-small" style={{ marginBottom: 8 }}>Noch keine dynamische Strategie aus dem Regime-Lab vorhanden – zuerst „Neu aus Strategien“ oder „Neue Strategie je Regime“ nutzen.</div>}

      <CoreSettings opts={opts} set={set} tab={tab} mode={mode} source={analysis} />
      {tab !== 'create' && (
        <label className="opt-check" style={{ marginTop: -6 }} title="Runde für Runde weitersuchen, bis du stoppst – nur echte Verbesserungen werden übernommen">
          <input type="checkbox" checked={opts.endless} onChange={e => set('endless', e.target.checked)} data-testid="dwb-endless" /> Endlos-Suche (Runden ignorieren, bis „Suche beenden“)
        </label>
      )}

      {regimes.length > 0 && (
        <div className="opt-row" data-testid="dwb-regimes-row">
          <div className="opt-label">{tab === 'create' ? 'STRATEGIE JE REGIME (leer = nicht handeln)' : 'REGIME (Häkchen = wird optimiert · Auswahl bleibt beim Reiter-Wechsel erhalten)'}</div>
          {tab === 'create'
            ? <StrategyMapping regimes={regimes} strategies={strategies} mapping={cur.mapping} setMapping={(m) => patchSel({ mapping: m })} />
            : <RegimePicker regimes={regimes} selected={cur.regimes} onToggle={toggleRegime} onAll={() => patchSel({ regimes: regimes.map(r => r.id) })} testPrefix="dwb" />}
        </div>
      )}

      {tab !== 'create' && <OptGroups flags={optFlags} toggle={(k) => setOptFlags(f => ({ ...f, [k]: !f[k] }))} />}
      <RobustnessRow opts={opts} set={set} tab={tab} holdout={holdout} />
      {mode !== 'params' && <IndicatorPicker indicators={indicators} setIndicators={setIndicators} testPrefix="dwb"
        label="INDIKATOREN FÜR DIE REGEL-SUCHE (Häkchen = wird je Regime getestet)" />}

      <ExecutionRow execution={opts.execution} setExecution={(v) => set('execution', v)} lwOnline={lwOnline}
        onManage={onManageLocal} name={opts.name} setName={(v) => set('name', v)} />
      <button className="opt-run" onClick={start} disabled={!canStart} data-testid="dwb-start">
        <Play size={14} weight="fill" /> {running ? 'Werkbank läuft...' : tab === 'create' ? 'Erstellen & testen' : opts.endless ? 'Endlos-Suche starten' : 'Optimierung starten'}
      </button>
      {running && !jobOnThisTab && (
        <div className="opt-small" style={{ marginTop: 6 }} data-testid="dwb-job-elsewhere">
          Es läuft gerade ein Werkbank-Job im Reiter „{TABS.find(t => t.id === job.kind)?.title || job.kind}“ – Fortschritt und Ergebnis siehst du dort.
        </div>
      )}
      {jobOnThisTab && <JobProgress job={job} onStop={() => control('stop')} onCancel={() => control('cancel')} />}
      {jobOnThisTab && job.status === 'done' && (
        <DynamicWorkbenchResult result={job.result} kindLabel={TABS.find(t => t.id === job.kind)?.title} />
      )}
    </div>
  );
}
