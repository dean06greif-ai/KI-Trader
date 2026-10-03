import React from 'react';
import { Cloud, Desktop, Gear } from '@phosphor-icons/react';
import { JobStateTags, JobControls } from './RegimeJobProgress';
import TIMEFRAMES from '../constants/timeframes';
import { OBJECTIVES, OPT_GROUPS, DAY_OPTIONS } from '../constants/optimizerOptions';
import NumInput from './NumInput';
import WorkerTargetSelect from './WorkerTargetSelect';
import { maxIterations, iterationsHint } from '../constants/searchLimits';

export const MODE_LABEL = { params: 'Trade- & Strategie-Parameter', combo: 'Neue Regeln + Parameter', discovery: 'Nur neue Regeln' };

export const LABEL_BASIS_HINT = 'Auf welchen Regime-Abschnitten die Strategie je Regime gesucht wird. '
  + 'Live-Sicht (empfohlen): genau die Abschnitte, in denen das Regime auch im Handel erkannt wird (inkl. Erkennungs-Verzögerung) – '
  + 'identisch zu Backtest, Walk-Forward und Live. Rückblick: ideale Phasen ab dem bestätigten Hoch/Tief – '
  + 'schönt vor allem Trend-Regime, weil dieser Einstieg live nie möglich ist.';

/** Segment-Basis der Regime-Suche (live = kausal wie im Handel, final = Rückblick). */
export function LabelBasisSelect({ value, onChange, testId }) {
  return (
    <select value={value || 'live'} onChange={e => onChange(e.target.value)} data-testid={testId}>
      <option value="live">Live-Sicht (wie im Handel, empfohlen)</option>
      <option value="final">Rückblick (ideale Phasen)</option>
    </select>
  );
}

export function RegimePicker({ regimes, selected, onToggle, onAll, testPrefix }) {
  return (
    <div className="opt-chips" data-testid={`${testPrefix}-regime-picker`}>
      {regimes.map(r => (
        <button key={r.id} className={`opt-chip ${selected.includes(r.id) ? 'on' : ''}`}
          onClick={() => onToggle(r.id)} data-testid={`${testPrefix}-regime-${r.id}`}
          title={r.strategy_name ? `Aktuell: ${r.strategy_name}` : 'noch keine Strategie zugeordnet'}>
          {selected.includes(r.id) ? '☑' : '☐'} {r.label || `#${r.id + 1}`}{r.strategy_name ? ` · ${r.strategy_name}` : ''}
        </button>
      ))}
      <button className="opt-chip" onClick={onAll} data-testid={`${testPrefix}-regime-all`}>alle</button>
    </div>
  );
}

// Asset-Auswahl der gemeinsamen Erkennung: Häkchen = Asset fließt in Suche,
// Walk-Forward und Ergebnis-Backtest ein (mind. 1 bleibt aktiv).
export function AssetToggle({ symbols, selected, onChange, disabled }) {
  const toggle = (s) => {
    const next = selected.includes(s) ? selected.filter(x => x !== s) : [...selected, s];
    if (next.length) onChange(symbols.filter(x => next.includes(x)));
  };
  return (
    <div className="opt-chips" data-testid="dwb-assets">
      {symbols.map(s => (
        <button key={s} type="button" disabled={disabled} className={`opt-chip ${selected.includes(s) ? 'on' : ''}`}
          onClick={() => toggle(s)} data-testid={`dwb-asset-${s}`}
          title={selected.length === 1 && selected.includes(s) ? 'Mindestens 1 Asset bleibt aktiv' : ''}>
          {s.replace('USDT', '')}
        </button>
      ))}
      <button type="button" className="opt-chip" disabled={disabled || selected.length === symbols.length}
        onClick={() => onChange([...symbols])} data-testid="dwb-asset-all">alle</button>
      <span className="opt-small" data-testid="dwb-assets-count">{selected.length}/{symbols.length} aktiv</span>
    </div>
  );
}

