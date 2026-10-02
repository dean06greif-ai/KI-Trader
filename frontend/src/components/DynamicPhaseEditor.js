import React, { useCallback, useEffect, useState } from 'react';
import { toast } from '../lib/toast';
import { isAdmin } from '../auth';
import { postJson } from '../lib/postJson';
import DynamicVersionHistory from './DynamicVersionHistory';

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

function PhaseRow({ phase, strategies, releaseStatus, disabled, onChanged }) {
  const [choice, setChoice] = useState('');
  const [asking, setAsking] = useState(false);
  const current = phase.traded
    ? <><b>{phase.strategy_name}</b>{phase.own_rules && phase.rules?.length ? <span className="opt-small"> · {phase.rules.length} Regeln</span> : null}</>
    : <span className="neg">nicht handeln<span className="opt-small"> · {phase.skip_reason}</span></span>;
  return (
    <div className="dpe-row" data-testid={`dpe-row-${phase.regime}`}>
      <div className="dpe-cell dpe-label"><b>{phase.label}</b></div>
      <div className="dpe-cell" data-testid={`dpe-current-${phase.regime}`}><span className="opt-small">Aktuell: </span>{current}</div>
      <div className="dpe-cell" data-testid={`dpe-optimized-${phase.regime}`}><span className="opt-small">Optimiert: </span><OptimizedCell o={phase.optimized} /></div>
      <div className="dpe-cell dpe-act">
        <select value={choice} disabled={disabled || asking} onChange={e => setChoice(e.target.value)} data-testid={`dpe-select-${phase.regime}`}>
          <option value="">– Phase ändern –</option>
          {phase.traded && <option value="skip">Nicht handeln</option>}
          {phase.optimized && <option value="optimized">Optimierte Strategie aktivieren</option>}
          {strategies.filter(s => s.id !== phase.strategy_id || phase.own_rules).map(s => (
            <option key={s.id} value={`strategy:${s.id}`}>Ausgangs-Strategie: {s.name}</option>
          ))}
        </select>
        <button className="opt-cancel-run" disabled={!choice || disabled || asking} onClick={() => setAsking(true)} data-testid={`dpe-apply-${phase.regime}`}>Übernehmen…</button>
      </div>
      {asking && <PhaseConfirm phase={phase} choice={choice} strategies={strategies} releaseStatus={releaseStatus}
        onCancel={() => setAsking(false)} onDone={() => { setAsking(false); setChoice(''); onChanged(); }} />}
    </div>
  );
}

/** „Bestehende optimieren“: Strategie je Phase anzeigen, nach Bestätigung
 *  abschalten / tauschen, mit Versionsverlauf zum Zurückholen. */
export default function DynamicPhaseEditor({ dynamicId, strategies, disabled }) {
  const [data, setData] = useState(null);
  const [rev, setRev] = useState(0);
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
      </div>
      {!isAdmin() && <div className="opt-small">Admin-Login erforderlich, um Phasen zu ändern.</div>}
      {data.phases.map(p => (
        <PhaseRow key={p.regime} phase={{ ...p, dynamicId }} strategies={strategies} releaseStatus={data.release_status}
          disabled={disabled || !isAdmin()} onChanged={changed} />
      ))}
      <DynamicVersionHistory dynamicId={dynamicId} count={data.versions} refreshKey={rev} disabled={disabled || !isAdmin()} onRestored={changed} />
    </div>
  );
}
