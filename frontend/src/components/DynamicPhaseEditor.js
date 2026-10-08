import React, { useCallback, useEffect, useState } from 'react';
import { toast } from '../lib/toast';
import { isAdmin } from '../auth';
import { postJson } from '../lib/postJson';
import DynamicVersionHistory from './DynamicVersionHistory';
import DynamicPhaseVariants, { ParamPills } from './DynamicPhaseVariants';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const RELEASE_LABEL = { validated: 'validiert', approved: 'freigegeben', draft: 'Entwurf', stale: 'geändert – neu prüfen', legacy: 'Altbestand' };
const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

/** Text der geplanten Änderung (für Bestätigung und Auswahl). */
function describe(choice, phase, strategies) {
  if (choice === 'skip') return `Phase „${phase.label}“ wird NICHT mehr gehandelt.`;
  if (choice === 'optimized') return `Phase „${phase.label}“ handelt wieder die optimierte Strategie (${phase.optimized?.strategy_name}).`;
  const s = strategies.find(x => `strategy:${x.id}` === choice);
  return `Phase „${phase.label}“ handelt künftig mit „${s?.name || choice}“ als Ausgangs-Strategie.`;
}

const OPT_HINT = 'Optimierte Strategie = das beste gespeicherte Ergebnis der Strategie-Suche für genau diese Phase '
  + '(„Strategie suchen“ im Regime-Lab bzw. Optimierungs-Läufe der Dynamik-Werkbank) inkl. Regeln und Parametern. '
  + '„Optimierte Strategie aktivieren“ setzt die Phase wieder auf dieses Ergebnis zurück – z.B. nachdem du sie testweise auf eine Standard-Strategie umgestellt hast.';

function OptimizedCell({ o }) {
  if (!o) return <span className="opt-small">keine gespeichert</span>;
  const m = o.metrics || {};
  return (
    <span>
      <b>{o.strategy_name}</b>
      <span className="opt-small"> · Score {fmt(o.score)} · PnL <b className={(m.pnl || 0) >= 0 ? 'pos' : 'neg'}>{fmt(m.pnl, 2)}</b>
        {m.trades !== undefined ? ` · ${m.trades} T.` : ''}{o.validation_passed ? ' · Val. ✓' : ''}</span>
    </span>
  );
}

function PhaseConfirm({ phase, choice, strategies, releaseStatus, onDone, onCancel }) {
  const released = ['validated', 'approved'].includes(releaseStatus);
  const [keepRelease, setKeepRelease] = useState(released);
  const [resetTp, setResetTp] = useState(false);
  const [busy, setBusy] = useState(false);
  const isStrategy = choice.startsWith('strategy:');
  const confirm = async () => {
    setBusy(true);
    try {
      const d = await postJson(`/api/dynamic/${phase.dynamicId}/phase`, {
        regime_id: phase.regime, confirm: true, keep_release: keepRelease, reset_trade_params: resetTp,
        action: isStrategy ? 'strategy' : choice, strategy_id: isStrategy ? choice.slice(9) : undefined,
      });
      toast.success(`Version ${d.version} gespeichert – ${d.reason}`);
      onDone();
    } catch (e) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="dyn-verdict warn dpe-confirm" data-testid={`dpe-confirm-${phase.regime}`}>
      <b>Bestätigen?</b> {describe(choice, phase, strategies)}
      <div className="opt-small">Es entsteht eine neue Version – die bisherige bleibt im Verlauf und kann jederzeit wiederhergestellt werden.</div>
      {isStrategy && (
        <label className="opt-check"><input type="checkbox" checked={resetTp} onChange={e => setResetTp(e.target.checked)} data-testid="dpe-reset-tp" />
          {' '}Trade-Parameter (SL/TP) dieser Phase zurücksetzen – sonst bleiben die optimierten Werte</label>
      )}
      {released && (
        <label className="opt-check"><input type="checkbox" checked={keepRelease} onChange={e => setKeepRelease(e.target.checked)} data-testid="dpe-keep-release" />
          {' '}Freigabe beibehalten (wird als Freigabe ohne neuen Test protokolliert) – sonst Entwurf: live keine Signale bis Walk-Forward/Freigabe</label>
      )}
      <div className="dpe-actions">
        <button className="opt-cancel-run" onClick={confirm} disabled={busy} data-testid={`dpe-confirm-yes-${phase.regime}`}>{busy ? 'Speichert…' : 'Bestätigen & neue Version speichern'}</button>
        <button className="opt-cancel-run" onClick={onCancel} disabled={busy} data-testid={`dpe-confirm-no-${phase.regime}`}>Abbrechen</button>
      </div>
    </div>
  );
}

/** Optimierungs-Auswahl & Schnellaktionen einer Phase (gleiche Auswahl wie die Regime-Häkchen unten). */
const SEARCH_MODES = [['params', 'Parameter optimieren'], ['combo', 'Neue Regeln + Parameter'], ['discovery', 'Komplett neue Strategie suchen']];

