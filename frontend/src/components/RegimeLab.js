import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Play, Trash, ChartScatter, ArrowClockwise, Cloud, Desktop, Gear, MoonStars } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { addToSeries } from '../lib/series';
import SafeOverlay from './SafeOverlay';
import LocalWorkerPanel from './LocalWorkerPanel';
import TIMEFRAMES from '../constants/timeframes';
import EquityChart from './EquityChart';
import RegimeChart from './RegimeChart';
import RegimeOptimizePanel from './RegimeOptimizePanel';
import RegimeEngineSettings from './RegimeEngineSettings';
import RegimeValidation from './RegimeValidation';
import RegimeQualityCard from './RegimeQualityCard';
import DynamicPanel from './DynamicPanel';
import StrategyCopilot from './StrategyCopilot';
import { regimeColor } from '../lib/regimeColors';
import { fmtDate, fmtDateTime } from '../lib/time';
import './RegimeLab.css';
import NumInput from './NumInput';
import { RegimeReleaseBadge, RegimeReleaseControls } from './RegimeRelease';
import { AblationCompare, EmaPeriodCompare, KombiAutoCalibrate } from './RegimeDetectorTools';
import RegimeLabSummary, { DETECTOR_LABELS } from './RegimeLabSummary';
import RegimeLabHelp from './RegimeLabHelp';
import RegimeInsights from './RegimeInsights';
import { JobProgress, SOFT_STOP_KINDS, useLabJobControls } from './RegimeJobProgress';
import RegimeAutopilot from './RegimeAutopilot';
import RegimeQueueStrip from './RegimeQueueStrip';

const JOB_KIND_LABEL = {
  analysis: 'Analyse', regime_opt: 'Regime-Optimierung', walkforward: 'Walk-Forward',
  calibration: 'Kalibrierung', ema_compare: 'EMA-Vergleich', kombi_calibrate: 'Kombi-Kalibrierung',
  ablation: 'Ablation', autopilot: 'Regime-Autopilot',
};

const NNFX_LABELS = { trend: 'NNFX: Trend', range: 'NNFX: Seitwärts', breakout: 'NNFX: Breakout' };

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

const DAY_OPTIONS = [30, 60, 90, 180, 360, 540, 720, 1080, 1440, 1800, 2160, 2880, 3600];
const STATE_KEY = 'regime_lab_ui_v1';
const loadState = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; } catch { return {}; } };

const scopeKey = (scope, symbol) => (scope === 'per_coin' ? `per_coin:${symbol}` : 'combined');

// ---------------- Regime-Übergangs-Matrix (Etappe 2) ----------------
function TransitionMatrix({ analysisId, scope, symbol, regimes, model }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState('final');
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!open) return;
    setData(null); setErr(null);
    const q = new URLSearchParams({ scope, view });
    if (symbol) q.set('symbol', symbol);
    fetch(`${API_URL}/api/regime-lab/${analysisId}/transitions?${q}`)
      .then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail || 'Fehler'); return d; }))
      .then(setData)
      .catch(e => setErr(String(e.message || e)));
  }, [open, view, scope, symbol, analysisId]);

  const DIR_COLORS = { 0: '#ff4757', 1: '#ffa502', 2: '#00e5a0' };
  const cellOf = (rows, f, t) => rows.find(m => m.from === f && m.to === t);

  const matrixTable = (rows, ids, labelOf, colorOf, testid) => (
    <table className="rl-compare-table" data-testid={testid}
      style={{ borderCollapse: 'collapse', fontSize: 12, marginTop: 4 }}>
      <thead>
        <tr style={{ textAlign: 'left', opacity: 0.7 }}>
          <th style={{ padding: '3px 10px 3px 0' }}>Von ↓ / Nach →</th>
          {ids.map(t => (
            <th key={t} style={{ padding: '3px 10px' }}>
              <span className="rl-dot" style={{ background: colorOf(t), marginRight: 4 }} />
              {labelOf(t)}
            </th>
          ))}
          <th style={{ padding: '3px 10px' }} title="Anzahl Übergänge aus diesem Regime · Ø Dauer der Phase vor dem Wechsel">Kontext</th>
        </tr>
      </thead>
      <tbody>
        {ids.map(f => {
          const fromRows = rows.filter(m => m.from === f);
          if (!fromRows.length) return null;
          const tot = fromRows.reduce((a, m) => a + m.count, 0);
          return (
            <tr key={f} style={data.last
              && ((rows === data.direction_matrix && data.last.direction === f)
                || (rows === data.matrix && data.last.regime === f))
              ? { background: 'rgba(179,136,255,0.10)' } : undefined}>
              <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>
                <span className="rl-dot" style={{ background: colorOf(f), marginRight: 4 }} />
                <b>{labelOf(f)}</b>
              </td>
              {ids.map(t => {
                const c = cellOf(fromRows, f, t);
                return (
                  <td key={t} style={{ padding: '3px 10px', whiteSpace: 'nowrap' }}
                    title={c ? `${c.count}× · Ø Dauer davor ${fmt(c.avg_from_days, 1)}d · Ø Dauer danach ${fmt(c.avg_to_days, 1)}d` : 'kein Übergang beobachtet'}>
                    {c ? <><b>{fmt(c.prob_pct, 0)}%</b> <span style={{ opacity: 0.6 }}>({c.count})</span></> : '–'}
                  </td>
                );
              })}
              <td style={{ padding: '3px 10px', whiteSpace: 'nowrap', opacity: 0.75 }}>
                {tot}× · Ø {(() => {
                  const pf = (rows === data.matrix ? data.per_from : data.direction_per_from) || [];
                  const e = pf.find(p => p.from === f);
                  return e ? `${fmt(e.avg_days, 1)}d` : '–';
                })()}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  return (
    <div data-testid="regime-transition-matrix">
      <div className="opt-setup" style={{ alignItems: 'center', marginTop: 6 }}>
        <button className="opt-chip" onClick={() => setOpen(!open)}
          data-testid="transition-matrix-toggle"
          title="Historische Übergangs-Wahrscheinlichkeiten zwischen den Regimen dieser Analyse: was folgt z.B. auf Seitwärts – und wie lange dauerten die Phasen davor/danach?">
          {open ? '▾' : '▸'} Übergangs-Matrix
        </button>
        {open && (
          <>
            <button className={`opt-chip ${view === 'final' ? 'on' : ''}`}
              onClick={() => setView('final')} data-testid="transition-matrix-view-final"
              title="Rückwirkend korrigierte Final-Phasen (die 'wahren' Abschnitte)">Final</button>
            <button className={`opt-chip ${view === 'live' ? 'on' : ''}`}
              onClick={() => setView('live')} data-testid="transition-matrix-view-live"
              title="Kausale Live-Abschnitte (ohne Lookahead) – so hätte man es live erlebt">Live</button>
          </>
        )}
      </div>
      {open && err && <span className="opt-small" style={{ color: '#e66' }}>{err}</span>}
      {open && !data && !err && <span className="opt-small">Lade Übergänge…</span>}
      {open && data && (
        <div style={{ overflowX: 'auto' }}>
          {data.last && (
            <div className="opt-small" style={{ marginTop: 4 }} data-testid="transition-matrix-last">
              Aktuelle Phase: <b>{data.last.label}</b> seit <b>{fmt(data.last.days, 1)}d</b>
              {' '}– die markierte Zeile zeigt, was darauf historisch folgte.
            </div>
          )}
          <div className="opt-small" style={{ marginTop: 6, fontWeight: 700, letterSpacing: 0.4 }}>
            RICHTUNGS-EBENE (AUF / SEITWÄRTS / AB)
          </div>
          {matrixTable(data.direction_matrix || [],
            [...new Set((data.direction_matrix || []).flatMap(m => [m.from, m.to]))].sort(),
            (d) => (data.direction_labels || {})[d] || d,
            (d) => DIR_COLORS[d] || '#888',
            'transition-matrix-direction')}
          <div className="opt-small" style={{ marginTop: 8, fontWeight: 700, letterSpacing: 0.4 }}>
            JE REGIME ({data.total_transitions} ÜBERGÄNGE)
          </div>
          {matrixTable(data.matrix || [],
            (data.regimes || []).map(r => r.id),
            (id) => (data.regimes || []).find(r => r.id === id)?.label || `#${id}`,
            (id) => regimeColor(id, regimes, model),
            'transition-matrix-regimes')}
          <div className="opt-small" style={{ marginTop: 4, opacity: 0.7 }}>
            Lesart: Zeile = aktuelles Regime, Spalte = nächstes Regime, Zelle = Anteil
            der historischen Übergänge (Anzahl). Kontext = Übergänge gesamt · Ø Phasendauer
            vor dem Wechsel. {view === 'live' ? 'Live-Abschnitte (kausal, ohne Lookahead).' : 'Final-Phasen (rückwirkend korrigiert).'}
          </div>
        </div>
      )}
    </div>
  );
}

const M = ({ m }) => m ? (
  <span className="opt-small">
    PnL <b className={m.pnl >= 0 ? 'pos' : 'neg'}>{fmt(m.pnl)}</b> · WR <b>{fmt(m.win_rate, 1)}%</b> ·
    Trades <b>{m.trades}</b> · DD <b>{fmt(m.max_drawdown)}</b>
  </span>
) : null;

// ---------------- Regime-Karte (Label, Kennzahlen, behalten, Strategie-Suche) ----------------
function RegimeCard({ analysis, scope, symbol, regime, usage, strategies, jobBlocked, execution, model, onChanged }) {
  const [showOpt, setShowOpt] = useState(false);
  const key = `${scopeKey(scope, symbol)}:${regime.id}`;
  const kept = (analysis.kept || {})[key] !== false;
  const assignment = (analysis.assignments || {})[key];
  const st = regime.stats || {};

  const toggleKeep = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/keep`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ scope, symbol, regime_id: regime.id, keep: !kept }),
    });
    if (r.ok) onChanged(); else toast.error('Speichern fehlgeschlagen');
  };

  const removeAssignment = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/assign`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ scope, symbol, regime_id: regime.id, remove: true }),
    });
    if (r.ok) { toast.success('Zuordnung entfernt'); onChanged(); } else toast.error('Fehlgeschlagen');
  };

  return (
    <div className={`rl-regime-card ${kept ? '' : 'discarded'} ${assignment ? 'assigned' : ''}`}
      style={{ borderLeft: `3px solid ${regimeColor(regime.id, [regime], model)}` }}
      data-testid={`regime-card-${scope}-${regime.id}`}>
      <div className="rl-regime-head">
        <span className="rl-dot" style={{ background: regimeColor(regime.id, [regime], model) }} />
        <label className="opt-check" style={{ paddingBottom: 0 }} title="Verworfene Regime werden bei Strategie-Suche und Zusammenbau übersprungen">
          <input type="checkbox" checked={kept} onChange={toggleKeep}
            data-testid={`regime-keep-${scope}-${regime.id}`} /> behalten
        </label>
        <b style={{ fontSize: 12.5 }}>#{regime.id + 1} {regime.label}</b>
        {regime.nnfx && (
          <span className={`rl-nnfx-tag ${regime.nnfx}`} title="Zuordnung im NNFX-Framework (3 Regime)"
            data-testid={`regime-nnfx-${scope}-${regime.id}`}>
            {NNFX_LABELS[regime.nnfx] || regime.nnfx}
          </span>
        )}
        <span className="opt-small">Anteil <b>{fmt(regime.share_pct, 0)}%</b></span>
        {usage && <span className="opt-small">· <b>{usage.days}</b> Tage in <b>{usage.segments}</b> Abschnitten</span>}
        <span style={{ flex: 1 }} />
        {kept && (
          <button className="opt-chip" onClick={() => setShowOpt(!showOpt)}
            data-testid={`regime-optimize-toggle-${scope}-${regime.id}`}>
            {showOpt ? 'Suche schließen' : (assignment ? 'Neue Strategie suchen' : 'Strategie suchen')}
          </button>
        )}
      </div>
      <div className="rl-regime-stats">
        <span title="Durchschnittliche Bewegung pro Tag in dieser Phase">Trend/Tag <b className={st.trend_pct_per_day >= 0 ? 'pos' : 'neg'}>{fmt(st.trend_pct_per_day, 2)}%</b></span>
        <span title="Verhältnis |Trend| zu Volatilität – unter ~0.45 gilt die Phase als seitwärts">Trendstärke <b>{fmt(st.trend_strength, 2)}</b></span>
        <span title="0 = reines Hin und Her, 1 = gerade Linie">Effizienz <b>{fmt(st.efficiency, 2)}</b></span>
        <span>Volatilität <b>{fmt(st.vol_pct, 2)}%</b></span>
        {st.score !== undefined && (
          <span title="Trend-Score = gewichteter t-Wert der Regression über alle Horizonte (|t|>2 = belegter Trend)">
            Trend-Score <b className={st.score >= 0 ? 'pos' : 'neg'}>{fmt(st.score, 2)}</b>
          </span>
        )}
        {st.adx !== undefined && <span title="Durchschnittlicher ADX in dieser Phase">ADX <b>{fmt(st.adx, 1)}</b></span>}
        {st.agreement !== undefined && (
          <span title="Anteil der Zeit-Horizonte, die dieselbe Richtung zeigen">
            Konsens <b>{fmt(st.agreement * 100, 0)}%</b>
          </span>
        )}
        {st.vol_z !== undefined && <span title="Volatilität gegen die eigene Historie (z-Wert)">Vola z <b>{fmt(st.vol_z, 2)}</b></span>}
        <span>Rel. Volumen <b>{fmt((regime.features || {}).rel_volume, 2)}</b></span>
      </div>
      {assignment && (
        <div className="rl-assign" data-testid={`regime-assignment-${scope}-${regime.id}`}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <b>✓ Bestätigte Strategie</b>
            <span className="opt-small">{assignment.mode === 'params'
              ? (assignment.strategy_name || assignment.strategy_id)
              : `Eigene Regeln (${(assignment.rules || []).length})`}</span>
            <M m={assignment.metrics} />
            {assignment.validation && (
              <span className={`opt-small ${(assignment.validation.pnl || 0) >= 0 ? 'pos' : 'neg'}`}>
                WF-PnL {fmt(assignment.validation.pnl)}
              </span>
            )}
            <span style={{ flex: 1 }} />
            <button className="opt-chip" onClick={removeAssignment} data-testid={`regime-assignment-remove-${scope}-${regime.id}`}>
              <Trash size={11} /> entfernen
            </button>
          </div>
          {(assignment.rules || []).length > 0 && (
            <div className="opt-params-list" style={{ marginTop: 4, marginBottom: 0 }}>
              {assignment.rules.map((r, i) => <span key={i} className="opt-param-pill">{r}</span>)}
            </div>
          )}
          {Object.keys(assignment.trade_params || {}).length > 0 && (
            <div className="opt-params-list" style={{ marginTop: 4, marginBottom: 0 }}>
              {Object.entries(assignment.trade_params).map(([k, v]) =>
                <span key={k} className="opt-param-pill trade">{k}: <b>{String(v)}</b></span>)}
            </div>
          )}
        </div>
      )}
      {showOpt && kept && (
        <RegimeOptimizePanel analysisId={analysis.id} scope={scope} symbol={symbol}
          regime={regime} strategies={strategies} analysisTf={analysis.timeframe}
          jobBlocked={jobBlocked} execution={execution}
          onAssigned={() => { setShowOpt(false); onChanged(); }} />
      )}
    </div>
  );
}

