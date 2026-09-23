import React, { useState } from 'react';
import { Play, MoonStars } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { addToSeries } from '../lib/series';
import { useLabJob, EdgeBanner } from './RegimeJobProgress';

// „+ Warteschlange“: Werkzeug in die Regime-Lab-Warteschlange einreihen –
// läuft automatisch nacheinander und erscheint im Haupt-Balken.
const QueueButton = ({ kind, body, onQueued, testId }) => (
  <button className="opt-chip" data-testid={testId}
    title="In die Regime-Lab-Warteschlange einreihen – läuft automatisch, sobald kein anderer Job mehr rechnet"
    onClick={async () => {
      if (!(body.symbols || []).length) { toast.error('Mindestens 1 Coin wählen'); return; }
      const item = await addToSeries(kind, body);
      if (item) onQueued?.();
    }}>
    <MoonStars size={11} /> + Warteschlange
  </button>
);

/** Hinweis statt eigenem Ladebalken: alle Werkzeuge laufen sichtbar im Haupt-Balken (Abschnitt 1). */
const RunningHint = ({ running, testId }) => (running ? (
  <div className="opt-small" data-testid={testId} style={{ marginTop: 6 }}>
    ⏳ Läuft – Fortschritt, Restzeit, Pause und Abbruch im Haupt-Balken oben (Abschnitt 1).
  </div>
) : null);

/** Kompakter „Was jetzt?“-Kasten unter einem Ergebnis. */
export const WhatNow = ({ testId, children }) => (
  <div className="rl-whatnow" data-testid={testId}><b>Was jetzt?</b><div style={{ flex: 1, minWidth: 220 }}>{children}</div></div>
);

const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const th = (extra = {}) => ({ padding: '3px 10px', ...extra });
const BEST_BG = { background: 'rgba(80,200,120,0.12)' };

const SelectionNote = ({ result, testId, children }) => (
  <div className="opt-small" style={{ marginTop: 4 }} data-testid={testId}>
    ★ = beste INNERE Validierung (der Holdout ist finaler Test, keine Auswahlbasis
    {result.selection_basis === 'inner_validation_reference' ? ' · Auswahl nach REFERENZ-Treffer (detektor-unabhängig), nicht nach Live=Final' : ''}
    {result.selection_basis === 'train_only' ? ' · Auswahl hier: nur Training, kein inneres Fenster' : ''}).
    {result.attempt_no ? ` Versuch #${result.attempt_no} auf diesem Holdout.` : ''}
    {result.evidence === 'insufficient_evidence' ? ' ⚠ Zu wenig Holdout-Daten – Ergebnis nicht belastbar.' : ''}
    {' '}{children}
  </div>
);

// ---------------- Indikator-Ablation (AP13) ----------------
const ABLATION_VERDICT = {
  traegt_bei: { text: 'trägt bei', cls: 'pos' },
  redundant: { text: 'redundant', cls: '' },
  schadet: { text: 'schadet', cls: 'neg' },
  unbewertet: { text: 'unbewertet', cls: '' },
};

/**
 * Ablation in Klartext (rein): Was sagt die Tabelle, was fehlt, was ist der
 * nächste Schritt? Berücksichtigt auch den Fall „einfache Alternative ohne
 * Live-Kennzahlen“ (–% / unbewertet) – dann ist die Zeile ohne Aussage.
 */
