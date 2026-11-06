import React, { useEffect, useState } from 'react';
import { toast } from '../lib/toast';
import { postJson } from '../lib/postJson';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const fmtDate = (iso) => { try { return new Date(iso).toLocaleString('de-DE'); } catch { return iso; } };

/** Parameter einer Variante als kompakte Pills. */
export function ParamPills({ trade, strategy, max = 6 }) {
  const items = [...Object.entries(trade || {}), ...Object.entries(strategy || {})];
  if (!items.length) return <span className="opt-small">Standard-Parameter</span>;
  return (
    <span className="opt-chips" style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4 }}>
      {items.slice(0, max).map(([k, v]) => (
        <span key={k} className="opt-param-pill">{k}={typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
      ))}
      {items.length > max && <span className="opt-small">+{items.length - max}</span>}
    </span>
  );
}

function VariantRow({ v, i, phase, disabled, onDone }) {
  const [asking, setAsking] = useState(false);
  const m = v.metrics || {};
  const back = async () => {
    try {
      const d = await postJson(`/api/dynamic/${phase.dynamicId}/phase`, {
        regime_id: phase.regime, action: 'variant', confirm: true,
        source_dynamic_id: v.dynamic_id, version: v.version,
      });
      toast.success(`Version ${d.version} gespeichert – ${d.reason}`);
      setAsking(false); onDone();
    } catch (e) { toast.error(e.message); }
  };
  return (
    <div className="dpe-version" data-testid={`dpv-variant-${phase.regime}-${i}`}>
      <div className="dpe-version-head">
        {v.current ? <span className="opt-badge">aktuell</span> : null}
        <b>{v.traded ? v.strategy_name : <span className="neg">nicht handeln</span>}</b>
        <span className="opt-small"> · {v.dynamic_name}{v.version ? ` v${v.version}` : ''} · {fmtDate(v.created_at)} · {v.reason}</span>
        {!v.current && <button className="opt-chip" disabled={disabled} onClick={() => setAsking(true)} data-testid={`dpv-back-${phase.regime}-${i}`}>zurückholen…</button>}
      </div>
      {v.traded && <ParamPills trade={v.trade_params} strategy={v.strategy_params} />}
      {v.own_rules && v.rules?.length > 0 && <div className="opt-small">Regeln: {v.rules.join(' · ')}</div>}
      {v.metrics && <div className="opt-small">Score {fmt(v.score)} · PnL {fmt(m.pnl, 2)}{m.trades != null ? ` · ${m.trades} Trades` : ''}</div>}
      {asking && (
        <div className="dyn-verdict warn dpe-confirm" data-testid={`dpv-confirm-${phase.regime}-${i}`}>
          <b>Diese Variante für „{phase.label}“ zurückholen?</b> Nur diese Phase ändert sich. Es entsteht eine neue Version.
          <div className="dpe-actions">
            <button className="opt-cancel-run" onClick={back} data-testid={`dpv-confirm-yes-${phase.regime}-${i}`}>Bestätigen & zurückholen</button>
            <button className="opt-cancel-run" onClick={() => setAsking(false)} data-testid={`dpv-confirm-no-${phase.regime}-${i}`}>Abbrechen</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Verlauf EINER Phase: alle bisherigen Varianten (Phasen-Anpassungen + frühere Optimierungs-Läufe). */
export default function DynamicPhaseVariants({ phase, refreshKey, disabled, onChanged }) {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    fetch(`${API_URL}/api/dynamic/${phase.dynamicId}/phase-history?regime_id=${phase.regime}`)
      .then(r => r.json()).then(d => setRows(d.variants || [])).catch(() => setRows([]));
  }, [phase.dynamicId, phase.regime, refreshKey]);
  if (!rows) return <div className="opt-small">Verlauf lädt…</div>;
  return (
    <div className="dpe-phase-history" data-testid={`dpv-panel-${phase.regime}`}>
      <div className="opt-small">Varianten dieser Phase ({rows.length}), neueste zuerst. Ältere Optimierungs-Läufe sind eingeschlossen.</div>
      {rows.map((v, i) => <VariantRow key={`${v.dynamic_id}-${v.version}-${i}`} v={v} i={i} phase={phase} disabled={disabled} onDone={onChanged} />)}
    </div>
  );
}
