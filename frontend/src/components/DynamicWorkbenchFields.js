import React from 'react';
import { StopCircle, X, Cloud, Desktop, Gear } from '@phosphor-icons/react';
import TIMEFRAMES from '../constants/timeframes';
import { OBJECTIVES, OPT_GROUPS, DAY_OPTIONS } from '../constants/optimizerOptions';
import NumInput from './NumInput';

export const MODE_LABEL = { params: 'Trade- & Strategie-Parameter', combo: 'Neue Regeln + Parameter', discovery: 'Nur neue Regeln' };

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
  const dayOpts = DAY_OPTIONS.filter(d => !maxDays || d < maxDays);
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
      <label className="opt-field" title="Nur die letzten N Tage des Analyse-Zeitraums für die Suche nutzen (Regime-Erkennung bleibt identisch)">Zeitraum
        <select value={opts.days} onChange={e => set('days', e.target.value)} data-testid="dwb-days">
          <option value="">gesamt{maxDays ? ` (${maxDays} Tage)` : ''}</option>
          {dayOpts.map(d => <option key={d} value={d}>{`${d} Tag${d > 1 ? 'e' : ''}`}</option>)}
        </select>
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
        <label className="opt-field">Iterationen/Regime
          <NumInput int min={5} max={500} value={opts.iterations} onCommit={(v) => set('iterations', v || 40)} data-testid="dwb-iterations" />
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
        <button className={`opt-chip ${opts.result_backtest ? 'on' : ''}`} onClick={() => set('result_backtest', !opts.result_backtest)}
          data-testid="dwb-result-backtest" title="Fertige dynamische Strategie über den gesamten Zeitraum simulieren: Gesamt, je Regime, Empfehlung, Equity">
          {opts.result_backtest ? '☑' : '☐'} Ergebnis-Backtest je Regime
        </button>
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
      <label className="opt-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>Name (optional)
        <input value={name} onChange={e => setName(e.target.value)} data-testid="dwb-name" style={{ width: 200 }} placeholder="Name der neuen dynamischen Strategie" />
      </label>
    </div>
  );
}

export function JobProgress({ job, onStop, onCancel }) {
  if (!job) return null;
  const running = job.status === 'running';
  const regimes = Object.entries(job.regimes || {});
  return (
    <div className="opt-progress" data-testid="dwb-job">
      {running && <div className="opt-progress-bar"><div style={{ width: `${job.sub_progress ?? job.progress ?? 0}%` }} /></div>}
      <div className="opt-progress-row">
        <div className="opt-progress-text" data-testid="dwb-job-phase">
          {job.params?.execution === 'local' && <span className="bt-exec-tag">💻 Lokal</span>}
          <b>{running ? `Läuft · Runde ${job.round || 1}` : { done: 'Fertig', error: 'Fehler', cancelled: 'Abgebrochen' }[job.status]}</b>
          {' '}· {job.phase}{running && job.sub_phase ? ` · ${job.sub_phase}` : ''}{running ? ` · ${Number(job.sub_progress || 0).toFixed(1)}%` : ''}
        </div>
        {running && <>
          <button className="opt-cancel-run" onClick={onStop} data-testid="dwb-stop" title="Suche beenden – das Beste je Regime wird übernommen, Walk-Forward und Bau laufen noch"><StopCircle size={13} weight="bold" /> Suche beenden &amp; Bestes übernehmen</button>
          <button className="opt-cancel-run" onClick={onCancel} data-testid="dwb-cancel"><X size={13} weight="bold" /> Abbrechen</button>
        </>}
      </div>
      {running && regimes.length > 0 && (
        <div className="opt-best-live" style={{ flexWrap: 'wrap' }} data-testid="dwb-job-regimes">
          Bester Stand je Regime: {regimes.map(([rid, r]) => (
            <span key={rid} className="opt-param-pill">
              {r.label}: {r.note || <>Score <b>{Number(r.score || 0).toFixed(1)}</b> · PnL <b className={(r.pnl || 0) >= 0 ? 'pos' : 'neg'}>{r.pnl}</b> ({r.trades} T.){r.validation_passed ? ' ✓' : ''}</>}
            </span>
          ))}
        </div>
      )}
      {job.status === 'error' && <div className="dyn-verdict warn" data-testid="dwb-job-error"><b>Fehler</b><div>{job.error}</div></div>}
    </div>
  );
}
