import React, { useCallback, useEffect, useState } from 'react';
import { ArrowClockwise, ArrowCounterClockwise } from '@phosphor-icons/react';
import { fmtDateTime } from '../lib/time';
import { calibBaselinePct, calibPct, metricLabel } from '../lib/regimeCalibration';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const SOURCE_LABEL = { calibration: 'Wissenschaftlich', autopilot: 'Autopilot' };

/** Eine Zeile des vereinten Verlaufs (Kalibrierung ODER Autopilot). */
function HistoryRow({ row, isActive, detector, onApply }) {
  const rep = row.report || {};
  const det = rep.detector || 'regression';
  const other = detector && det !== detector;
  const src = SOURCE_LABEL[row.source] || row.source || 'Kalibrierung';
  return (
    <div className={`rl-cal-row ${isActive ? 'active' : ''} ${other ? 'other' : ''}`}
      data-testid={`regime-calibration-row-${row.id}`}
      title={other ? `Für Grundgerüst „${det}“ – beim Übernehmen wird das Grundgerüst mit umgestellt` : undefined}>
      <span className="opt-small">{fmtDateTime(row.created_at)}</span>
      <span className={`rl-cal-source ${row.source || 'calibration'}`} data-testid={`regime-calibration-source-${row.id}`}>{src}</span>
      <b>{(row.symbols || []).map(s => s.replace('USDT', '')).join(', ')}</b>
      <span className="opt-small">{row.timeframe} · {rep.total_days}d · Detektor <b>{det}</b></span>
      <span className="opt-small" title={`${metricLabel(rep)}: vorher → Ergebnis`}>
        {metricLabel(rep)} <b>{fmt(calibBaselinePct(rep))}%</b> → <b>{fmt(calibPct(rep))}%</b>
        {rep.improved === false ? ' (keine Verbesserung)' : ''}
        {rep.holdout_regressed ? ' · ⚠ Holdout gefallen' : ''}
      </span>
      {isActive && <span className="rl-cal-active" data-testid={`regime-calibration-active-${row.id}`}>✓ aktiv</span>}
      <span style={{ flex: 1 }} />
      {rep.best_config && (
        <button className={`opt-chip ${isActive ? 'on' : ''}`} onClick={() => onApply(row)}
          data-testid={`regime-calibration-apply-${row.id}`}>
          {isActive ? 'Erneut übernehmen' : 'Übernehmen'}
        </button>
      )}
    </div>
  );
}

/** Gesicherte Einstellungs-Stände (vor jeder Übernahme) – exakt zurückholbar. */
function SnapshotList({ snapshots, onRestore }) {
  if (!snapshots?.length) return null;
  return (
    <div style={{ marginTop: 8 }} data-testid="regime-engine-snapshots">
      <div className="opt-small" style={{ marginBottom: 4 }}>
        <b>Vorherige Einstellungen</b> – vor jeder Übernahme automatisch gesichert. „Zurückholen“ stellt
        Engine-Einstellungen UND aktive Kalibrierung exakt wieder her.
      </div>
      {snapshots.map(s => {
        const c = s.calib_applied;
        return (
          <div key={s.id} className="rl-cal-row" data-testid={`regime-snapshot-row-${s.id}`}>
            <span className="opt-small">{fmtDateTime(s.created_at)}</span>
            <span className="opt-small">Detektor <b>{s.engine_config?.detector || 'reactive'}</b></span>
            <span className="opt-small">
              {c ? <>{metricLabel(c)} <b>{fmt(c.best_pct)}%</b> · via {c.source || '–'}</> : 'ohne aktive Kalibrierung'}
            </span>
            <span className="opt-small" style={{ opacity: 0.7 }}>gesichert vor: {s.reason}</span>
            <span style={{ flex: 1 }} />
            <button className="opt-chip" onClick={() => onRestore(s)} data-testid={`regime-snapshot-restore-${s.id}`}>
              <ArrowCounterClockwise size={11} /> Zurückholen
            </button>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Vereinter Kalibrierungs-Verlauf (wissenschaftliche Kalibrierung + Autopilot,
 * Cloud + lokaler Worker) plus gesicherte Einstellungs-Stände. Jede Zeile kann
 * erneut übernommen werden; die aktive ist markiert, anderes Grundgerüst gedimmt.
 */
export default function RegimeCalibrationHistory({ onApply, onRestore, refreshKey, activeId, detector }) {
  const [rows, setRows] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    fetch(`${API_URL}/api/regime-lab/calibrations?limit=16`).then(r => r.json())
      .then(d => setRows(d.calibrations || [])).catch(() => setRows([]));
    fetch(`${API_URL}/api/regime-lab/engine/snapshots?limit=8`).then(r => r.json())
      .then(d => setSnapshots(d.snapshots || [])).catch(() => setSnapshots([]));
  }, []);

  useEffect(() => { if (open) load(); }, [open, load, refreshKey]);

  return (
    <div data-testid="regime-calibration-history" style={{ width: '100%', marginTop: 6 }}>
      <button className="opt-chip" onClick={() => setOpen(!open)} data-testid="regime-calibration-history-toggle">
        {open ? '▾' : '▸'} Kalibrierungs-Verlauf {rows ? `(${rows.length})` : ''}
      </button>
      {open && (
        <div style={{ marginTop: 6 }}>
          <div className="opt-small" style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
            Alle Kalibrierungen (wissenschaftlich UND Autopilot, neueste zuerst). „Übernehmen“ setzt Detektor + Feinwerte
            in die Engine-Einstellungen und markiert die Zeile als aktiv – der bisherige Stand wird vorher gesichert.
            <button className="opt-chip" style={{ fontSize: 10 }} onClick={load} data-testid="regime-calibration-history-refresh">
              <ArrowClockwise size={11} />
            </button>
          </div>
          {rows === null && <span className="opt-small">Lade…</span>}
          {rows !== null && rows.length === 0 && (
            <span className="opt-small" data-testid="regime-calibration-history-empty">Noch keine Kalibrierung gespeichert.</span>
          )}
          {(rows || []).map(r => (
            <HistoryRow key={`${r.source}-${r.id}`} row={r} detector={detector}
              isActive={!!activeId && r.id === activeId} onApply={onApply} />
          ))}
          {onRestore && <SnapshotList snapshots={snapshots} onRestore={onRestore} />}
        </div>
      )}
    </div>
  );
}