const TF_MIN = { m: 1, h: 60, d: 1440 };
const tfMinutes = (tf) => { const m = /^(\d+)([mhd])$/.exec(tf || ''); return m ? Number(m[1]) * TF_MIN[m[2]] : 60; };

// Grobe Plausibilität von „Min. Trades“ (gilt JE Regime): Ø Trainings-Kerzen
// je Regime auf der Auswahl vs. geforderte Trades.
export function MinTradesHint({ minTrades, nAssets, nRegimes, timeframe, days, trainPct }) {
  if (!minTrades || !nAssets || !nRegimes || !days) return null;
  const bars = Math.round(days * 1440 / tfMinutes(timeframe) * nAssets / nRegimes * (trainPct || 100) / 100);
  const every = bars / minTrades;
  if (every >= 15) return null;
  return (
    <div className="dyn-verdict warn" style={{ marginTop: 6 }} data-testid="dwb-min-trades-hint">
      <b>Min. Trades {minTrades} ist für diese Auswahl sehr hoch</b>
      <div>Der Wert gilt je Regime. Mit {nAssets} Asset{nAssets === 1 ? '' : 's'} hat ein Regime im Schnitt nur ca. {bars.toLocaleString('de-DE')} Trainings-Kerzen ({timeframe}) –
        das verlangt einen Trade alle ~{Math.max(every, 0.1).toFixed(1)} Kerzen. Viele Regime erreichen das nicht und die Suche liefert dann nichts Brauchbares.
        Richtwert: höchstens ca. {Math.max(Math.floor(bars / 30), 1).toLocaleString('de-DE')}.</div>
    </div>
  );
}

export function StrategyMapping({ regimes, strategies, mapping, setMapping }) {
  return (
    <div className="opt-setup" data-testid="dwb-mapping" style={{ marginBottom: 0 }}>
      {regimes.map(r => (
        <label key={r.id} className="opt-field">{r.label || `Regime #${r.id + 1}`}
          <select value={mapping[r.id] || ''} data-testid={`dwb-map-${r.id}`}
            onChange={e => setMapping({ ...mapping, [r.id]: e.target.value })}>
            <option value="">– nicht handeln –</option>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
      ))}
    </div>
  );
}