export function ablationInterpretation(result) {
  const rows = result?.rows || [];
  const full = rows.find(r => r.variant_key === 'full');
  const verdicts = result?.verdicts || {};
  const parts = rows.filter(r => r.variant_key !== 'full' && !String(r.variant_key).startsWith('alt_'));
  const alts = rows.filter(r => String(r.variant_key).startsWith('alt_'));
  const helps = parts.filter(r => verdicts[r.variant_key]?.verdict === 'traegt_bei');
  const hurts = parts.filter(r => verdicts[r.variant_key]?.verdict === 'schadet');
  const redundant = parts.filter(r => verdicts[r.variant_key]?.verdict === 'redundant');
  const altRated = alts.filter(r => r.holdout_direction_pct != null && r.inner_direction_pct != null);
  const altUnrated = alts.filter(r => r.holdout_direction_pct == null || r.inner_direction_pct == null);
  const bestAlt = altRated.length
    ? altRated.reduce((a, b) => ((b.holdout_direction_pct || 0) > (a.holdout_direction_pct || 0) ? b : a)) : null;
  const gateDelta = full && bestAlt
    ? Number(full.holdout_direction_pct || 0) - Number(bestAlt.holdout_direction_pct || 0) : null;
  const lines = [];
  if (!parts.length) {
    lines.push('Dein Grundgerüst hat keine abschaltbaren Bestätigungs-Komponenten – es gab nur „voll“ und die einfache Alternative zu vergleichen.');
  } else {
    if (helps.length) lines.push(`Behalten: ${helps.map(r => r.name.replace(/^ohne /, '')).join(', ')} (trägt bei).`);
    if (hurts.length) lines.push(`Kandidat zum Abschalten: ${hurts.map(r => r.name.replace(/^ohne /, '')).join(', ')} (schadet) – in den Engine-Feineinstellungen deaktivieren, dann neu „Regime suchen & speichern“ und die Note vergleichen.`);
    if (redundant.length) lines.push(`Ohne Wirkung: ${redundant.map(r => r.name.replace(/^ohne /, '')).join(', ')} (redundant) – kann bleiben, bringt aber nichts.`);
  }
  if (altUnrated.length) {
    lines.push(`„–% / unbewertet“ bei ${altUnrated.map(r => r.name).join(', ')}: diese Alternative liefert keine Live-Kennzahlen (keine Live-Sicht) – die Zeile hat keine Aussage. Das ist kein Fehler von dir.`);
  }
  if (gateDelta !== null) {
    lines.push(gateDelta >= 0
      ? `Freigabe-Nachweis: volle Konfiguration ${gateDelta >= 0 ? '+' : ''}${gateDelta.toFixed(1)}pp vs. einfache Alternative im Holdout – Regime-Umschaltung verliert nicht ✓.`
      : `Freigabe-Nachweis: volle Konfiguration ${gateDelta.toFixed(1)}pp SCHLECHTER als die einfache Alternative im Holdout – so wird die Freigabe blockiert.`);
  }
  const next = full && (full.holdout_direction_pct ?? 0) >= 65
    ? 'Nächster Schritt: Analyse unter „3 · Analyse“ öffnen → „Behalten vorschlagen“ → „Beobachten (Shadow)“ (die Knöpfe zeigen an, welcher Nachweis noch fehlt).'
    : 'Nächster Schritt: Erkennung verbessern (Autopilot oder anderes Grundgerüst), dann neu „Regime suchen & speichern“.';
  return { lines, next, fullHoldout: full?.holdout_direction_pct ?? null, gateDelta };
}