/** Such-Modus dieser Phase für den nächsten Start (leer = Standard von unten). */
function PhaseModeSelect({ phase, value, globalMode, onSetMode, disabled }) {
  const std = SEARCH_MODES.find(([k]) => k === globalMode)?.[1] || globalMode;
  return (
    <select value={value || ''} disabled={disabled} onChange={e => onSetMode(phase.regime, e.target.value)}
      data-testid={`dpe-mode-${phase.regime}`}
      title="Wie diese Phase beim nächsten Start gesucht wird. 'Komplett neue Strategie suchen' = Discovery nur für diese Phase – übernommen wird sie nur, wenn sie die aktuelle Zuordnung robust schlägt.">
      <option value="">Suche: Standard ({std})</option>
      {SEARCH_MODES.map(([k, l]) => <option key={k} value={k}>Suche: {l}</option>)}
    </select>
  );
}

function PhaseFocus({ phase, optimizing, onToggleOptimize, onFocus, showHistory, setShowHistory, disabled, modeValue, globalMode, onSetMode }) {
  return (
    <div className="dpe-cell dpe-focus">
      {onToggleOptimize && (
        <label className="opt-check" title="Häkchen = diese Phase wird beim nächsten Start weiter optimiert. Ohne Häkchen bleibt sie unverändert.">
          <input type="checkbox" checked={optimizing} disabled={disabled} onChange={() => onToggleOptimize(phase.regime)} data-testid={`dpe-opt-${phase.regime}`} />
          {' '}{optimizing ? 'wird weiter optimiert' : 'bleibt so (nicht optimieren)'}
        </label>
      )}
      {onSetMode && optimizing && <PhaseModeSelect phase={phase} value={modeValue} globalMode={globalMode} onSetMode={onSetMode} disabled={disabled} />}
      {onFocus && <button className="opt-chip" disabled={disabled} onClick={() => onFocus(phase, 'params')} data-testid={`dpe-only-${phase.regime}`}
        title="Nur diese Phase markieren und Parameter weiter optimieren">Nur diese Phase optimieren</button>}
      {onFocus && <button className="opt-chip" disabled={disabled} onClick={() => onFocus(phase, 'discovery')} data-testid={`dpe-newrules-${phase.regime}`}
        title="Nur diese Phase markieren und komplett neue Regeln suchen (wie Discovery)">Neue Regeln für diese Phase</button>}
      <button className="opt-chip" onClick={() => setShowHistory(h => !h)} data-testid={`dpe-history-${phase.regime}`}>
        {showHistory ? 'Verlauf zu' : 'Verlauf der Phase'}
      </button>
    </div>
  );
}

function CurrentVariant({ phase }) {
  if (!phase.traded) return <span className="neg">nicht handeln<span className="opt-small"> · {phase.skip_reason}</span></span>;
  const m = phase.metrics || {};
  return (
    <>
      <b>{phase.strategy_name}</b>{phase.own_rules && phase.rules?.length ? <span className="opt-small"> · {phase.rules.length} Regeln</span> : null}
      <div><ParamPills trade={phase.trade_params} strategy={phase.strategy_params} max={4} /></div>
      {phase.metrics && <div className="opt-small">Score {fmt(phase.score)} · PnL {fmt(m.pnl, 2)}{m.trades != null ? ` · ${m.trades} Trades` : ''}</div>}
    </>
  );
}

