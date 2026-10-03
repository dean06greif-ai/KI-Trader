import React, { useState } from 'react';

const sourceOf = (a) => {
  if (a.source === 'dynamic_workbench') return 'Dynamik-Werkbank';
  if (a.nnfx || String(a.strategy_id || '').startsWith('nnfx_')) return 'NNFX-Framework';
  if (a.source_job_id) return 'Strategie-Suche';
  return 'manuell zugeordnet';
};

/** Klartext VOR dem Start: was der finale Walk-Forward genau testet – je Regime die zugeordnete Strategie. */
export default function WalkForwardScope({ analysis, scope, regimes, scopeKeyStr }) {
  const [open, setOpen] = useState(false);
  const all = analysis.assignments || {};
  const rows = regimes.map(r => ({ r, a: all[`${scopeKeyStr}:${r.id}`] }));
  const n = rows.filter(x => x.a).length;
  return (
    <div className="opt-small" data-testid={`regime-wf-scope-${scope}`}
      style={{ margin: '0 0 8px', padding: '8px 10px', borderRadius: 6, background: 'rgba(255,215,90,.05)', border: '1px solid rgba(255,215,90,.22)' }}>
      <b>Was testet der Walk-Forward?</b> Nicht die Erkennung allein – die Erkennung wird nur als „Schalter“ benutzt.
      Getestet wird die <b>zusammengestellte dynamische Strategie</b>: je Regime die unten zugeordnete Strategie
      ({n} von {regimes.length} Regimen belegt). Nichts davon ist zufällig – die Zuordnungen stammen aus „NNFX-Framework anwenden“,
      „Strategie suchen“ je Regime oder der Dynamik-Werkbank. Regime ohne Zuordnung handeln im Test nicht.
      Ablauf: Erkennung läuft Kerze für Kerze (ohne Zukunftswissen) über den Holdout → je erkanntem Regime handelt die zugeordnete Strategie →
      Ergebnis wird mit der besten Einzelstrategie verglichen, die immer durchläuft.
      <button className="opt-chip" style={{ fontSize: 10, marginLeft: 6 }} onClick={() => setOpen(v => !v)}
        data-testid={`regime-wf-scope-toggle-${scope}`}>
        {open ? '▾ Zuordnungen ausblenden' : '▸ Zuordnungen anzeigen'}
      </button>
      {open && (
        <table className="rl-compare-table" style={{ fontSize: 11, marginTop: 6 }} data-testid={`regime-wf-scope-table-${scope}`}>
          <thead><tr><th>Regime</th><th>Strategie im Test</th><th>Herkunft</th></tr></thead>
          <tbody>
            {rows.map(({ r, a }) => (
              <tr key={r.id} data-testid={`regime-wf-scope-row-${scope}-${r.id}`}>
                <td>{r.label || `#${r.id + 1}`}</td>
                <td>{a ? (a.strategy_name || a.strategy_id || 'eigene Regeln') : <span style={{ opacity: 0.6 }}>– handelt nicht –</span>}</td>
                <td>{a ? sourceOf(a) : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
