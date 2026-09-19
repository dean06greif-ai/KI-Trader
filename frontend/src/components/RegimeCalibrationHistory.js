import React, { useCallback, useEffect, useState } from 'react';
import { ArrowClockwise } from '@phosphor-icons/react';
import { fmtDateTime } from '../lib/time';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

/**
 * Verlauf der gespeicherten Kalibrierungen (Cloud + lokaler Worker). Jede
 * Kalibrierung bleibt sichtbar und kann erneut übernommen werden. Die aktuell
 * übernommene Zeile ist markiert; Zeilen mit anderem Detektor sind gedimmt.
 */
export default function RegimeCalibrationHistory({ onApply, refreshKey, activeId, detector }) {
  const [rows, setRows] = useState(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    fetch(`${API_URL}/api/regime-lab/calibrations?limit=12`).then(r => r.json())
      .then(d => setRows(d.calibrations || [])).catch(() => setRows([]));
  }, []);

  useEffect(() => { if (open) load(); }, [open, load, refreshKey]);

  return (
    <div data-testid="regime-calibration-history" style={{ width: '100%', marginTop: 6 }}>
      <button className="opt-chip" onClick={() => setOpen(!open)} data-testid="regime-calibration-history-toggle">
        {open ? '▾' : '▸'} Kalibrierungs-Verlauf
      </button>
      {open && (
        <div style={{ marginTop: 6 }}>
          <div className="opt-small" style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
            Gespeicherte Kalibrierungen (neueste zuerst). „Übernehmen“ setzt Detektor + Feinwerte in die Engine-Einstellungen
            und markiert die Zeile als aktiv.
            <button className="opt-chip" style={{ fontSize: 10 }} onClick={load} data-testid="regime-calibration-history-refresh">
              <ArrowClockwise size={11} />
            </button>
          </div>
          {rows === null && <span className="opt-small">Lade…</span>}
          {rows !== null && rows.length === 0 && (
            <span className="opt-small" data-testid="regime-calibration-history-empty">Noch keine Kalibrierung gespeichert.</span>
          )}
          {(rows || []).map(r => {
            const rep = r.report || {};
            const det = rep.detector || 'regression';
            const isActive = activeId && r.id === activeId;
            const other = detector && det !== detector;
            return (
              <div key={r.id} className={`rl-cal-row ${isActive ? 'active' : ''} ${other ? 'other' : ''}`}
                data-testid={`regime-calibration-row-${r.id}`}
                title={other ? `Für Grundgerüst „${det}“ – beim Übernehmen wird das Grundgerüst mit umgestellt` : undefined}>
                <span className="opt-small">{fmtDateTime(r.created_at)}</span>
                <b>{(r.symbols || []).map(s => s.replace('USDT', '')).join(', ')}</b>
                <span className="opt-small">{r.timeframe} · {rep.total_days}d · Detektor <b>{det}</b></span>
                <span className="opt-small" title="Richtungs-Treffer (balanciert) vorher → kalibriert">
                  Treffer <b>{fmt(rep.baseline?.balanced_direction_pct)}%</b> → <b>{fmt(rep.best?.balanced_direction_pct)}%</b>
                  {rep.improved === false ? ' (keine Verbesserung)' : ''}
                </span>
                {isActive && <span className="rl-cal-active" data-testid={`regime-calibration-active-${r.id}`}>✓ aktiv</span>}
                <span style={{ flex: 1 }} />
                {rep.best_config && (
                  <button className={`opt-chip ${isActive ? 'on' : ''}`} onClick={() => onApply(r)}
                    data-testid={`regime-calibration-apply-${r.id}`}>
                    {isActive ? 'Erneut übernehmen' : 'Übernehmen'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
