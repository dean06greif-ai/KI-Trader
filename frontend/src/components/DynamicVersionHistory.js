import React, { useEffect, useState } from 'react';
import { ClockCounterClockwise } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { postJson } from '../lib/postJson';
import DynamicVersionCompare from './DynamicVersionCompare';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmtDate = (iso) => { try { return new Date(iso).toLocaleString('de-DE'); } catch { return iso; } };

function VersionSummary({ summary }) {
  return (
    <div className="opt-chips" style={{ marginTop: 4 }}>
      {(summary || []).map(p => (
        <span key={p.regime} className="opt-param-pill">{p.label}: {p.traded ? p.strategy_name : <span className="neg">nicht handeln</span>}</span>
      ))}
    </div>
  );
}

function VersionRow({ v, latest, disabled, dynamicId, onRestored, picked, onPick }) {
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState(false);
  const restore = async () => {
    try {
      const d = await postJson(`/api/dynamic/${dynamicId}/versions/${v.version}/restore`, { confirm: true });
      toast.success(`Version ${v.version} wiederhergestellt (neu: Version ${d.version})`);
      setAsking(false); onRestored();
    } catch (e) { toast.error(e.message); }
  };
  return (
    <div className="dpe-version" data-testid={`dvh-version-${v.version}`}>
      <div className="dpe-version-head">
        <label className="opt-check" title="Für den Vergleich auswählen (2 Versionen)">
          <input type="checkbox" checked={picked} onChange={() => onPick(v.version)} data-testid={`dvh-pick-${v.version}`} />
        </label>
        <b>v{v.version}</b>{latest ? <span className="opt-badge"> aktuell</span> : null}
        <span className="opt-small"> · {fmtDate(v.created_at)} · {v.reason}</span>
        <button className="opt-chip" onClick={() => setOpen(o => !o)} data-testid={`dvh-view-${v.version}`}>{open ? 'zuklappen' : 'ansehen'}</button>
        {!latest && <button className="opt-chip" disabled={disabled} onClick={() => setAsking(true)} data-testid={`dvh-restore-${v.version}`}>wiederherstellen…</button>}
      </div>
      {open && <VersionSummary summary={v.summary} />}
      {asking && (
        <div className="dyn-verdict warn dpe-confirm" data-testid={`dvh-confirm-${v.version}`}>
          <b>Version {v.version} wiederherstellen?</b> Der aktuelle Stand bleibt im Verlauf erhalten.
          <VersionSummary summary={v.summary} />
          <div className="dpe-actions">
            <button className="opt-cancel-run" onClick={restore} data-testid={`dvh-confirm-yes-${v.version}`}>Bestätigen & wiederherstellen</button>
            <button className="opt-cancel-run" onClick={() => setAsking(false)} data-testid={`dvh-confirm-no-${v.version}`}>Abbrechen</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Versionsverlauf der Phasen-Anpassungen – ältere Stände ansehen & zurückholen. */
export default function DynamicVersionHistory({ dynamicId, count, refreshKey, disabled, onRestored }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState([]);
  const [pick, setPick] = useState([]);
  // max. 2 Versionen: die älteste Auswahl fällt heraus
  const togglePick = (ver) => setPick(p => (p.includes(ver) ? p.filter(x => x !== ver) : [...p, ver].slice(-2)));
  const [va, vb] = [...pick].sort((x, y) => x - y);
  useEffect(() => {
    if (!open) return;
    fetch(`${API_URL}/api/dynamic/${dynamicId}/versions`).then(r => r.json())
      .then(d => setRows(d.versions || [])).catch(() => setRows([]));
  }, [open, dynamicId, refreshKey]);
  return (
    <div className="dpe-history" data-testid="dvh-panel">
      <button className="opt-chip" onClick={() => setOpen(o => !o)} data-testid="dvh-toggle">
        <ClockCounterClockwise size={13} /> Verlauf ({count || 0} {count === 1 ? 'Version' : 'Versionen'})
      </button>
      {open && !rows.length && <div className="opt-small" data-testid="dvh-empty">Noch keine Änderungen – die erste Phasen-Anpassung sichert den Ausgangsstand als Version 1.</div>}
      {open && rows.length > 1 && (
        <div className="opt-small" data-testid="dvh-compare-hint">
          {pick.length < 2 ? `Zwei Versionen anhaken, um sie nebeneinander zu vergleichen (${pick.length}/2).` : `Vergleich v${va} ↔ v${vb}`}
        </div>
      )}
      {open && pick.length === 2 && <DynamicVersionCompare dynamicId={dynamicId} a={va} b={vb} />}
      {open && rows.map((v, i) => (
        <VersionRow key={v.id} v={v} latest={i === 0} disabled={disabled} dynamicId={dynamicId} onRestored={onRestored}
          picked={pick.includes(v.version)} onPick={togglePick} />
      ))}
    </div>
  );
}