// ---------------- Zusammenbau + finaler Walk-Forward ----------------
function BuildAndTest({ analysis, scope, symbol, strategies, jobBlocked, execution, onChanged }) {
  const [name, setName] = useState('');
  const [baseStrategy, setBaseStrategy] = useState('');
  const [busy, setBusy] = useState(false);
  const [wfJob, setWfJob] = useState(null);
  const [wfResult, setWfResult] = useState(null);
  const pollRef = useRef(null);
  useEffect(() => () => clearInterval(pollRef.current), []);

  const key = scopeKey(scope, symbol);
  const model = scope === 'per_coin' ? analysis.per_coin?.[symbol]?.model : analysis.combined?.model;
  const regimes = model?.regimes || [];
  const keptRegimes = regimes.filter(r => (analysis.kept || {})[`${key}:${r.id}`] !== false);
  const assignments = Object.keys(analysis.assignments || {}).filter(k => k.startsWith(key + ':'));
  const savedWf = (analysis.walkforward || {})[key];
  const trainPct = analysis.settings?.train_pct ?? 100;
  const hasHoldout = trainPct < 100;
  const needsBase = keptRegimes.some(r => {
    const a = (analysis.assignments || {})[`${key}:${r.id}`];
    return a && !a.definition;
  }) || assignments.some(k => !(analysis.assignments[k] || {}).definition);

  const build = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/build`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ scope, symbol, name: name || undefined, strategy_id: baseStrategy || undefined }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Erstellen fehlgeschlagen'); return; }
      toast.success(`Dynamische Strategie erstellt (${d.regimes.length} Regime) – unter "Dynamische Strategien" im Optimizer verfügbar`);
    } catch { toast.error('Verbindungsfehler'); }
    finally { setBusy(false); }
  };

  const buildNnfx = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/build-nnfx`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ scope, symbol, name: name || undefined }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'NNFX-Aufbau fehlgeschlagen'); return; }
      toast.success(`NNFX-Strategie erstellt: ${Object.keys(d.regime_strategies).length} Regime → `
        + `${Object.keys(d.nnfx_strategies).length} NNFX-Strategien. Jedes Regime hat jetzt eine Zuordnung `
        + '– Feinjustierung je Regime weiterhin über "Strategie suchen".');
      onChanged();
    } catch { toast.error('Verbindungsfehler'); }
    finally { setBusy(false); }
  };

  const runWf = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/walkforward`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ scope, symbol, strategy_id: baseStrategy || undefined, execution }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
      setWfResult(null);
      setWfJob({ id: d.job_id, status: 'running', progress: 0, phase: 'Startet' });
      pollRef.current = setInterval(async () => {
        try {
          const j = await fetch(`${API_URL}/api/regime-lab/status/${d.job_id}`).then(x => x.json());
          setWfJob(j);
          if (j.status !== 'running') {
            clearInterval(pollRef.current);
            if (j.status === 'done') { setWfResult(j.result); onChanged(); }
            else if (j.status === 'error') toast.error(j.error || 'Walk-Forward fehlgeschlagen');
          }
        } catch { /* transient */ }
      }, 1500);
    } catch { toast.error('Verbindungsfehler'); }
  };

  const wf = wfResult || savedWf;
  return (
    <div className="rl-wf-box" data-testid={`regime-build-${scope}`}>
      <div className="opt-section-title">DYNAMISCHE STRATEGIE ZUSAMMENSTELLEN</div>
      <div className="opt-small" style={{ marginBottom: 8 }}>
        {assignments.length} von {keptRegimes.length} behaltenen Regimen haben eine bestätigte Strategie.
        {!hasHoldout && ' Hinweis: Diese Analyse hat keinen Holdout (Training 100%) – für den finalen Walk-Forward-Test eine Analyse mit z.B. 75% Training erstellen.'}
      </div>
      <div className="opt-setup">
        <label className="opt-field">Name
          <input value={name} onChange={e => setName(e.target.value)} placeholder={`Regime-Lab: ${analysis.name}`}
            data-testid={`regime-build-name-${scope}`} style={{ width: 220 }} />
        </label>
        <label className="opt-field" title={needsBase
          ? 'Erforderlich: mindestens ein Regime nutzt eine bestehende Strategie ohne eigene Regeln'
          : 'Optional: Basis-Strategie für Regime ohne Zuordnung'}>
          Basis-Strategie {needsBase ? '(erforderlich)' : '(optional)'}
          <select value={baseStrategy} onChange={e => setBaseStrategy(e.target.value)}
            data-testid={`regime-build-base-${scope}`}>
            <option value="">– automatisch –</option>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <button className="opt-apply" onClick={build} disabled={busy || !assignments.length}
          data-testid={`regime-build-btn-${scope}`}>
          Dynamische Strategie erstellen
        </button>
        <button className="opt-apply" onClick={runWf}
          disabled={!assignments.length || !hasHoldout || wfJob?.status === 'running' || jobBlocked}
          title="Testet die Kombination auf dem unangetasteten Holdout – Klassifikation rückblickend, kein Lookahead"
          data-testid={`regime-wf-btn-${scope}`}>
          <Play size={13} /> Finaler Walk-Forward (Holdout)
        </button>
        <button className="opt-apply" onClick={buildNnfx} disabled={busy || !keptRegimes.length}
          title="NNFX-Framework: die Regime werden auf Trend / Seitwärts / Volatilität gemappt und automatisch mit den drei NNFX-Strategien belegt (danach je Regime optimierbar)"
          data-testid={`regime-nnfx-btn-${scope}`}>
          NNFX-Framework anwenden
        </button>
      </div>
      {wfJob?.status === 'running' && (
        <div className="opt-progress">
          <div className="opt-progress-bar"><div style={{ width: `${wfJob.progress || 0}%`, height: '100%', background: '#00e5a0' }} /></div>
          <div className="opt-progress-text">{wfJob.phase} · {wfJob.progress || 0}%</div>
        </div>
      )}
      {wf && (
        <div data-testid={`regime-wf-result-${scope}`}>
          <div className={`rl-verdict ${wf.verdict?.dynamic_better ? 'good' : 'bad'}`}>
            {wf.verdict?.recommendation}
          </div>
          <div className="opt-small" data-testid={`regime-wf-provenance-${scope}`}
            title="Provenienz des Ergebnisses: Klassifikationsbasis, Versuchszähler auf diesem Holdout und Testzeitpunkt">
            Klassifikation: <b>{wf.label_basis === 'causal_live'
              ? 'kausal (live, ohne Rückkorrektur)' : 'rückblickende Referenz'}</b>
            {wf.attempt_no ? <> · Versuch <b>#{wf.attempt_no}</b> auf diesem Holdout</> : null}
            {wf.created_at ? <> · getestet {fmtDateTime(wf.created_at)}</> : null}
            {(wf.dynamic_test?.trades ?? 0) < 10
              ? <span style={{ color: '#FFB74D' }}> · ⚠ nur {wf.dynamic_test?.trades ?? 0} Trades im Holdout – wenig belastbar</span>
              : null}
          </div>
          <div className="opt-compare">
            <div className={`opt-card ${wf.verdict?.dynamic_better ? 'best' : ''}`}>
              <div className="opt-card-title">DYNAMISCH (HOLDOUT · {wf.switches} Phasenwechsel)</div>
              <M m={wf.dynamic_test} />
            </div>
            <div className="opt-card">
              <div className="opt-card-title">BESTE EINZELSTRATEGIE STATISCH (BENCHMARK)</div>
              <M m={wf.best_single?.metrics} />
              {wf.best_single && <div className="opt-small" style={{ marginTop: 4 }}>{wf.best_single.label}</div>}
            </div>
          </div>
          {(wf.per_regime || []).length > 0 && (
            <div className="opt-small" style={{ margin: '6px 0' }}>
              Je Regime im Holdout: {wf.per_regime.map(p => (
                <span key={p.regime} className="opt-param-pill" style={{ marginRight: 4 }}>
                  {p.label || `#${p.regime + 1}`}: <b className={p.metrics.pnl >= 0 ? 'pos' : 'neg'}>{fmt(p.metrics.pnl)}</b> ({p.metrics.trades} T.)
                </span>
              ))}
            </div>
          )}
          {wfResult?.points?.length > 0 && (
            <EquityChart points={wfResult.points} title="Equity im Holdout (dynamisch)" />
          )}
        </div>
      )}
    </div>
  );
}