function PhaseRow({ phase, strategies, releaseStatus, disabled, onChanged, refreshKey, optimizing, onToggleOptimize, onFocus, modeValue, globalMode, onSetMode }) {
  const [choice, setChoice] = useState('');
  const pick = (v) => {
    if (v === 'search:discovery') {
      // Kein sofortiges Speichern: Phase für den nächsten Start auf Discovery stellen
      onSetMode(phase.regime, 'discovery');
      setChoice('');
      toast.info(`„${phase.label}“: beim nächsten Start wird eine komplett neue Strategie gesucht (übrige Phasen wie eingestellt). Unten „Optimierung starten“.`);
      return;
    }
    setChoice(v);
  };
  const [asking, setAsking] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  return (
    <div className={`dpe-row ${optimizing ? 'optimizing' : ''}`} data-testid={`dpe-row-${phase.regime}`}>
      <div className="dpe-cell dpe-label"><b>{phase.label}</b></div>
      <div className="dpe-cell" data-testid={`dpe-current-${phase.regime}`}><span className="opt-small">Aktuell: </span><CurrentVariant phase={phase} /></div>
      <div className="dpe-cell" data-testid={`dpe-optimized-${phase.regime}`} title={OPT_HINT}><span className="opt-small">Optimiert ⓘ: </span><OptimizedCell o={phase.optimized} /></div>
      <div className="dpe-cell dpe-act">
        <select value={choice} disabled={disabled || asking} onChange={e => pick(e.target.value)} data-testid={`dpe-select-${phase.regime}`}
          title="Strategie dieser Phase ändern: nicht handeln, optimierte Strategie oder eine Standard-Strategie als Ausgangs-Strategie">
          <option value="">– Strategie ändern –</option>
          {onSetMode && <option value="search:discovery">Komplett neue Strategie für diese Phase suchen…</option>}
          {phase.traded && <option value="skip">Nicht handeln</option>}
          {phase.optimized && <option value="optimized" title={OPT_HINT}>Optimierte Strategie aktivieren</option>}
          {strategies.filter(s => s.id !== phase.strategy_id || phase.own_rules).map(s => (
            <option key={s.id} value={`strategy:${s.id}`}>Ausgangs-Strategie: {s.name}</option>
          ))}
        </select>
        {choice && !asking && (
          <button className="opt-cancel-run" disabled={disabled} onClick={() => setAsking(true)} data-testid={`dpe-apply-${phase.regime}`}>Übernehmen…</button>
        )}
      </div>
      <PhaseFocus phase={phase} optimizing={optimizing} onToggleOptimize={onToggleOptimize} onFocus={onFocus}
        showHistory={showHistory} setShowHistory={setShowHistory} disabled={disabled}
        modeValue={modeValue} globalMode={globalMode} onSetMode={onSetMode} />
      {asking && <PhaseConfirm phase={phase} choice={choice} strategies={strategies} releaseStatus={releaseStatus}
        onCancel={() => setAsking(false)} onDone={() => { setAsking(false); setChoice(''); onChanged(); }} />}
      {showHistory && <DynamicPhaseVariants phase={phase} refreshKey={refreshKey} disabled={disabled} onChanged={onChanged} />}
    </div>
  );
}

/** „Bestehende optimieren“: Strategie je Phase anzeigen, nach Bestätigung
 *  abschalten / tauschen, mit Versionsverlauf zum Zurückholen. */
export default function DynamicPhaseEditor({ dynamicId, strategies, disabled, optimizeSel, onToggleOptimize, onFocus, phaseModes, globalMode, onSetMode }) {
  const [data, setData] = useState(null);
  const [rev, setRev] = useState(0);
  const [showPhases, setShowPhases] = useState(false);
  const load = useCallback(() => {
    fetch(`${API_URL}/api/dynamic/${dynamicId}/phases`).then(r => r.json()).then(setData).catch(() => setData(null));
  }, [dynamicId]);
  useEffect(() => { load(); }, [load, rev]);
  if (!data?.phases) return null;
  const changed = () => setRev(v => v + 1);
  return (
    <div className="opt-row dpe" data-testid="dpe-panel">
      <div className="opt-label">STRATEGIE JE PHASE (aktuell gehandelt · darauf optimiert · ändern mit Bestätigung)</div>
      <div className="opt-small" style={{ marginBottom: 6 }} data-testid="dpe-release">
        Status: <b>{RELEASE_LABEL[data.release_status] || data.release_status}</b>{data.traded ? ' · wird live/paper gehandelt – Änderungen wirken sofort' : ''}
        {data.block_reason ? <span className="neg"> · {data.block_reason}</span> : null}
        {data.refined_from_name ? <span> · optimiert aus „{data.refined_from_name}“</span> : null}
      </div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        Pro Phase: Häkchen = wird beim nächsten Start weiter optimiert. Phasen ohne Häkchen bleiben genau so, wie sie sind.
        Deine Anpassungen bleiben auch in der neuen optimierten Version erhalten, solange dort nichts verbessert wurde.
      </div>
      {!isAdmin() && <div className="opt-small">Admin-Login erforderlich, um Phasen zu ändern.</div>}
      <button className="opt-chip" onClick={() => setShowPhases(v => !v)} data-testid="dpe-toggle-phases"
        style={{ marginBottom: 6 }} title="Je Phase: aktuell gehandelte Strategie, optimierte Strategie, Strategie ändern und Verlauf">
        {showPhases ? '▾ Phasen-Parameter ausblenden' : `▸ Phasen-Parameter anzeigen (${data.phases.length} Phasen)`}
      </button>
      {showPhases && data.phases.map(p => (
        <PhaseRow key={p.regime} phase={{ ...p, dynamicId }} strategies={strategies} releaseStatus={data.release_status}
          disabled={disabled || !isAdmin()} onChanged={changed} refreshKey={rev}
          optimizing={(optimizeSel || []).includes(p.regime)} onToggleOptimize={onToggleOptimize} onFocus={onFocus}
          modeValue={(phaseModes || {})[p.regime]} globalMode={globalMode} onSetMode={onSetMode} />
      ))}
      <DynamicVersionHistory dynamicId={dynamicId} count={data.versions} refreshKey={rev} disabled={disabled || !isAdmin()} onRestored={changed} />
    </div>
  );
}