/** Einstellungen wie im klassischen Optimizer (Timeframe, Zeitraum, Kapital, Hebel …). */
export function CoreSettings({ opts, set, tab, mode, source }) {
  const maxDays = source?.days || 0;
  const live = (opts.label_basis || 'live') === 'live';
  const dayOpts = DAY_OPTIONS.filter(d => !maxDays || d < maxDays || live);
  const beyond = maxDays && Number(opts.days) > maxDays;
  return (
    <div className="opt-setup">
      {tab !== 'create' && (
        <label className="opt-field">Was optimieren
          <select value={mode} onChange={e => set('mode', e.target.value)} data-testid="dwb-mode">
            {(tab === 'discover' ? ['discovery', 'combo'] : ['params', 'combo', 'discovery']).map(m => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
          </select>
        </label>
      )}
      <label className="opt-field">Timeframe
        <select value={opts.timeframe} onChange={e => set('timeframe', e.target.value)} data-testid="dwb-timeframe">
          <option value="">wie Analyse{source?.timeframe ? ` (${source.timeframe})` : ''}</option>
          {TIMEFRAMES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
        </select>
      </label>
      <label className="opt-field" title={`Zeitraum für Suche und Ergebnis-Backtest. Innerhalb der Analyse: die letzten N Tage. Länger als die Analyse (${maxDays || '?'} Tage): nur mit Live-Sicht – die früheren Daten werden wie im Handel live mit der Erkennung eingeteilt (Holdout bleibt unberührt). Rückblick (ideale Phasen) gibt es nur im Analyse-Zeitraum.`}>Zeitraum
        <select value={opts.days} onChange={e => set('days', e.target.value)} data-testid="dwb-days">
          <option value="">gesamt{maxDays ? ` (${maxDays} Tage)` : ''}</option>
          {dayOpts.map(d => <option key={d} value={d}>{`${d} Tag${d > 1 ? 'e' : ''}`}{maxDays && d > maxDays ? ' · über Analyse hinaus (Live-Sicht)' : ''}</option>)}
        </select>
        {beyond && !live && (
          <span className="neg opt-small" data-testid="dwb-days-retro-locked">Rückblick gesperrt – nur im Analyse-Zeitraum ({maxDays} Tage). Live-Sicht wählen oder Zeitraum verkürzen.</span>
        )}
        {beyond && live && (
          <span className="opt-small" data-testid="dwb-days-extended">+{Number(opts.days) - maxDays} Tage vor der Analyse, live eingeteilt</span>
        )}
      </label>
      <label className="opt-field">Zeitfenster (optional)
        <input type="text" placeholder="z.B. 15:00-18:00 · leer = 24h" value={opts.sessions}
          onChange={e => set('sessions', e.target.value)} data-testid="dwb-sessions" style={{ width: 170 }}
          title="Festes Handels-Zeitfenster (Berlin-Zeit), z.B. 15:00-18:00 oder 09:00-12:00,15:00-18:00" />
      </label>
      <label className="opt-field">Ziel
        <select value={opts.objective} onChange={e => set('objective', e.target.value)} data-testid="dwb-objective">
          {OBJECTIVES.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
        </select>
      </label>
      <label className="opt-field">Min. Trades
        <NumInput int min={1} value={opts.min_trades} onCommit={(v) => set('min_trades', v || 1)} data-testid="dwb-min-trades" />
      </label>
      {tab !== 'create' && <>
        <label className="opt-field" title={iterationsHint(opts.execution)}>Iterationen/Regime
          <NumInput int min={5} max={maxIterations(opts.execution)} value={opts.iterations}
            onCommit={(v) => set('iterations', Math.min(v || 40, maxIterations(opts.execution)))} data-testid="dwb-iterations" />
          {Number(opts.iterations) > maxIterations(opts.execution) && (
            <span className="neg opt-small" data-testid="dwb-iterations-capped">wird auf {maxIterations(opts.execution)} begrenzt</span>
          )}
        </label>
        {mode !== 'params' && (
          <label className="opt-field">Max. Regeln
            <NumInput int min={1} max={8} value={opts.max_rules} onCommit={(v) => set('max_rules', v || 4)} data-testid="dwb-max-rules" />
          </label>
        )}
        <label className="opt-field">Runden
          <NumInput int min={1} max={50} disabled={opts.endless} value={opts.rounds} onCommit={(v) => set('rounds', v || 1)} data-testid="dwb-rounds" />
        </label>
      </>}
      <label className="opt-field">Kapital (USDT)
        <NumInput min={1} value={opts.max_capital} onCommit={(v) => set('max_capital', v || 100)} data-testid="dwb-capital" />
      </label>
      <label className="opt-field">Hebel
        <NumInput int min={1} max={125} value={opts.leverage} onCommit={(v) => set('leverage', v || 10)} data-testid="dwb-leverage" />
      </label>
      <label className="opt-field">Gebühren (%)
        <NumInput min={0} max={1} step={0.01} value={opts.fee_percent} onCommit={(v) => set('fee_percent', v ?? 0.06)} data-testid="dwb-fee" />
      </label>
    </div>
  );
}

export function OptGroups({ flags, toggle }) {
  return (
    <div className="opt-row">
      <div className="opt-label">WAS SOLL MITOPTIMIERT WERDEN? (je Regime eine eigene Konfiguration)</div>
      <div className="opt-chips">
        {OPT_GROUPS.map(g => (
          <button key={g.k} className={`opt-chip ${flags[g.k] ? 'on' : ''}`}
            onClick={() => toggle(g.k)} title={g.d} data-testid={`dwb-flag-${g.k}`}>
            {flags[g.k] ? '☑' : '☐'} {g.l}
          </button>
        ))}
      </div>
    </div>
  );
}

export function RobustnessRow({ opts, set, tab, holdout }) {
  return (
    <div className="opt-row" data-testid="dwb-robustness">
      <div className="opt-label">ROBUSTHEIT &amp; WALK-FORWARD</div>
      <div className="opt-chips" style={{ alignItems: 'center' }}>
        {tab !== 'create' && (
          <button className={`opt-chip ${opts.regime_walk_forward ? 'on' : ''}`} onClick={() => set('regime_walk_forward', !opts.regime_walk_forward)}
            data-testid="dwb-regime-wf" title="Jede Regime-Suche prüft ihren Kandidaten auf einem Validierungs-Anteil der eigenen Regime-Abschnitte (Overfitting-Schutz je Marktphase)">
            {opts.regime_walk_forward ? '☑' : '☐'} Walk-Forward je Regime
          </button>
        )}
        {tab !== 'create' && opts.regime_walk_forward && (
          <label className="opt-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>Training (%)
            <NumInput int min={40} max={95} value={opts.regime_train_pct} onCommit={(v) => set('regime_train_pct', v || 75)} data-testid="dwb-regime-train-pct" style={{ width: 55 }} />
          </label>
        )}
        <button className={`opt-chip ${opts.walkforward ? 'on' : ''}`} onClick={() => set('walkforward', !opts.walkforward)}
          data-testid="dwb-walkforward" title="Am Ende auf dem unangetasteten Holdout der Analyse prüfen – nur dann gilt die neue Strategie als validiert">
          {opts.walkforward ? '☑' : '☐'} Finaler Walk-Forward (Holdout){holdout === false ? ' · Analyse ohne Holdout' : ''}
        </button>
        {opts.walkforward && (
          <button className={`opt-chip ${opts.skip_losing ? 'on' : ''}`} onClick={() => set('skip_losing', !opts.skip_losing)}
            data-testid="dwb-skip-losing" title="Regime mit negativem Walk-Forward (ab 5 Trades) werden in der gebauten Strategie auf „nicht handeln“ gestellt">
            {opts.skip_losing ? '☑' : '☐'} Verlust-Regime abschalten
          </button>
        )}
        <button className={`opt-chip ${opts.result_backtest ? 'on' : ''}`} onClick={() => set('result_backtest', !opts.result_backtest)}
          data-testid="dwb-result-backtest" title="Fertige dynamische Strategie über den gesamten Zeitraum simulieren: Gesamt, je Regime, Empfehlung, Equity">
          {opts.result_backtest ? '☑' : '☐'} Ergebnis-Backtest je Regime
        </button>
        {tab !== 'create' && (
          <label className="opt-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }} title={LABEL_BASIS_HINT}>
            Regime-Abschnitte
            <LabelBasisSelect value={opts.label_basis} onChange={v => set('label_basis', v)} testId="dwb-label-basis" />
          </label>
        )}
        {tab !== 'create' && (
          <label className="opt-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }} title="Aufwärts-Regime nur Longs, Abwärts-Regime nur Shorts (auto) – oder fest vorgeben">
            Richtungs-Bias
            <select value={opts.direction_bias} onChange={e => set('direction_bias', e.target.value)} data-testid="dwb-direction-bias">
              <option value="off">aus (beide Seiten)</option><option value="auto">auto (aus Regime-Richtung)</option>
              <option value="long">nur Long</option><option value="short">nur Short</option>
            </select>
          </label>
        )}
      </div>
    </div>
  );
}

const CHECK = { flexDirection: 'row', alignItems: 'center', gap: 6 };

/** Optionale Zusatz-Tests wie im klassischen Optimizer. Drawdown-Filter wirkt
 *  schon in der Suche je Regime; Monte-Carlo, Kosten-Stress und Konstanz prüfen die
 *  fertige dynamische Strategie auf dem Ergebnis-Backtest (ohne neues Kerzen-Laden). */
export function ExtraChecksRow({ opts, set, tab }) {
  const chip = (k, label, title) => (
    <button className={`opt-chip ${opts[k] ? 'on' : ''}`} onClick={() => set(k, !opts[k])} data-testid={`dwb-${k.replace(/_/g, '-')}`} title={title}>
      {opts[k] ? '☑' : '☐'} {label}
    </button>
  );
  const num = (k, label, props, testId) => (
    <label className="opt-field" style={CHECK}>{label}
      <NumInput {...props} value={opts[k]} onCommit={(v) => set(k, v || props.min)} data-testid={testId} style={{ width: 60 }} />
    </label>
  );
  const needsBt = !opts.result_backtest && (opts.mc_enabled || opts.st_enabled || opts.ct_enabled);
  return (
    <div className="opt-row" data-testid="dwb-extra-checks">
      <div className="opt-label">ZUSATZ-TESTS (optional · wie Discovery/Optimizer)</div>
      <div className="opt-chips" style={{ alignItems: 'center' }}>
        {chip('dd_enabled', tab === 'create' ? 'Drawdown-Filter (Ergebnis)' : 'Drawdown-Filter (je Regime + Ergebnis)',
          'Max. Drawdown in % vom Gewinn. In der Suche werden Kandidaten, die das überschreiten, nachrangig behandelt; am Ende wird die fertige Strategie geprüft.')}
        {opts.dd_enabled && num('dd_max_pct', 'max DD %', { int: true, min: 1, max: 1000 }, 'dwb-dd-max')}
        {chip('mc_enabled', 'Monte-Carlo', 'Trade-Reihenfolge der fertigen Strategie mehrfach mischen → realistische Drawdown-Spanne (p95) statt Einzelwert')}
        {opts.mc_enabled && num('mc_runs', 'Läufe', { int: true, min: 50, max: 2000 }, 'dwb-mc-runs')}
        {opts.mc_enabled && num('mc_max_dd_pct', 'max DD p95 %', { int: true, min: 10, max: 1000 }, 'dwb-mc-max')}
        {chip('st_enabled', 'Kosten-Stresstest', 'Bleibt die fertige Strategie profitabel, wenn Gebühren/Slippage höher ausfallen?')}
        {opts.st_enabled && num('st_mult', 'Kosten ×', { min: 1.1, max: 5, step: 0.1 }, 'dwb-st-mult')}
        {chip('ct_enabled', 'Konstanz-Test', 'Gewinn gleichmäßig über die Zeit verteilt oder nur aus wenigen Phasen?')}
        {opts.ct_enabled && num('ct_chunk_days', 'Abschnitt (Tage)', { int: true, min: 2, max: 365 }, 'dwb-ct-days')}
        {opts.ct_enabled && num('ct_max_dev_pct', 'max Streuung %', { int: true, min: 1, max: 1000 }, 'dwb-ct-max')}
      </div>
      {needsBt && <div className="opt-small neg" data-testid="dwb-extra-needs-bt">Monte-Carlo, Stress und Konstanz brauchen den „Ergebnis-Backtest je Regime“.</div>}
    </div>
  );
}

/** Body-Felder der Zusatz-Tests (Format von services/robustness.parse_config). */
export function extraChecksBody(opts) {
  const dd = opts.dd_enabled ? { enabled: true, max_dd_pct: Number(opts.dd_max_pct) } : undefined;
  const robustness = {
    dd_filter: dd,
    monte_carlo: opts.mc_enabled ? { enabled: true, runs: Number(opts.mc_runs), max_dd_p95_pct: Number(opts.mc_max_dd_pct) } : undefined,
    stress_test: opts.st_enabled ? { enabled: true, cost_multiplier: Number(opts.st_mult) } : undefined,
    constancy: opts.ct_enabled ? { enabled: true, chunk_days: Number(opts.ct_chunk_days), max_deviation_pct: Number(opts.ct_max_dev_pct) } : undefined,
  };
  const any = Object.values(robustness).some(Boolean);
  return { dd_filter: dd, robustness: any ? robustness : undefined };
}

export function ExecutionRow({ execution, setExecution, lwOnline, onManage, name, setName }) {
  return (
    <div className="opt-exec-row" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', margin: '10px 0 4px' }}>
      <div className="bt-exec" data-testid="dwb-execution-toggle">
        <span className="bt-exec-label">Ausführung</span>
        <button className={`bt-exec-btn ${execution === 'cloud' ? 'on' : ''}`} onClick={() => setExecution('cloud')} data-testid="dwb-exec-cloud">
          <Cloud size={13} weight="bold" /> Cloud
        </button>
        <button className={`bt-exec-btn ${execution === 'local' ? 'on' : ''}`} onClick={() => setExecution('local')} data-testid="dwb-exec-local">
          <Desktop size={13} weight="bold" /> Lokal <span className={`bt-exec-dot ${lwOnline ? 'on' : ''}`} />
        </button>
        {onManage && (
          <button className="bt-exec-manage" onClick={onManage} title="Lokale Ausführung verwalten" data-testid="dwb-exec-manage">
            <Gear size={13} weight="bold" />
          </button>
        )}
      </div>
      <WorkerTargetSelect area="workbench" execution={execution} testPrefix="dwb" />
      <label className="opt-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>Name (optional)
        <input value={name} onChange={e => setName(e.target.value)} data-testid="dwb-name" style={{ width: 200 }} placeholder="Name der neuen dynamischen Strategie" />
      </label>
    </div>
  );
}

/** Werkbank-Balken – Steuerung identisch zur Strategie-Discovery (Pausieren/
 * Fortsetzen, Suche beenden & Beste behalten, Abbrechen, Notfall-Reset). */
export function JobProgress({ job, onStop, onCancel, onPause, onReset }) {
  if (!job) return null;
  const running = job.status === 'running';
  const regimes = Object.entries(job.regimes || {});
  const pct = Number(job.sub_progress ?? job.progress ?? 0);
  return (
    <div className="opt-progress" data-testid="dwb-job">
      {running && <div className="opt-progress-bar"><div style={{ width: `${pct}%` }} /></div>}
      <div className="opt-progress-row">
        <div className="opt-progress-text" data-testid="dwb-job-phase">
          {job.params?.execution === 'local' && <span className="bt-exec-tag">💻 Lokal</span>}
          <b>{running ? `Läuft · Runde ${job.round || 1}` : { done: 'Fertig', error: 'Fehler', cancelled: 'Abgebrochen' }[job.status]}</b>
          {' '}· {job.phase}{running && job.sub_phase ? ` · ${job.sub_phase}` : ''}{running ? ` · ${pct.toFixed(1)}%` : ''}
          {running && <JobStateTags job={job} testId="dwb" />}
        </div>
        <JobControls job={job} onPause={onPause} onStop={onStop} onCancel={onCancel} onReset={onReset} testId="dwb" />
      </div>
      {running && regimes.length > 0 && (
        <div className="opt-best-live" style={{ flexWrap: 'wrap' }} data-testid="dwb-job-regimes">
          Bester Stand je Regime: {regimes.map(([rid, r]) => (
            <span key={rid} className="opt-param-pill">
              {r.label}: {r.note || <>Score <b>{Number(r.score || 0).toFixed(1)}</b> · PnL <b className={(r.pnl || 0) >= 0 ? 'pos' : 'neg'}>{r.pnl}</b> ({r.trades} T.){r.validation_passed ? ' ✓' : ''}</>}{r.state ? ` · ${r.state}` : ''}
            </span>
          ))}
        </div>
      )}
      {job.status === 'error' && <div className="dyn-verdict warn" data-testid="dwb-job-error"><b>Fehler</b><div>{job.error}</div></div>}
    </div>
  );
}