// Chart-Daten je Asset nachladen: das Analyse-Dokument kommt seit 09/2026 OHNE
// chart/chart_emas (mehrere MB bei 20+ Assets) – die Kurven holt jeder Chart
// selbst über /chart/{symbol}. Liegen die Daten noch im Dokument (full=1 /
// Bestand), werden sie direkt verwendet.
function LazyRegimeChart({ analysis, symbol, ...rest }) {
  const inDoc = analysis.chart?.[symbol];
  const [data, setData] = useState(inDoc ? { prices: inDoc, emas: analysis.chart_emas?.[symbol] } : null);
  useEffect(() => {
    if (inDoc) return undefined;
    let alive = true;
    setData(null);
    fetch(`${API_URL}/api/regime-lab/${analysis.id}/chart/${symbol}`).then(r => r.json())
      .then(d => { if (alive) setData({ prices: d.prices || [], emas: d.emas }); })
      .catch(() => { if (alive) setData({ prices: [], emas: null }); });
    return () => { alive = false; };
  }, [analysis.id, symbol, inDoc]);
  if (!data) {
    return <div className="rl-chart opt-small" data-testid={`regime-chart-loading-${symbol}`}>Lade Chart {symbol.replace('USDT', '')} …</div>;
  }
  return <RegimeChart prices={data.prices} emas={data.emas} {...rest} />;
}