export function AblationCompare({ selCoins, timeframe, days, trainPct, engineConfig, jobBlocked, onQueued, onStarted, onResult }) {
  const body = { symbols: selCoins, timeframe, days, train_pct: trainPct, engine_config: engineConfig || {} };
  const [result, setResult] = useState(null);
  const { start, running } = useLabJob({
    errorLabel: 'Ablation fehlgeschlagen', onStarted,
    onDone: (res) => { if (res?.rows) { setResult(res); onResult?.(res); } },
  });
  const run = () => {
    setResult(null);
    start('/api/regime-lab/ablation', body, { requireCoins: true, kind: 'ablation' });
  };
  const verdictOf = (key) => ABLATION_VERDICT[result?.verdicts?.[key]?.verdict] || null;
  const interpretation = result ? ablationInterpretation(result) : null;

  return (
    <div className="opt-row rl-tool" data-testid="ablation-section">
      <div className="opt-label">C · INDIKATOR-ABLATION – WELCHE BESTÄTIGUNG HILFT WIRKLICH?</div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        <b>Wann?</b> Nach der Kalibrierung, wenn du wissen willst, ob eine Bestätigungs-Komponente
        (ADX, Effizienz, Volumen, EMA …) etwas bringt oder nur bremst. Derselbe Datensatz: Detektor
        voll vs. ohne je eine Komponente vs. einfache Alternative. Ergebnis ist eine Bewertung
        („trägt bei / redundant / schadet“) – <b>kein</b> automatisches Übernehmen. Außerdem ist die
        Ablation ein <b>Nachweis für die Freigabe</b> (Shadow) – gleiche Coins/Timeframe wie die Analyse wählen.
      </div>
      <div className="opt-setup" style={{ alignItems: 'center' }}>
        <button className="opt-chip" onClick={run} disabled={running || jobBlocked} data-testid="ablation-run">
          <Play size={11} weight="fill" /> Ablation starten
        </button>
        <QueueButton kind="regime_ablation" body={body} onQueued={onQueued} testId="ablation-queue" />
      </div>
      <RunningHint running={running} testId="ablation-running" />
      {result && (
        <div style={{ overflowX: 'auto', marginTop: 6 }}>
          <table className="rl-compare-table" data-testid="ablation-table"
            style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: 640 }}>
            <thead>
              <tr style={{ textAlign: 'left', opacity: 0.7 }}>
                <th style={th({ paddingLeft: 0 })}>Variante</th>
                <th style={th()} title="Anteil der Kerzen, bei denen die Live-Sicht dieselbe Richtung sieht wie die finale Sicht">Live=Final</th>
                <th style={th()} title="Innere Validierung (letzter Teil des Trainingsfensters) – Bewertungsbasis">Innere Val.</th>
                <th style={th()} title="Holdout = unangetasteter FINALER Test (keine Auswahlbasis)">Holdout (finaler Test)</th>
                <th style={th()} title="Nur auf Trend-Kerzen (Auf/Ab)">Trend-Treffer</th>
                <th style={th()}>Wechsel (final/live)</th>
                <th style={th()} title="Beitrag der entfernten Komponente: volle Konfiguration minus diese Variante (innere Validierung)">Beitrag</th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map(r => (
                <tr key={r.variant_key} data-testid={`ablation-row-${r.variant_key}`}
                  style={r.variant_key === result.best_variant ? BEST_BG : undefined}>
                  <td style={th({ paddingLeft: 0 })}><b>{r.name}</b>{r.variant_key === result.best_variant ? ' ★' : ''}</td>
                  {r.error ? <td colSpan={6} className="neg">{r.error}</td> : (
                    <>
                      <td style={th()}>{fmt(r.direction_pct, 1)}%</td>
                      <td style={th()}><b>{fmt(r.inner_direction_pct, 1)}%</b></td>
                      <td style={th()}>{fmt(r.holdout_direction_pct, 1)}%</td>
                      <td style={th()}>{fmt(r.trend_hit_pct, 1)}%</td>
                      <td style={th()}>{r.switches_final} / {r.switches_live}</td>
                      <td style={th()} className={verdictOf(r.variant_key)?.cls || ''}>
                        {r.variant_key === 'full' ? '–'
                          : `${verdictOf(r.variant_key)?.text || '–'}${
                            result.verdicts?.[r.variant_key]?.delta_pp !== null
                            && result.verdicts?.[r.variant_key]?.delta_pp !== undefined
                              ? ` (${result.verdicts[r.variant_key].delta_pp > 0 ? '+' : ''}${result.verdicts[r.variant_key].delta_pp}pp)` : ''}`}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {(() => {
            const pool = result.rows.find(r => r.variant_key === result.best_variant)?.pooling;
            const classes = pool?.classes ? Object.values(pool.classes) : [];
            if (classes.length < 1) return null;
            return (
              <div className="opt-small" style={{ marginTop: 4 }} data-testid="ablation-pooling">
                Pooling (★-Variante, {pool.basis === 'bars_weighted' ? 'kerzen-gewichtet' : pool.basis}):{' '}
                {classes.map(c => `${c.label} ${fmt(c.direction_pct, 1)}% (${c.n_symbols} Symbole${
                  Object.keys(c.deviation || {}).length > 1
                    ? `, Abweichung ${Object.entries(c.deviation).map(([s, d]) => `${s} ${d > 0 ? '+' : ''}${d}pp`).join(', ')}` : ''})`).join(' · ')}
              </div>
            );
          })()}
          <SelectionNote result={result} testId="ablation-selection-note" />
          {interpretation && (
            <WhatNow testId="ablation-whatnow">
              <ul>{interpretation.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
              <div style={{ marginTop: 4 }}><b style={{ color: 'inherit' }}>{interpretation.next}</b></div>
            </WhatNow>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------- EMA-Perioden-Vergleich (Detektor 'ema') ----------------
export function EmaPeriodCompare({ selCoins, timeframe, days, trainPct, engineConfig, setEngineConfig, jobBlocked, onQueued, onStarted, onResult }) {
  const [periods, setPeriods] = useState('5, 9, 14');
  const [result, setResult] = useState(null);
  const [banner, setBanner] = useState(null);

  // Sieger-Periode direkt in die Engine-Feineinstellungen (Detektor 'ema') –
  // danach „Regime suchen & speichern“ neu starten.
  const applyBest = (res) => {
    if (!res?.best_period || !setEngineConfig) return;
    const prev = engineConfig?.ema_regime_days;
    const bestRow = res.rows.find(r => r.period === res.best_period) || {};
    const prevRow = res.rows.find(r => r.period === prev);
    setEngineConfig({ ...(engineConfig || {}), detector: 'ema', ema_regime_days: res.best_period });
    const same = prev === res.best_period;
    setBanner({
      improved: !same,
      title: same
        ? `Deine EMA-Periode (${prev}d) ist bereits die beste im Vergleich`
        : `Besserer Edge gefunden & übernommen: EMA ${prev ? `${prev}d → ` : ''}${res.best_period}d`,
      detail: (bestRow.inner_reference_pct != null
        ? `Referenz-Treffer (innere Val.) ${prevRow ? `${fmt(prevRow.inner_reference_pct, 1)}% → ` : ''}${fmt(bestRow.inner_reference_pct, 1)}%`
          + ` · Referenz Holdout ${fmt(bestRow.holdout_reference_pct, 1)}% · Live=Final ${fmt(bestRow.inner_direction_pct, 1)}%`
        : `Innere Validierung ${prevRow ? `${fmt(prevRow.inner_direction_pct, 1)}% → ` : ''}${fmt(bestRow.inner_direction_pct, 1)}%`
          + ` · Holdout ${fmt(bestRow.holdout_direction_pct, 1)}%`)
        + ` · Detektor auf 'ema' gestellt – jetzt oben „Regime suchen & speichern“ starten`,
    });
  };

  const { start, running } = useLabJob({
    errorLabel: 'Vergleich fehlgeschlagen', onStarted,
    onDone: (res) => { if (res?.rows) { setResult(res); applyBest(res); onResult?.(res); } },
  });

  const parsePeriods = () => periods.split(',').map(x => parseFloat(x.trim())).filter(x => x >= 2 && x <= 100);
  const emaBody = () => ({ symbols: selCoins, timeframe, days, train_pct: trainPct,
    periods: parsePeriods(), engine_config: engineConfig || {} });
  const run = () => {
    if (!parsePeriods().length) { toast.error('Perioden 2-100 Tage angeben, z.B. 5, 9, 14'); return; }
    setResult(null); setBanner(null);
    start('/api/regime-lab/ema-compare', emaBody(), { requireCoins: true, kind: 'ema_compare' });
  };

  return (
    <div className="opt-row rl-tool" data-testid="ema-compare-section">
      <div className="opt-label">A · EMA-PERIODEN-VERGLEICH – NUR FÜR GRUNDGERÜST „EMA-STEIGUNG“</div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        <b>Wann?</b> Wenn du das Grundgerüst „EMA-Steigung“ nutzt und die beste Glättungs-Periode suchst.
        Gleiche Coins/Zeitraum wie oben, mehrere Perioden im direkten Vergleich. Die Sieger-Periode
        wird <b>automatisch übernommen</b> (Detektor wird auf „ema“ gestellt).
      </div>
      <div className="opt-setup" style={{ alignItems: 'center' }}>
        <label className="opt-field" title="Kommagetrennte EMA-Perioden in Tagen (2-100), max. 8">
          Perioden (Tage)
          <input value={periods} onChange={e => setPeriods(e.target.value)}
            placeholder="5, 9, 14" style={{ width: 110 }} data-testid="ema-compare-periods" />
        </label>
        <button className="opt-chip" onClick={run} disabled={running || jobBlocked} data-testid="ema-compare-run">
          <Play size={11} weight="fill" /> Vergleich starten
        </button>
        <QueueButton kind="regime_ema_compare" body={emaBody()} onQueued={onQueued} testId="ema-compare-queue" />
      </div>
      <RunningHint running={running} testId="ema-compare-running" />
      {banner && <EdgeBanner {...banner} testId="ema-compare-banner" />}
      {result && (
        <div style={{ overflowX: 'auto', marginTop: 6 }}>
          <table className="rl-compare-table" data-testid="ema-compare-table"
            style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: 620 }}>
            <thead>
              <tr style={{ textAlign: 'left', opacity: 0.7 }}>
                <th style={th({ paddingLeft: 0 })}>EMA</th>
                <th style={th()} title="Live-Sicht vs. detektor-UNABHÄNGIGE Referenz (zentrierte Rückblick-Phasen) in der inneren Validierung – DARAUF wird die beste Periode gewählt. Live=Final bevorzugt sonst lange EMAs (Selbst-Übereinstimmung).">Referenz innere Val. ★</th>
                <th style={th()} title="Live-Sicht vs. Referenz im Holdout (finaler Test, keine Auswahlbasis)">Referenz Holdout</th>
                <th style={th()} title="Tage, bis die Live-Sicht einen Referenz-Phasenwechsel zeigt · Anteil nie getroffener Referenz-Phasen">Lag / verpasst</th>
                <th style={th()} title="Anteil der Kerzen, bei denen die Live-Sicht dieselbe Richtung sieht wie die finale Sicht DESSELBEN Detektors (Selbst-Übereinstimmung – nicht zwischen Detektoren vergleichbar)">Live=Final</th>
                <th style={th()} title="Live=Final in der inneren Validierung (nur Rückfall, wenn keine Referenz vorliegt)">Innere Val.</th>
                <th style={th()} title="Live=Final im Holdout (finaler Test)">Holdout (finaler Test)</th>
                <th style={th()} title="Nur auf Trend-Kerzen (Auf/Ab)">Trend-Treffer</th>
                <th style={th()} title="Durchschnittliche Phasendauer der finalen Sicht">Ø Phase final</th>
                <th style={th()} title="Durchschnittliche Phasendauer der Live-Sicht (kürzer = mehr Flackern)">Ø Phase live</th>
                <th style={th()}>Wechsel (final/live)</th>
                <th style={th()} title="Anteil der Kerzen, die gegen ihr Regime-Label laufen">Verstöße</th>
                <th style={th()}>Prüfung</th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map(r => (
                <tr key={r.period} data-testid={`ema-compare-row-${r.period}`}
                  style={r.period === result.best_period ? BEST_BG : undefined}>
                  <td style={th({ paddingLeft: 0 })}><b>{r.period}d</b>{r.period === result.best_period ? ' ★' : ''}</td>
                  {r.error ? <td colSpan={12} className="neg">{r.error}</td> : (
                    <>
                      <td style={th()} data-testid={`ema-compare-ref-${r.period}`}><b>{fmt(r.inner_reference_pct, 1)}%</b></td>
                      <td style={th()}>{fmt(r.holdout_reference_pct, 1)}%</td>
                      <td style={th()}>{fmt(r.reference_lag_days, 1)}d / {fmt(r.reference_missed_pct, 0)}%</td>
                      <td style={th()}>{fmt(r.direction_pct, 1)}%</td>
                      <td style={th()}>{fmt(r.inner_direction_pct, 1)}%</td>
                      <td style={th()}>{fmt(r.holdout_direction_pct, 1)}%</td>
                      <td style={th()}>{fmt(r.trend_hit_pct, 1)}%</td>
                      <td style={th()}>{fmt(r.avg_final_segment_days, 1)}d</td>
                      <td style={th()}>{fmt(r.avg_live_segment_days, 1)}d</td>
                      <td style={th()}>{r.switches_final} / {r.switches_live}</td>
                      <td style={th()}>{fmt(r.violation_pct, 1)}%</td>
                      <td style={th()} className={r.passed ? 'pos' : 'neg'}>{r.passed ? '✓' : '✗'}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <SelectionNote result={result} testId="ema-compare-selection-note">
            {result.best_period && setEngineConfig && (
              <button className="opt-chip" onClick={() => applyBest(result)} data-testid="ema-compare-apply">
                Beste Periode ({result.best_period}d) erneut übernehmen
              </button>
            )}
          </SelectionNote>
        </div>
      )}
    </div>
  );
}

// ---------------- Kombi-Detektor: Auto-Kalibrierung ----------------
export function KombiAutoCalibrate({ selCoins, timeframe, days, trainPct, engineConfig,
  setEngineConfig, jobBlocked, onQueued, onStarted, onResult }) {
  const body = { symbols: selCoins, timeframe, days, train_pct: trainPct, engine_config: engineConfig || {} };
  const [result, setResult] = useState(null);
  const [banner, setBanner] = useState(null);

  const applyBest = (res) => {
    if (!res?.best_config || !res.best) return;
    const prevThr = engineConfig?.kombi_thr;
    const prevSlope = engineConfig?.kombi_slope_days;
    const same = prevThr === res.best.thr && prevSlope === res.best.slope_days;
    setEngineConfig({ ...(engineConfig || {}), ...res.best_config });
    setBanner({
      improved: !same,
      title: same
        ? 'Deine Kombi-Einstellung ist bereits die beste der geprüften Kombinationen'
        : `Besserer Edge gefunden & übernommen: Schwelle ${res.best.thr} · Fenster ${res.best.slope_days}d`,
      detail: `Holdout-Trefferquote ${fmt(res.best.holdout_direction_pct, 1)}% · Ø Phase ${fmt(res.best.avg_final_segment_days, 1)}d`
        + `${res.best.in_target ? ' (im 5–15d-Zielband)' : ' (⚠ außerhalb 5–15d)'} · ${res.combos} Kombinationen geprüft`
        + ' · Detektor auf \'kombi\' gestellt – jetzt oben „Regime suchen & speichern“ starten',
    });
  };

  const { start, running } = useLabJob({
    errorLabel: 'Kalibrierung fehlgeschlagen', onStarted,
    onDone: (res) => { if (res?.rows) { setResult(res); applyBest(res); onResult?.(res); } },
  });

  const run = () => {
    setResult(null); setBanner(null);
    start('/api/regime-lab/kombi-calibrate', body, { requireCoins: true, kind: 'kombi_calibrate' });
  };

  const isBest = (r) => result?.best && r.thr === result.best.thr && r.slope_days === result.best.slope_days;

  return (
    <div className="opt-row rl-tool" data-testid="kombi-calibrate-section">
      <div className="opt-label">B · AUTO-KALIBRIERUNG – NUR FÜR GRUNDGERÜST „KOMBI“</div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        <b>Was ist das?</b> Eine Raster-Suche über Trend-Schwelle × Steigungs-Fenster des Kombi-Detektors
        (alle Kombinationen, feste Anzahl Runden – kein „bis es gut ist“). Bewertet wird die Holdout-Trefferquote
        (Live=Final, ohne Zukunftswissen) minus Strafe, wenn die Ø Phasendauer das 5–15-Tage-Zielband verlässt.
        <b> Das beste Ergebnis wird automatisch übernommen.</b> Unterschied zu „Wissenschaftlich kalibrieren“:
        dort wird gegen eine Referenz (Rückblick/HMM) gemessen, hier gegen die eigene Live=Final-Kennzahl.
      </div>
      <div className="opt-setup" style={{ alignItems: 'center' }}>
        <button className="opt-chip" onClick={run} disabled={running || jobBlocked} data-testid="kombi-calibrate-run">
          <Play size={11} weight="fill" /> Auto-Kalibrierung starten
        </button>
        <QueueButton kind="regime_kombi" body={body} onQueued={onQueued} testId="kombi-calibrate-queue" />
      </div>
      <RunningHint running={running} testId="kombi-calibrate-running" />
      {banner && <EdgeBanner {...banner} testId="kombi-calibrate-banner" />}
      {result && (
        <div style={{ overflowX: 'auto', marginTop: 6 }}>
          <div className="opt-setup" style={{ alignItems: 'center', marginBottom: 4 }}>
            {result.best ? (
              <>
                <span className="opt-small" data-testid="kombi-calibrate-best">
                  Bestes Ergebnis: Schwelle <b>{result.best.thr}</b> · Fenster <b>{result.best.slope_days}d</b> ·
                  Ø Phase <b>{fmt(result.best.avg_final_segment_days, 1)}d</b>
                  {result.best.in_target ? ' ✓ im Zielband' : ' ⚠ außerhalb 5–15d'} ·
                  Holdout <b>{fmt(result.best.holdout_direction_pct, 1)}%</b>
                </span>
                <button className="opt-chip" onClick={() => applyBest(result)} data-testid="kombi-calibrate-apply">
                  Beste Werte erneut übernehmen
                </button>
              </>
            ) : <span className="opt-small">Kein bewertbares Ergebnis</span>}
          </div>
          <table className="rl-compare-table" data-testid="kombi-calibrate-table"
            style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: 640 }}>
            <thead>
              <tr style={{ textAlign: 'left', opacity: 0.7 }}>
                <th style={th({ paddingLeft: 0 })}>Schwelle</th>
                <th style={th()}>Fenster</th>
                <th style={th()} title="Durchschnittliche Phasendauer der finalen Sicht – Ziel: 5-15 Tage">Ø Phase final</th>
                <th style={th()} title="Liegt die Phasendauer im 5-15-Tage-Zielband?">Zielband</th>
                <th style={th()} title="Nur im Holdout (Walk-Forward-Zeitraum) – die ehrlichste Kennzahl">Holdout</th>
                <th style={th()} title="Anteil der Kerzen, bei denen die Live-Sicht dieselbe Richtung sieht wie die finale Sicht">Live=Final</th>
                <th style={th()} title="Nur auf Trend-Kerzen (Auf/Ab)">Trend-Treffer</th>
                <th style={th()}>Wechsel (final/live)</th>
                <th style={th()} title="Holdout-Trefferquote minus 4 Punkte je Tag außerhalb des Zielbands">Score</th>
              </tr>
            </thead>
            <tbody>
              {result.rows.slice(0, 12).map((r, idx) => (
                <tr key={`${r.thr}-${r.slope_days}`} data-testid={`kombi-calibrate-row-${idx}`}
                  style={isBest(r) ? BEST_BG : undefined}>
                  <td style={th({ paddingLeft: 0 })}><b>{r.thr}</b>{isBest(r) ? ' ★' : ''}</td>
                  {r.error ? <td colSpan={8} className="neg">{r.error}</td> : (
                    <>
                      <td style={th()}>{r.slope_days}d</td>
                      <td style={th()}><b>{fmt(r.avg_final_segment_days, 1)}d</b></td>
                      <td style={th()} className={r.in_target ? 'pos' : 'neg'}>{r.in_target ? '✓' : '✗'}</td>
                      <td style={th()}><b>{fmt(r.holdout_direction_pct, 1)}%</b></td>
                      <td style={th()}>{fmt(r.direction_pct, 1)}%</td>
                      <td style={th()}>{fmt(r.trend_hit_pct, 1)}%</td>
                      <td style={th()}>{r.switches_final} / {r.switches_live}</td>
                      <td style={th()}>{fmt(r.score, 2)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="opt-small" style={{ marginTop: 4 }}>
            ★ = bester Score ({result.combos} Kombinationen geprüft, Top 12 angezeigt) – bereits in den
            Engine-Einstellungen (Detektor „kombi“). Danach die Analyse oben neu starten.
          </div>
        </div>
      )}
    </div>
  );
}
