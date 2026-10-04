import React, { useState } from 'react';
import { overfitInfo } from '../lib/overfit';
import { Star, PushPin, Crosshair, ArrowRight, CheckCircle, DownloadSimple } from '@phosphor-icons/react';

const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const short = (s) => s.replace('USDT', '');
// Ampel-Grade -> Klasse der Erkennungsqualität (RegimeLab.css, gleicher Farbverlauf wie RegimeQualityCard)
export const GRADE_CLS = { top: 'vgood', good: 'good', mid: 'mid', weak: 'bad' };
const FILTERS = [['all', 'alle'], ['good', 'gut & sehr gut'], ['top', 'nur sehr gut']];
const passes = (grade, f) => f === 'all' || (f === 'top' ? grade === 'top' : ['top', 'good'].includes(grade));

export function GradeBadge({ rating, testId }) {
  return (
    <span title={rating?.why} data-testid={testId} className={`rl-grade-badge ${GRADE_CLS[rating?.grade] || 'mid'}`}>
      {(rating?.label || '–').toUpperCase()}
    </span>
  );
}

/** Autopilot-Verlauf: Ampel je Lauf, übernehmen, direkt neue Erkennung, als Referenz weitersuchen. */
export default function RegimeAutopilotHistory({ runs, det, appliedId, referenceId, onApply, onAnalyze, onReference, onPin, onImport, importing }) {
  const [open, setOpen] = useState(true);
  const [filter, setFilter] = useState('all');
  const shown = runs.filter(r => passes(r.rating?.grade, filter));
  return (
    <div style={{ marginTop: 8 }} data-testid="autopilot-history-section">
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="opt-chip" style={{ fontSize: 10 }} onClick={() => setOpen(v => !v)} data-testid="autopilot-history-toggle">
          {open ? '▾' : '▸'} Autopilot-Verlauf ({runs.length})
        </button>
        {open && runs.length > 0 && (
          <select value={filter} onChange={e => setFilter(e.target.value)} data-testid="autopilot-history-filter" style={{ fontSize: 10 }}>
            {FILTERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        )}
        {open && onImport && (
          <button className="opt-chip" style={{ fontSize: 10 }} onClick={onImport} disabled={importing} data-testid="autopilot-history-import"
            title="Deine gespeicherten Regime-Analysen (Erkennung + Kennzahlen) als gemerkte Zeilen in diesen Verlauf übernehmen – bleiben dauerhaft gesichert, auch wenn die Analyse später gelöscht wird. Bereits übernommene werden übersprungen.">
            <DownloadSimple size={11} /> {importing ? 'Übernehme …' : 'Bestehende Regime sichern & einfügen'}
          </button>
        )}
      </div>
      {open && runs.length === 0 && (
        <div className="opt-small" style={{ marginTop: 4, opacity: 0.75 }} data-testid="autopilot-history-empty">
          Noch keine gespeicherten Autopilot-Läufe – jeder fertige Lauf (Cloud oder lokaler Worker) erscheint hier mit Bewertung.
        </div>
      )}
      {open && runs.length > 0 && (
        <>
          <div className="opt-small" style={{ margin: '4px 0', opacity: 0.8 }}>
            <b>übernehmen</b> = Erkennung oben in die Engine-Einstellungen · <b>Regime suchen</b> = übernehmen und sofort mit den Coins/Timeframe des Laufs eine neue Erkennung speichern ·
            <b> Referenz</b> = nächste Endlos-Suche startet von diesem Ergebnis (auch auf anderem Timeframe, z.B. 1h → 4h) – kein Suchfortschritt geht verloren. ★ = bester Lauf für diese Coins/Timeframe.
          </div>
          <table className="rl-compare-table" data-testid="autopilot-history" style={{ fontSize: 11 }}>
            <thead>
              <tr><th>Bewertung</th><th>Datum</th><th>Coins · TF</th><th>Grundgerüst</th><th title="Such-Score = Rangliste der Autopilot-Suche (nur Trainingsdaten + innere Validierung, ohne Holdout). Formel: 25 % Live=Final + 75 % Referenz-Macro-F1, ± Regime-Nutzen, minus Phasen-Strafe. Theoretisch ≈ 100 (max. 105), praktisch liegt die Obergrenze bei ca. 65–70: die Referenz kennt die Zukunft, eine Live-Erkennung muss Wendepunkte erst bestätigen. Die Bewertung links ist die Erkennungsqualität (gleiche Kriterien wie im Regime-Lab).">Score ⓘ</th><th>Holdout-F1</th><th>Innere Val.</th><th>Ø Phase</th><th>Varianten</th><th></th></tr>
            </thead>
            <tbody>
              {shown.map(r => {
                const res = r.result || {};
                const m = res.best?.metrics || {};
                const hl = appliedId === r.id ? 'rgba(80,200,120,0.12)' : referenceId === r.id ? 'rgba(120,190,255,0.12)' : undefined;
                return (
                  <tr key={r.id} data-testid={`autopilot-run-${r.id}`} className={`rl-grade-row ${GRADE_CLS[r.rating?.grade] || ''}`} style={{ background: hl }}>
                    <td>
                      <GradeBadge rating={r.rating} testId={`autopilot-run-grade-${r.id}`} />
                      {r.best_in_group && <Star size={12} weight="fill" color="#ffd75a" style={{ marginLeft: 4 }} data-testid={`autopilot-run-best-${r.id}`} />}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>{new Date(r.created_at).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })}
                      {res.source === 'import' && <div className="opt-small" style={{ opacity: 0.75 }} data-testid={`autopilot-run-imported-${r.id}`} title="Aus einer gespeicherten Regime-Analyse übernommen (kein Autopilot-Lauf)">⤓ importiert: {res.imported_from?.name || 'Analyse'}
                        {(res.imported_from?.grade || res.imported_from?.regimes) && (
                          <span data-testid={`autopilot-run-imported-grade-${r.id}`} title="Erkennungs-Note und Regime-Zahl der ursprünglichen Regime-Analyse">
                            {' '}· Analyse: {[res.imported_from.grade, res.imported_from.regimes ? `${res.imported_from.regimes} Regime` : null].filter(Boolean).join(', ')}
                          </span>
                        )}</div>}
                      {overfitInfo(res) && <div className="opt-small" style={{ color: '#ffd75a' }} data-testid={`autopilot-run-overfit-${r.id}`} title="Score gestiegen, Abschlusstest (Holdout) gefallen – wahrscheinlich Überanpassung">⚠ Überanpassung ({(res.tested || 0).toLocaleString('de-DE')} Var.)</div>}
                      {res.settings?.fine_mode && <div className="opt-small" style={{ opacity: 0.75 }} data-testid={`autopilot-run-fine-${r.id}`}>🔍 Kurze Feinsuche{res.fine_start?.source ? ` · Start: ${res.fine_start.source}` : ''}</div>}
                      {res.reference && <div className="opt-small" style={{ opacity: 0.7 }} data-testid={`autopilot-run-ref-${r.id}`}>⤷ aus Referenz {String(res.reference.created_at || '').slice(0, 10)} · {res.reference.timeframe}</div>}
                    </td>
                    <td>{(res.symbols || []).map(short).join(', ')}<div className="opt-small" style={{ whiteSpace: 'nowrap' }}><b>{res.timeframe}</b> · {res.days}d
                      {res.tf_chain?.changed_timeframe && <span title="Timeframe-Kette: bester Timeframe statt des gewählten" data-testid={`autopilot-run-tfchain-${r.id}`}> · ⛓ statt {res.tf_chain.selected_timeframe}</span>}</div></td>
                    <td>{det[res.best?.detector] || res.best?.detector}</td>
                    <td><b>{fmt(res.best?.score)}</b>{res.baseline?.score != null && <span className="opt-small"> ({fmt(res.baseline.score)})</span>}</td>
                    <td>{fmt(m.holdout_reference_f1_pct)}%</td>
                    <td>{fmt(m.inner_direction_pct)}%</td>
                    <td>{fmt(m.avg_live_phase_days)}d</td>
                    <td>{res.tested} · {res.improvements} ↑{res.followup ? ' · ⛓' : ''}</td>
                    <td style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                      <button className="opt-chip" data-testid={`autopilot-run-apply-${r.id}`} onClick={() => onApply(r)}
                        title="Erkennung (Grundgerüst + Feineinstellungen) oben in die Engine-Einstellungen übernehmen">
                        {appliedId === r.id ? <CheckCircle size={11} weight="fill" /> : null} übernehmen
                      </button>
                      <button className="opt-chip" data-testid={`autopilot-run-analyze-${r.id}`} onClick={() => onAnalyze(r)}
                        title="Übernehmen und sofort „Regime suchen & speichern“ mit Coins/Timeframe/Zeitraum dieses Laufs">
                        <ArrowRight size={11} /> Regime suchen
                      </button>
                      <button className="opt-chip" data-testid={`autopilot-run-reference-${r.id}`} onClick={() => onReference(r)}
                        style={referenceId === r.id ? { borderColor: '#8cc8ff', color: '#8cc8ff' } : undefined}
                        title="Als Startpunkt für die nächste Autopilot-Suche verwenden (Coins/Timeframe oben frei wählbar)">
                        <Crosshair size={11} /> {referenceId === r.id ? 'Referenz ✓' : 'Referenz'}
                      </button>
                      <button className="opt-chip" data-testid={`autopilot-run-pin-${r.id}`} onClick={() => onPin(r)}
                        title={r.pinned ? 'Gemerkt – wird bei der Datenbank-Bereinigung nie gelöscht (klicken zum Lösen)' : 'Merken – bleibt dauerhaft im Verlauf'}
                        style={r.pinned ? { color: '#ffd75a' } : undefined}>
                        <PushPin size={11} weight={r.pinned ? 'fill' : 'regular'} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