// ---------------- Detail einer Analyse ----------------
function AnalysisDetail({ analysis, strategies, jobBlocked, execution, onChanged }) {
  const scopes = [];
  if (analysis.combined) scopes.push({ id: 'combined', label: 'Alle Assets (kombiniert)' });
  (analysis.symbols || []).forEach(s => {
    if (analysis.per_coin?.[s] && !analysis.per_coin[s].error) {
      scopes.push({ id: `coin:${s}`, label: s.replace('USDT', '') });
    }
  });
  const [tab, setTab] = useState(scopes[0]?.id || 'combined');
  const isCombined = tab === 'combined';
  const symbol = isCombined ? null : tab.slice(5);
  const scope = isCombined ? 'combined' : 'per_coin';
  const model = isCombined ? analysis.combined?.model : analysis.per_coin?.[symbol]?.model;
  const usage = isCombined ? analysis.combined?.usage : analysis.per_coin?.[symbol]?.usage;
  const regimes = model?.regimes || [];
  const trainEnd = (sym) => analysis.bounds?.[sym]?.train_end_ts;
  const perSymbol = isCombined
    ? (analysis.combined?.per_symbol || {})
    : (symbol ? { [symbol]: analysis.per_coin?.[symbol] || {} } : {});
  const validationSummary = isCombined
    ? analysis.combined?.validation
    : (analysis.per_coin?.[symbol]?.validation);
  const engine = model?.engine || 'kmeans';
  const [showIdeal, setShowIdeal] = useState(false);
  const [showLive, setShowLive] = useState(false);

  const currentBadges = Object.entries(perSymbol)
    .map(([sym, v]) => [sym, v?.current])
    .filter(([, c]) => c && c.regime !== null && c.regime !== undefined);

  return (
    <div data-testid="regime-analysis-detail">
      {scopes.length > 1 && (
        <div className="rl-scope-tabs" data-testid="regime-scope-tabs"
          title="Asset-Auswahl der Analyse: direkt unter der Freigabe, damit nicht erst alle Assets durchgescrollt werden müssen">
          <span className="opt-small" style={{ alignSelf: 'center' }}>Asset:</span>
          {scopes.map(s => (
            <button key={s.id} className={`opt-chip ${tab === s.id ? 'on' : ''}`}
              onClick={() => setTab(s.id)} data-testid={`regime-scope-tab-${s.id}`}>
              {s.label}{tab === s.id ? ' · ausgewählt' : ''}
            </button>
          ))}
        </div>
      )}
      <div className="opt-small" style={{ margin: '4px 0 8px' }}>
        {analysis.timeframe} · {analysis.days} Tage · Training {analysis.settings?.train_pct}%
        {analysis.settings?.train_pct < 100 && ' (Rest = Holdout für den finalen Walk-Forward)'}
        {analysis.dataset?.per_symbol
          ? <span data-testid="regime-detail-dataset"
              title="Datensatz-Manifest vorhanden: spätere Läufe auf dieser Analyse lesen exakt dieselben Kerzen (Anzahl + Hash geprüft)"> · Daten gepinnt</span>
          : <span data-testid="regime-detail-dataset" style={{ opacity: 0.6 }}
              title="Bestandsanalyse ohne Datensatz-Manifest – das Datenfenster ist nicht fixiert, spätere Läufe sind nicht exakt reproduzierbar"> · Daten nicht gepinnt</span>}
        {(() => {
          const unc = analysis.combined?.uncertainty?.per_symbol?.[symbol];
          if (!unc) return null;
          return (
            <span data-testid="regime-detail-uncertainty"
              title="Unsicherheitskalibrierung: mittlerer Abstand zwischen heuristischem Regime-Score und tatsächlicher Live=Final-Trefferquote. Der Score ist KEINE kalibrierte Wahrscheinlichkeit; bei wenig Daten wird über die Anlageklasse gepoolt.">
              {' · Score-Kalibrierung: '}
              {unc.verdict === 'ok'
                ? `Ø-Abweichung ${fmt(unc.gap_pp, 1)}pp`
                : 'zu wenig Daten'}
              {String(unc.basis || '').startsWith('pooled') ? ' (Klassen-Pool)' : ''}
            </span>
          );
        })()} ·
        {engine === 'v2'
          ? ` Engine v2 · ${(model?.regime_mode || model?.config?.regime_mode || 9)}-Regime-Modus`
            + ((model?.config?.detector || 'reactive') === 'ema'
              ? ` · EMA-Steigungs-Regime (EMA ${fmt(model?.config?.ema_regime_days ?? 9, 0)} Tage`
                + `, Schwelle ${model?.config?.ema_regime_thr ?? 0.18}×Vola)`
                + ` · Mini-Phasen-Filter ${model?.config?.min_phase_days ? `${fmt(model.config.min_phase_days, 1)}d` : 'auto'}`
              : (model?.config?.detector || 'reactive') === 'kombi'
              ? ` · Kombi-Detektor (EMA ${fmt(model?.config?.kombi_ema_days ?? 14, 0)} Tage`
                + `, Schwelle ${model?.config?.kombi_thr ?? 0.18}×Vola`
                + `, Fenster ${fmt(model?.config?.kombi_slope_days ?? 5, 0)}d`
                + `, Trend-Dominanz ${fmt(model?.config?.kombi_dominance_days ?? 3, 0)}d`
                + `${model?.config?.kombi_pivot_accel === false ? ', ohne' : ', mit'} Umkehrpunkt-Beschleuniger)`
                + ` · Mini-Phasen-Filter ${model?.config?.min_phase_days ? `${fmt(model.config.min_phase_days, 1)}d` : 'auto'}`
              : (model?.config?.detector || 'reactive') !== 'regression'
              ? ` · Umkehrpunkt-Erkennung (reaktiv) · Umkehr-Schwelle ${model?.config?.rev_atr_mult ?? 3}×ATR`
                + ` · Persistenz ${model?.config?.persist_candles ?? 3} Kerzen`
                + ` · Mini-Phasen-Filter ${model?.config?.min_phase_days ? `${fmt(model.config.min_phase_days, 1)}d` : 'auto'}`
                + (model?.adapt?.profile ? ` · Glättung "${model.adapt.profile}"` : '')
              : ` · Horizonte ${(model?.config?.horizons_days || []).map(d => `${d}d`).join('/')}`
                + (model?.adapt?.profile ? ` · Glättung "${model.adapt.profile}"` : '')
                + ` · Mindesthaltedauer ${fmt(model?.config?.min_hold_days, 1)}d`
                + ` · Trend-Schwelle t=${model?.config?.trend_t} · ADX≥${model?.config?.adx_min}`)
          : ` Cluster-Modell · Lookback ${analysis.settings?.lookback_days}d · max. ${analysis.settings?.max_regimes} Regime · Cluster-Qualität ${fmt(model?.silhouette, 2)}`}
      </div>
      <RegimeQualityCard quality={(analysis.quality || {})[scopeKey(scope, symbol)]} />
      {model?.adapt?.report?.candidates?.length > 0 && (
        <div className="rl-sim" data-testid="regime-adapt-report">
          <span className="opt-small" style={{ alignSelf: 'center' }}
            title="Es wurden mehrere Glättungs-Profile berechnet und nach Plausibilität, Rückblick-Übereinstimmung und Abschnittslänge bewertet. Das beste wurde verwendet.">
            Glättungs-Profile geprüft:
          </span>
          {model.adapt.report.candidates.map(c => (
            <span key={c.profile} className="opt-param-pill"
              style={c.profile === model.adapt.profile
                ? { borderColor: 'rgba(0,229,160,0.5)' } : undefined}
              title={`Verstöße ${c.violation_bars_pct}% · Rückblick-Treffer ${c.direction_pct}% · Ø Abschnitt ${c.avg_segment_days}d (Ziel ${c.target_segment_days}d)${c.passes === false ? ' · Plausibilitätsprüfung NICHT bestanden' : ''}`}>
              {c.profile} <b>{fmt(c.quality, 1)}</b>{c.passes === false ? ' ✕' : ''}
            </span>
          ))}
        </div>
      )}
      {currentBadges.length > 0 && (
        <div className="rl-current-row" data-testid="regime-current-row">
          {currentBadges.map(([sym, c]) => (
            <div key={sym} className="rl-current" data-testid={`regime-current-${sym}`}
              title={c.reason || ''}>
              <span className="rl-dot" style={{ background: regimeColor(c.regime, regimes, model) }} />
              <b>{sym.replace('USDT', '')}</b>
              <span className="rl-current-label">{c.label}</span>
              {c.nnfx && <span className={`rl-nnfx-tag ${c.nnfx}`}>{NNFX_LABELS[c.nnfx] || c.nnfx}</span>}
              <span className="opt-small">Sicherheit <b>{fmt(c.confidence, 0)}%</b></span>
              {c.strength && <span className="opt-small">Stärke <b>{c.strength}</b></span>}
              {c.last_switch && (
                <span className="opt-small">seit {fmtDate(c.last_switch)}</span>
              )}
              {c.early_warning?.active && (
                <span className="rl-warn" title={c.early_warning.reason}
                  data-testid={`regime-warn-${sym}`}>
                  Wechsel → {c.early_warning.next_label} · {fmt(c.early_warning.probability_pct, 0)}%
                  {c.early_warning.eta_days !== null && c.early_warning.eta_days !== undefined
                    ? ` · ~${c.early_warning.eta_days} Tage` : ''}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      <RegimeValidation summary={validationSummary} perSymbol={perSymbol} />
      {(() => {
        const reps = Object.entries(perSymbol)
          .map(([s, v]) => [s, v?.corrections]).filter(([, c]) => c);
        if (!reps.length) return null;
        return (
          <div className="rl-sim" data-testid="regime-corrections">
            <span className="opt-small" style={{ alignSelf: 'center' }}
              title="Reaktive Erkennung: Umkehrpunkte werden erst nach Bestätigung (2-3 Kerzen Persistenz) erkannt – die Phase wird dann rückwirkend bis zum Hoch-/Tiefpunkt korrigiert. Hier steht, wie viele Umkehrpunkte gefunden wurden und wie schnell die Erkennung im Schnitt war.">
              Umkehrpunkt-Erkennung (selbstkorrigierend):
            </span>
            {reps.map(([s, c]) => (
              <span key={s} className="opt-param-pill" data-testid={`regime-corrections-${s}`}>
                {s.replace('USDT', '')} <b>{c.pivots}</b> Umkehrpunkte
                {c.avg_delay_days !== null && c.avg_delay_days !== undefined
                  ? <> · erkannt nach Ø <b>{fmt(c.avg_delay_days, 1)}d</b></> : null}
              </span>
            ))}
          </div>
        );
      })()}
      {(() => {
        const ags = Object.entries(perSymbol)
          .map(([s, v]) => [s, v?.live_agreement]).filter(([, a]) => a?.direction_pct != null);
        if (!ags.length) return null;
        const col = (p) => (p >= 65 ? 'rgba(0,229,160,0.45)'
          : p >= 50 ? 'rgba(255,165,2,0.45)' : 'rgba(255,71,87,0.45)');
        return (
          <div className="rl-sim" data-testid="regime-live-agreement">
            <span className="opt-small" style={{ alignSelf: 'center' }}
              title="Wie oft trifft die LIVE-Erkennung (ohne Zukunftswissen, Kerze für Kerze) die Richtung der final korrigierten Phasen? 'Holdout' = nur der Walk-Forward-Testzeitraum nach der Trainings-Grenze – der ehrlichste Wert dafür, ob die Regime-Umschaltung für Paper-/Live-Trading taugt. 'Trend' = Trefferquote nur auf Trend-Kerzen (Auf/Ab).">
              Live-Trefferquote (Richtung):
            </span>
            {ags.map(([s, a]) => (
              <span key={s} className="opt-param-pill"
                style={{ borderColor: col(a.holdout_direction_pct ?? a.direction_pct) }}
                data-testid={`regime-live-agreement-${s}`}>
                {s.replace('USDT', '')} <b>{fmt(a.direction_pct, 0)}%</b>
                {a.holdout_direction_pct != null
                  ? <> · Holdout <b>{fmt(a.holdout_direction_pct, 0)}%</b></> : null}
                {a.trend_hit_pct != null
                  ? <> · Trend <b>{fmt(a.trend_hit_pct, 0)}%</b></> : null}
              </span>
            ))}
          </div>
        );
      })()}
      {isCombined && (analysis.combined?.coin_similarity || []).length > 0 && (
        <div className="rl-sim" data-testid="regime-coin-similarity">
          <span className="opt-small" style={{ alignSelf: 'center' }}
            title="Anteil der Zeit, in der zwei Coins im selben Regime sind – Coins mit hoher Übereinstimmung passen gut in eine gemeinsame dynamische Strategie">
            Regime-Übereinstimmung:
          </span>
          {analysis.combined.coin_similarity.map((s, i) => (
            <span key={i} className="opt-param-pill"
              style={s.agreement_pct >= 70 ? { borderColor: 'rgba(0,229,160,0.4)' } : undefined}>
              {s.a.replace('USDT', '')}↔{s.b.replace('USDT', '')} <b>{fmt(s.agreement_pct, 0)}%</b>
            </span>
          ))}
        </div>
      )}
      <TransitionMatrix analysisId={analysis.id} scope={scope} symbol={symbol}
        regimes={regimes} model={model} />
      {isCombined
        ? (analysis.symbols || []).map(sym => (
          <LazyRegimeChart key={sym} analysis={analysis} symbol={sym} title={sym.replace('USDT', '')}
            segments={analysis.combined?.per_symbol?.[sym]?.segments}
            idealSegments={showIdeal ? analysis.combined?.per_symbol?.[sym]?.ideal?.segments : null}
            liveSegments={analysis.combined?.per_symbol?.[sym]?.live_segments}
            liveBand={showLive}
            regimes={regimes} model={model} trainEndTs={trainEnd(sym)} />
        ))
        : (
          <LazyRegimeChart analysis={analysis} symbol={symbol} title={symbol.replace('USDT', '')}
            segments={analysis.per_coin?.[symbol]?.segments}
            idealSegments={showIdeal ? analysis.per_coin?.[symbol]?.ideal?.segments : null}
            liveSegments={analysis.per_coin?.[symbol]?.live_segments}
            liveBand={showLive}
            regimes={regimes} model={model} trainEndTs={trainEnd(symbol)} height={240} />
        )}
      {Object.values(perSymbol).some(v => v?.live_segments?.length) && (
        <label className="opt-check" style={{ paddingBottom: 0 }}
          title="Band oben im Chart: so hat die Erkennung die Phasen über den GESAMTEN Zeitraum in Echtzeit gesehen (ohne rückwirkende Korrektur bis zum Umkehrpunkt). Abweichungen zum Hintergrund = Erkennungsverzögerung an den Umkehrpunkten. Der Bereich nach der orangen Holdout-Linie zeigt die Live-Sicht bereits als leuchtenden Hintergrund.">
          <input type="checkbox" checked={showLive} onChange={e => setShowLive(e.target.checked)}
            data-testid="regime-show-live" /> Live-Band über gesamten Zeitraum einblenden
        </label>
      )}
      {Object.values(perSymbol).some(v => v?.ideal?.segments?.length) && (
        <label className="opt-check" style={{ paddingBottom: 0 }}
          title="Vergleichsband: so lagen die Phasen im Rückblick (mit Zukunftssicht) – nur zur Kontrolle, wird nie für Backtests/Live genutzt">
          <input type="checkbox" checked={showIdeal} onChange={e => setShowIdeal(e.target.checked)}
            data-testid="regime-show-ideal" /> Rückblick-Vergleich einblenden (nur Kontrolle)
        </label>
      )}
      <div className="opt-section-title">REGIME PRÜFEN, BEHALTEN & STRATEGIEN SUCHEN</div>
      <RegimeInsights analysis={analysis} scopeKeyStr={scopeKey(scope, symbol)} regimes={regimes}
        usage={usage} model={model} quality={(analysis.quality || {})[scopeKey(scope, symbol)]}
        perSymbol={perSymbol} />
      {regimes.map(r => (
        <RegimeCard key={r.id} analysis={analysis} scope={scope} symbol={symbol}
          regime={r} usage={usage?.[String(r.id)]} strategies={strategies}
          jobBlocked={jobBlocked} execution={execution} model={model}
          onChanged={onChanged} />
      ))}
      <BuildAndTest analysis={analysis} scope={scope} symbol={symbol}
        strategies={strategies} jobBlocked={jobBlocked} execution={execution}
        onChanged={onChanged} />
    </div>
  );
}

// ---------------- Haupt-Panel ----------------
export default function RegimeLab({ onClose }) {
  const saved = useRef(loadState()).current;
  const [coins, setCoins] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [selCoins, setSelCoins] = useState(saved.selCoins || []);
  const [timeframe, setTimeframe] = useState(saved.timeframe || '15m');
  const [days, setDays] = useState(saved.days ?? 360);
  const [scope, setScope] = useState(saved.scope || 'both');
  const [maxRegimes, setMaxRegimes] = useState(saved.maxRegimes ?? 5);
  const [lookback, setLookback] = useState(saved.lookback ?? 3);
  const [minShare, setMinShare] = useState(saved.minShare ?? 5);
  const [confMin, setConfMin] = useState(saved.confMin ?? 70);
  const [minHold, setMinHold] = useState(saved.minHold ?? 0);
  const [trainPct, setTrainPct] = useState(saved.trainPct ?? 75);
  const [engine, setEngine] = useState(saved.engine || 'v2');
  const [engineConfig, setEngineConfig] = useState(saved.engineConfig || {});
  // Zuletzt übernommene Kalibrierung (Anzeige „aktuelle Kalibrierungsdaten“)
  const [calibApplied, setCalibApplied] = useState(saved.calibApplied || null);
  const [execution, setExecution] = useState(saved.execution || 'cloud');
  const [lwOnline, setLwOnline] = useState(false);
  const [showLW, setShowLW] = useState(false);
  const [name, setName] = useState('');
  const [job, setJob] = useState(null);
  const [analyses, setAnalyses] = useState(null);
  const [shadowTrades, setShadowTrades] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const selectedRef = useRef(null);
  // Auswahl wechseln: alte Detail-Daten SOFORT verwerfen, sonst zeigen Banner
  // (oben) und Punkt 3 bis zum Laden noch die vorher gewählte Analyse.
  const selectAnalysis = useCallback((aid) => {
    selectedRef.current = aid;
    setSelected(aid);
    setDetail(null);
  }, []);
  const [autopilotResult, setAutopilotResult] = useState(null);
  const [queueRefresh, setQueueRefresh] = useState(0);
  const pollRef = useRef(null);
  const jobRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify({
        selCoins, timeframe, days, scope, maxRegimes, lookback, minShare, confMin, minHold, trainPct, execution,
        engine, engineConfig, calibApplied,
      }));
    } catch { /* quota */ }
  }, [selCoins, timeframe, days, scope, maxRegimes, lookback, minShare, confMin, minHold, trainPct, execution,
    engine, engineConfig, calibApplied]);

  const loadList = useCallback(() => {
    fetch(`${API_URL}/api/regime-lab/list`).then(r => r.json())
      .then(d => setAnalyses(d.analyses || [])).catch(() => setAnalyses([]));
    fetch(`${API_URL}/api/regime-lab/releases`).then(r => r.json())
      .then(d => setShadowTrades(d?.activation?.shadow_trades ?? null)).catch(() => {});
  }, []);

  const loadDetail = useCallback((aid) => {
    fetch(`${API_URL}/api/regime-lab/${aid}`).then(r => r.json())
      .then(d => {
        if (selectedRef.current !== aid) return; // verspätete Antwort einer alten Auswahl
        setDetail({ ...d.analysis, quality: d.quality || {} });
      })
      .catch(() => { if (selectedRef.current === aid) toast.error('Analyse konnte nicht geladen werden'); });
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/api/coins`).then(r => r.json()).then(d => {
      const cs = d.coins || [];
      setCoins(cs);
      setSelCoins(prev => {
        const valid = (prev || []).filter(c => cs.includes(c));
        return valid.length ? valid : cs.slice(0, 4);
      });
    });
    fetch(`${API_URL}/api/strategies`).then(r => r.json()).then(d => setStrategies(d.strategies || []));
    loadList();
    const checkLw = () => fetch(`${API_URL}/api/localworker/status`).then(r => r.json())
      .then(d => setLwOnline(!!d.online)).catch(() => setLwOnline(false));
    checkLw();
    const lwIv = setInterval(checkLw, 10000);
    // Haupt-Balken: JEDER Regime-Lab-Job (Analyse, Kalibrierung, Auto-Kalibrierung,
    // Ablation, EMA-Vergleich, Autopilot, Warteschlange) wird hier angezeigt –
    // egal wo er gestartet wurde. Läuft nichts, wird alle 4s nachgesehen.
    const checkActive = () => {
      if (jobRef.current?.status === 'running') return;
      fetch(`${API_URL}/api/regime-lab/active`).then(r => r.json()).then(d => {
        if (d.active && d.active.id !== jobRef.current?.id) attachPoll(d.active.id, d.active.kind);
      }).catch(() => {});
    };
    checkActive();
    const activeIv = setInterval(checkActive, 4000);
    return () => { clearInterval(pollRef.current); clearInterval(lwIv); clearInterval(activeIv); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { if (selected) loadDetail(selected); }, [selected, loadDetail]);

  const attachPoll = (jobId, kind) => {
    const initial = { id: jobId, kind, status: 'running', progress: 0, phase: 'Läuft...' };
    jobRef.current = initial;
    setJob(initial);
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetch(`${API_URL}/api/regime-lab/status/${jobId}`).then(x => x.json());
        if (!j?.status) return;
        jobRef.current = j;
        setJob(j);
        if (j.status !== 'running') {
          clearInterval(pollRef.current);
          if (j.status === 'done' && j.kind === 'analysis') {
            toast.success('Regime-Analyse fertig');
            loadList();
            if (j.result?.analysis_id) selectAnalysis(j.result.analysis_id);
          } else if (j.status === 'done' && j.kind === 'autopilot') {
            setAutopilotResult({ ...(j.result || {}), job_id: jobId });
          } else if (j.status === 'error') {
            toast.error(j.error || 'Job fehlgeschlagen');
          }
          setQueueRefresh(k => k + 1);
          setTimeout(() => { if (jobRef.current?.id === jobId) { jobRef.current = null; setJob(null); } }, 4000);
        }
      } catch { /* transient */ }
    }, 1500);
  };

  // Request-Body wie beim direkten Start – wird auch für die Nacht-Serie genutzt
  const buildAnalysisBody = () => ({
    symbols: selCoins, timeframe, days, scope, name: name || undefined,
    max_regimes: maxRegimes, lookback_days: lookback, min_share_pct: minShare,
    confidence_min: confMin, min_hold_days: minHold, train_pct: trainPct,
    engine, engine_config: engine === 'v2' ? { min_phase_days: minHold, ...engineConfig } : undefined,
    execution,
  });

  const queueAnalysisToSeries = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!selCoins.length) { toast.error('Mindestens 1 Coin wählen'); return; }
    const item = await addToSeries('regime_analysis', buildAnalysisBody());
    if (item) setQueueRefresh(k => k + 1);
  };

  // Ausgewählte gespeicherte Analyse als Ausgangspunkt übernehmen: Coins,
  // Timeframe, Zeitraum, Training-% und die komplette Erkennungs-Einstellung
  // (Grundgerüst + Feineinstellungen) – danach oben weiter optimieren.
  const adoptAnalysisSettings = (a) => {
    if (!a) return;
    const s = a.settings || {};
    if (Array.isArray(a.symbols) && a.symbols.length) setSelCoins(a.symbols);
    if (a.timeframe) setTimeframe(a.timeframe);
    if (a.days) setDays(Number(a.days));
    if (s.train_pct) setTrainPct(Number(s.train_pct));
    if (s.confidence_min) setConfMin(Number(s.confidence_min));
    if (s.min_hold_days !== undefined && s.min_hold_days !== null) setMinHold(Number(s.min_hold_days));
    if (s.engine) setEngine(s.engine);
    if (s.engine === 'kmeans') {
      if (s.max_regimes) setMaxRegimes(Number(s.max_regimes));
      if (s.lookback_days) setLookback(Number(s.lookback_days));
      if (s.min_share_pct) setMinShare(Number(s.min_share_pct));
    }
    if (s.engine_config && typeof s.engine_config === 'object') setEngineConfig({ ...s.engine_config });
    toast.success(`Einstellungen von „${a.name}“ übernommen – oben weiter optimieren (Kalibrierung, Autopilot, Werkzeuge) und dann neu „Regime suchen & speichern“`);
  };

  const startAnalysis = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!selCoins.length) { toast.error('Mindestens 1 Coin wählen'); return; }
    if (execution === 'local' && !lwOnline) {
      toast.error('Kein lokaler Worker verbunden – Worker starten oder Cloud wählen');
      return;
    }
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/analyze`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(buildAnalysisBody()),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
      attachPoll(d.job_id, 'analysis');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const cancelJob = async () => {
    if (job?.id) await fetch(`${API_URL}/api/regime-lab/cancel/${job.id}`, { method: 'POST', headers: authHeaders() });
  };

  // Haupt-Balken: Pausieren / Suche beenden / Notfall-Reset (wie Strategie-Optimizer)
  const jobControls = useLabJobControls(job, {
    setJob: (fn) => { const next = fn(jobRef.current); jobRef.current = next; setJob(next); },
    onReset: () => { clearInterval(pollRef.current); jobRef.current = null; setJob(null); setQueueRefresh(k => k + 1); },
  });

  const removeAnalysis = async (aid, e) => {
    e.stopPropagation();
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-lab/${aid}`, { method: 'DELETE', headers: authHeaders() });
    if (r.ok) {
      toast.success('Analyse gelöscht');
      if (selected === aid) selectAnalysis(null);
      loadList();
    } else toast.error('Löschen fehlgeschlagen');
  };

  const jobBlocked = job?.status === 'running';
  return (
    <SafeOverlay className="opt-overlay" onClose={onClose} testId="regime-lab-overlay">
      <div className="opt-panel" onClick={e => e.stopPropagation()} data-testid="regime-lab-modal">
        <div className="opt-header">
          <h2 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ChartScatter size={20} weight="bold" /> Regime-Lab
          </h2>
          <button className="opt-close" onClick={onClose} data-testid="regime-lab-close"><X size={22} weight="bold" /></button>
        </div>
        <div className="opt-small" style={{ marginBottom: 8 }} data-testid="regime-lab-workflow">
          <b>Ziel:</b> für deine Coins die Marktphasen (Auf / Seitwärts / Ab) so erkennen, dass die Live-Erkennung
          (ohne Zukunftswissen) möglichst oft richtig liegt – gemessen auf dem unangetasteten Holdout (Out-of-Sample).
          <b> Ablauf:</b> 1 Grundgerüst wählen → 2 kalibrieren (Bestes wird automatisch übernommen) →
          3 „Regime suchen &amp; speichern“ → 4 Analyse öffnen, Note &amp; Regime-Insights lesen →
          danach Strategien je Regime suchen und final per Walk-Forward auf dem Holdout testen.
        </div>
        <RegimeLabSummary engine={engine} engineConfig={engineConfig} calibApplied={calibApplied}
          analysesCount={analyses?.length} detail={detail} trainPct={trainPct}
          jobRunning={job?.status === 'running'} />
        <RegimeLabHelp />

        <div className="opt-exec-row" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', margin: '0 0 10px' }}>
          <div className="bt-exec" data-testid="regime-execution-toggle">
            <span className="bt-exec-label">Ausführung</span>
            <button className={`bt-exec-btn ${execution === 'cloud' ? 'on' : ''}`}
              onClick={() => setExecution('cloud')} data-testid="regime-exec-cloud"
              title="Berechnung auf dem Server – für kurze Zeiträume ok">
              <Cloud size={13} weight="bold" /> Cloud
            </button>
            <button className={`bt-exec-btn ${execution === 'local' ? 'on' : ''}`}
              onClick={() => setExecution('local')} data-testid="regime-exec-local"
              title="Berechnung auf deinem PC (lokaler Worker, Multi-Core + lokale Kerzendaten) – empfohlen ab ~1000 Tagen, entlastet die Website. Worker-Version 1.5.0+ nötig.">
              <Desktop size={13} weight="bold" /> Lokal
              <span className={`bt-exec-dot ${lwOnline ? 'on' : ''}`} data-testid="regime-exec-dot" />
            </button>
            <button className="bt-exec-manage" onClick={() => setShowLW(true)}
              title="Lokale Ausführung verwalten: Worker, Einstellungen & Marktdaten"
              data-testid="regime-exec-manage">
              <Gear size={13} weight="bold" />
            </button>
          </div>
          {execution === 'local' && (
            <span className="opt-small">
              Gilt für alle Regime-Lab-Jobs (Analyse, Strategie-Suche, Walk-Forward)
              {!lwOnline && ' · kein Worker verbunden'}
            </span>
          )}
        </div>
        {showLW && <LocalWorkerPanel onClose={() => setShowLW(false)} />}

        <div className="opt-row">
          <div className="opt-label">1 · GRUNDGERÜST WÄHLEN, KALIBRIEREN &amp; REGIME SUCHEN</div>
          {selected && detail && (
            <div className="rl-edge-banner neutral" data-testid="regime-base-banner"
              style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
              <span>
                <b>Ausgewählte Analyse (unten): „{detail.name}“</b>
                <span className="opt-small" style={{ color: 'inherit', opacity: 0.85 }}>
                  {' '}· {(detail.symbols || []).map(s => s.replace('USDT', '')).join(', ')} · {detail.timeframe} · {detail.days}d ·
                  Grundgerüst {DETECTOR_LABELS[detail.settings?.engine === 'kmeans' ? 'kmeans' : (detail.settings?.engine_config?.detector || 'reactive')]
                    || detail.settings?.engine_config?.detector || 'reactive'} · Training {detail.settings?.train_pct}%
                </span>
              </span>
              <button className="opt-chip" onClick={() => adoptAnalysisSettings(detail)} data-testid="regime-base-adopt"
                title="Coins, Timeframe, Zeitraum, Training-% und die komplette Erkennungs-Einstellung dieser Analyse hier oben übernehmen – dann Kalibrierung / Autopilot / Werkzeuge darauf aufsetzen und neu speichern">
                ↑ Einstellungen übernehmen &amp; weiter optimieren
              </button>
              <button className="opt-chip" onClick={() => selectAnalysis(null)} data-testid="regime-base-clear"
                title="Auswahl aufheben">
                <X size={11} />
              </button>
            </div>
          )}
          <div className="opt-chips" style={{ marginBottom: 8 }}>
            {coins.map(c => (
              <button key={c} className={`opt-chip ${selCoins.includes(c) ? 'on' : ''}`}
                onClick={() => setSelCoins(selCoins.includes(c) ? selCoins.filter(x => x !== c) : [...selCoins, c])}
                data-testid={`regime-coin-${c}`}>{c.replace('USDT', '')}</button>
            ))}
          </div>
          <RegimeEngineSettings engine={engine} setEngine={setEngine}
            config={engineConfig} setConfig={setEngineConfig}
            calibApplied={calibApplied} setCalibApplied={setCalibApplied}
            calibrateCtx={{ symbols: selCoins, timeframe, days, execution }} />
          <div className="opt-setup">
            <label className="opt-field">Name (optional)
              <input value={name} onChange={e => setName(e.target.value)} style={{ width: 170 }}
                placeholder="z.B. 15m · 360d · Top4" data-testid="regime-name" />
            </label>
            <label className="opt-field">Timeframe
              <select value={timeframe} onChange={e => setTimeframe(e.target.value)} data-testid="regime-tf">
                {TIMEFRAMES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
              </select>
            </label>
            <label className="opt-field">Zeitraum
              <select value={days} onChange={e => setDays(parseInt(e.target.value))} data-testid="regime-days">
                {DAY_OPTIONS.map(d => <option key={d} value={d}>{`${d} Tage`}</option>)}
              </select>
            </label>
            <label className="opt-field" title="Kombiniert = EIN Regime-Modell über alle gewählten Assets (gleiche Phasen-Definition, mehr Daten, robuster – aber Eigenheiten einzelner Assets gehen unter) · Je Asset einzeln = eigenes Modell pro Asset (passt sich z.B. Gold vs. BTC an, braucht aber pro Asset genug Historie) · Beides = beide Varianten zum Vergleichen">
              Modell-Umfang
              <select value={scope} onChange={e => setScope(e.target.value)} data-testid="regime-scope">
                <option value="both">Beides (kombiniert + je Asset)</option>
                <option value="combined">Nur kombiniert (alle Assets)</option>
                <option value="per_coin">Nur je Asset einzeln</option>
              </select>
            </label>
            <label className="opt-field" title="Vorderer Anteil für Regime-Clustering & Strategie-Suche; der Rest bleibt unangetastet für den finalen Walk-Forward">
              Training %
              <NumInput int min={50} max={100} value={trainPct}
                onCommit={(v) => setTrainPct(v || 75)}
                data-testid="regime-trainpct" style={{ width: 55 }} />
            </label>
            {engine === 'kmeans' && (
              <>
                <label className="opt-field" title="Fenster für Trend/Volatilität/Effizienz – größer = trägere, stabilere Regime">
                  Lookback (Tage)
                  <NumInput min={0.5} max={60} step={0.5} value={lookback}
                    onCommit={(v) => setLookback(v || 3)}
                    data-testid="regime-lookback" style={{ width: 55 }} />
                </label>
                <label className="opt-field">Max. Regime
                  <NumInput int min={2} max={10} value={maxRegimes}
                    onCommit={(v) => setMaxRegimes(v || 5)}
                    data-testid="regime-max" style={{ width: 50 }} />
                </label>
                <label className="opt-field" title="Regime mit kleinerem Anteil werden zusammengelegt">
                  Min. Anteil %
                  <NumInput int min={1} max={30} value={minShare}
                    onCommit={(v) => setMinShare(v || 5)}
                    data-testid="regime-minshare" style={{ width: 50 }} />
                </label>
              </>
            )}
            <label className="opt-field" title="Umschalten nur bei dieser Sicherheit (Anti-Flattern)">
              Sicherheit %
              <NumInput int min={50} max={95} value={confMin}
                onCommit={(v) => setConfMin(v || 70)}
                data-testid="regime-confmin" style={{ width: 50 }} />
            </label>
            <label className="opt-field"
              title="Mini-Phasen-Filter: kürzere Auf-/Ab-/Seitwärts-Phasen werden mit dem längeren Nachbarn zusammengelegt – weniger Mini-Regime, handelbarere Abschnitte. 0 = automatisch (~1% des Zeitraums)">
              Min. Phasendauer (d)
              <NumInput min={0} max={60} step={0.5} value={minHold}
                onCommit={(v) => setMinHold(v || 0)}
                data-testid="regime-minhold" style={{ width: 55 }} />
            </label>
            <button className="opt-run" onClick={startAnalysis} disabled={jobBlocked} data-testid="regime-analyze-btn">
              <Play size={14} weight="fill" /> Regime suchen & speichern
            </button>
            <button className="opt-chip" onClick={queueAnalysisToSeries} data-testid="regime-add-series"
              title="Diese Analyse in die Nacht-Serie einreihen – läuft automatisch nacheinander mit den anderen eingeplanten Jobs">
              <MoonStars size={13} /> + Serie
            </button>
          </div>
          {job && (
            <JobProgress job={job} onCancel={job.status === 'running' ? cancelJob : null}
              onPause={job.status === 'running' ? jobControls.togglePause : null}
              onStop={job.status === 'running' && SOFT_STOP_KINDS.includes(job.kind) ? jobControls.softStop : null}
              onReset={job.status === 'running' ? jobControls.reset : null}
              testId="regime-job-progress"
              label={JOB_KIND_LABEL[job.kind] || 'Regime-Lab-Job'} />
          )}
          {job?.status === 'running' && job.kind === 'autopilot' && job.best && (
            <div className="opt-small" data-testid="regime-job-best">
              Autopilot – aktuell Bestes: <b>{job.best.detector}</b> · Score <b>{Number(job.best.score ?? 0).toFixed(1)}</b>
              {job.best.baseline_score != null && ` (Start ${Number(job.best.baseline_score).toFixed(1)})`} ·
              innere Val. {job.best.metrics?.inner_direction_pct ?? '–'}% · Holdout {job.best.metrics?.holdout_direction_pct ?? '–'}% ·
              Ø Phase {job.best.metrics?.avg_live_phase_days ?? '–'}d
            </div>
          )}
          <RegimeQueueStrip refreshKey={queueRefresh} />
        </div>

        <div className="opt-row" data-testid="regime-research-intro">
          <div className="opt-label">1b · DETEKTOR-FORSCHUNG – WERKZEUGE JE GRUNDGERÜST (OPTIONAL)</div>
          <div className="opt-small">
            Aktuelles Grundgerüst: <b data-testid="regime-research-detector">{DETECTOR_LABELS[engineConfig?.detector || 'reactive']}</b>
            {selected && detail && <> · Basis: <b data-testid="regime-research-base">„{detail.name}“</b> (unten ausgewählt)</>}.
            Diese Werkzeuge testen Erkennungs-Einstellungen gegeneinander und speichern KEINE Analyse.
            Maßstab ist immer <b>Live=Final</b> auf dem Holdout. Das jeweils beste Ergebnis wird
            <b> automatisch übernommen</b> und hier angezeigt – danach oben „Regime suchen &amp; speichern“ erneut starten
            und die Note der neuen Analyse mit der alten vergleichen. Es läuft immer nur EIN Regime-Lab-Job gleichzeitig –
            jedes Werkzeug lässt sich über „+ Warteschlange“ einreihen und erscheint dann im Haupt-Balken oben.
          </div>
        </div>

        <EmaPeriodCompare selCoins={selCoins} timeframe={timeframe} days={days}
          trainPct={trainPct} engineConfig={engineConfig} setEngineConfig={setEngineConfig} jobBlocked={jobBlocked}
          onQueued={() => setQueueRefresh(k => k + 1)} />

        <KombiAutoCalibrate selCoins={selCoins} timeframe={timeframe} days={days}
          trainPct={trainPct} engineConfig={engineConfig}
          setEngineConfig={setEngineConfig} jobBlocked={jobBlocked}
          onQueued={() => setQueueRefresh(k => k + 1)} />

        <AblationCompare selCoins={selCoins} timeframe={timeframe} days={days}
          trainPct={trainPct} engineConfig={engineConfig} jobBlocked={jobBlocked}
          onQueued={() => setQueueRefresh(k => k + 1)} />

        <RegimeAutopilot selCoins={selCoins} timeframe={timeframe} days={days} trainPct={trainPct}
          engineConfig={engineConfig} setEngineConfig={setEngineConfig} setCalibApplied={setCalibApplied}
          execution={execution} lwOnline={lwOnline} jobBlocked={jobBlocked}
          activeJob={job} lastResult={autopilotResult}
          onStarted={(id, kind) => attachPoll(id, kind)}
          onQueued={() => setQueueRefresh(k => k + 1)} />

        <div className="opt-row">
          <div className="opt-label" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            2 · GESPEICHERTE ANALYSEN
            <button className="opt-chip" style={{ fontSize: 10 }} onClick={loadList} data-testid="regime-list-refresh">
              <ArrowClockwise size={11} />
            </button>
          </div>
          {analyses === null && <div className="opt-small">Lade...</div>}
          {analyses !== null && analyses.length === 0 && (
            <div className="opt-small">Noch keine Analysen – oben Konfiguration wählen und "Regime suchen & speichern" starten.</div>
          )}
          {(analyses || []).map(a => (
            <div key={a.id} className={`rl-analysis-row ${selected === a.id ? 'on' : ''}`}
              onClick={() => selectAnalysis(selected === a.id ? null : a.id)}
              data-testid={`regime-analysis-row-${a.id}`}>
              {selected === a.id && (
                <span className="rl-selected-tag" data-testid={`regime-analysis-selected-${a.id}`}>Ausgewählt</span>
              )}
              <b>{a.name}</b>
              <span className="opt-small">{(a.symbols || []).map(s => s.replace('USDT', '')).join(', ')}</span>
              <span className="opt-small">{a.timeframe} · {a.days}d · Training {a.settings?.train_pct}%</span>
              {a.n_regimes_combined > 0 && <span className="opt-small">{a.n_regimes_combined} Regime (kombiniert)</span>}
              {a.n_assignments > 0 && <span className="opt-small pos">{a.n_assignments} Strategie(n) bestätigt</span>}
              <RegimeReleaseBadge analysis={a} shadowTrades={shadowTrades} />
              {a.has_walkforward
                ? (a.walkforward_stale
                  ? <span className="opt-small" style={{ color: '#FFB74D' }}
                      data-testid={`wf-status-${a.id}`}
                      title="Nach dem Walk-Forward wurden Strategie-Zuordnungen geändert/bestätigt – das gespeicherte Ergebnis beschreibt nicht mehr die aktuelle Zusammenstellung. Erneut testen.">
                      WF veraltet
                    </span>
                  : <span className={`opt-small ${a.walkforward_passed ? 'pos' : 'neg'}`}
                      data-testid={`wf-status-${a.id}`}
                      title={a.walkforward_passed
                        ? 'Finaler Walk-Forward bestanden (dynamisch schlägt Benchmark)'
                        : 'Walk-Forward vorhanden, aber NICHT bestanden'}>
                      {a.walkforward_passed ? 'WF bestanden' : 'WF nicht bestanden'}
                    </span>)
                : <span className="opt-small" style={{ opacity: 0.55 }}
                    data-testid={`wf-status-${a.id}`}>WF nicht geprüft</span>}
              {a.dataset_status === 'legacy_unpinned' && (
                <span className="opt-small" style={{ opacity: 0.55 }}
                  title="Bestandsanalyse ohne Datensatz-Manifest – das Datenfenster ist nicht fixiert, spätere Läufe sind nicht exakt reproduzierbar"
                  data-testid={`dataset-status-${a.id}`}>Daten nicht gepinnt</span>
              )}
              <span style={{ flex: 1 }} />
              <span className="opt-small">{fmtDateTime(a.created_at)}</span>
              <button className="opt-chip" onClick={(e) => removeAnalysis(a.id, e)} data-testid={`regime-analysis-delete-${a.id}`}>
                <Trash size={11} />
              </button>
            </div>
          ))}
        </div>

        <div className="opt-row">
          <div className="opt-label" data-testid="regime-step3-label">
            3 · ANALYSE{selected && detail ? `: ${detail.name}` : ''}
          </div>
          {!selected && (
            <div className="opt-small" data-testid="regime-step3-placeholder">
              Bitte wähle unter „2 · Gespeicherte Analysen“ ein Regime (Analyse) aus – hier erscheinen dann
              Freigabe, Regime-Insights, Charts und Strategie-Zuordnung.
            </div>
          )}
          {selected && !detail && (
            <div className="opt-small" data-testid="regime-step3-loading">
              Lade Regime „{(analyses || []).find(a => a.id === selected)?.name || selected}“ …
            </div>
          )}
          {selected && detail && (
            <>
              <RegimeReleaseControls analysis={detail} onChanged={() => { loadDetail(selected); loadList(); }} />
              <AnalysisDetail analysis={detail} strategies={strategies}
                jobBlocked={jobBlocked} execution={execution} onChanged={() => loadDetail(selected)} />
            </>
          )}
        </div>

        <div className="opt-row">
          <div className="opt-label">4 · DYNAMISCHE STRATEGIEN (LIVE/PAPER)</div>
          <div className="opt-small" style={{ marginBottom: 6 }}>
            Aktuelles Regime je Coin, aktive Strategie, Auto-Umschaltung inkl. optionaler
            manueller Bestätigung und Wechsel-Protokoll – alles an einem Ort.
          </div>
          <DynamicPanel />
        </div>

        <StrategyCopilot panel="regime_lab"
          onApplySettings={(s) => {
            if (!s || typeof s !== 'object') return 0;
            let n = 0;
            if (Array.isArray(s.coins) && s.coins.every(c => typeof c === 'string') && s.coins.length) {
              setSelCoins(s.coins); n++;
            }
            if (typeof s.timeframe === 'string' && s.timeframe) { setTimeframe(s.timeframe); n++; }
            const d = Number(s.days);
            if (!Number.isNaN(d) && d > 0) { setDays(d); n++; }
            if (['both', 'combined', 'per_coin'].includes(s.scope)) { setScope(s.scope); n++; }
            return n;
          }}
          getContext={() => {
            const key = detail ? Object.keys(detail.quality || {})[0] : null;
            const q = key ? detail.quality[key] : null;
            const focus = q ? (Object.values(q.classes || {}).length === 1 ? Object.values(q.classes)[0] : q.overall) : null;
            const wf = detail?.walkforward ? Object.values(detail.walkforward)[0] : null;
            const regs = detail?.combined?.model?.regimes || Object.values(detail?.per_coin || {})[0]?.model?.regimes || [];
            return {
              settings: { coins: selCoins, timeframe, days, scope, train_pct: trainPct },
              regime: {
                detector: engine === 'kmeans' ? 'kmeans' : (engineConfig?.detector || 'reactive'),
                regime_mode: engineConfig?.regime_mode || 5,
                calibration: calibApplied ? { detector: calibApplied.detector, before_pct: calibApplied.baseline_pct,
                  after_pct: calibApplied.best_pct, source: calibApplied.source } : null,
                analyses_saved: analyses?.length ?? 0,
                analysis: detail ? {
                  name: detail.name, grade: focus?.grade, holdout_live_final_pct: focus?.pct,
                  trend_hit_pct: focus?.trend_hit_pct, avg_segment_days: focus?.avg_segment_days,
                  avg_delay_days: focus?.avg_delay_days, holdout_bars: focus?.holdout_bars,
                  regimes: regs.slice(0, 9).map(r => ({ label: r.label, share_pct: Math.round(r.share_pct || 0) })),
                  assignments: Object.keys(detail.assignments || {}).length,
                  walkforward: wf ? { dynamic_pnl: wf.dynamic_test?.pnl, dynamic_trades: wf.dynamic_test?.trades,
                    benchmark_pnl: wf.best_single?.metrics?.pnl, passed: !!wf.verdict?.dynamic_better } : null,
                } : null,
              },
            };
          }} />
      </div>
    </SafeOverlay>
  );
}
