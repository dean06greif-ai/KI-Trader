import React from 'react';
import { Trash, Eye } from '@phosphor-icons/react';

const fmtTs = (iso) => (iso ? iso.slice(0, 16).replace('T', ' ') : '–');

/** Gespeicherte Korrelations-Ergebnisse: ansehen / löschen (neueste zuerst). */
export default function RegimeCorrelationRuns({ runs, shownId, onShow, onDelete }) {
  if (!runs?.length) return null;
  return (
    <div style={{ marginTop: 6 }} data-testid="regime-correlation-runs">
      <b>Gespeicherte Ergebnisse</b> ({runs.length})
      <div style={{ overflowX: 'auto' }}>
        <table className="rl-compare-table" style={{ fontSize: 11, marginTop: 3 }}>
          <thead>
            <tr><th>Stand</th><th>TF · Tage</th><th>Assets</th><th title="(nahezu) perfekte Korrelation, |r| ≥ 0,95">±1</th><th /></tr>
          </thead>
          <tbody>
            {runs.map(r => (
              <tr key={r.id} data-testid={`regime-correlation-run-${r.id}`}
                style={shownId === r.id ? { background: 'rgba(80,200,120,0.12)' } : undefined}>
                <td style={{ whiteSpace: 'nowrap' }}>{fmtTs(r.created_at)}</td>
                <td>{r.timeframe} · {r.days}</td>
                <td>{r.with_data}/{r.assets}{r.missing ? <span className="opt-small" style={{ color: '#f5a623' }}> · {r.missing} ohne Daten</span> : null}</td>
                <td>{r.perfect ? <b style={{ color: '#ffd75a' }}>★ {r.perfect}</b> : '–'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="opt-chip" onClick={() => onShow(r.id)} data-testid={`regime-correlation-run-show-${r.id}`}>
                    <Eye size={11} /> {shownId === r.id ? 'angezeigt' : 'ansehen'}
                  </button>
                  <button className="opt-chip" style={{ color: '#ff8a80', marginLeft: 3 }} onClick={() => onDelete(r)}
                    data-testid={`regime-correlation-run-delete-${r.id}`} title="Ergebnis löschen">
                    <Trash size={11} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
