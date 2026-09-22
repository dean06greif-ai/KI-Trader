import React from 'react';
import { fmtDateTime } from '../lib/time';
import { metricLabel } from '../lib/regimeCalibration';

export const DETECTOR_LABELS = {
  reactive: 'Umkehrpunkte (Standard)',
  ema: 'EMA-Steigung',
  kombi: 'Kombi (EMA + Umkehrpunkte)',
  regression: 'Regression (alt)',
};

const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

function Step({ n, title, state, children, testId }) {
  return (
    <div className={`rl-step ${state}`} data-testid={testId}>
      <div className="rl-step-head">
        <span className="rl-step-num">{n}</span>
        <b>{title}</b>
      </div>
      <div className="rl-step-body">{children}</div>
    </div>
  );
}

/**
 * „Wo stehe ich gerade?“ – ein Blick zeigt: gewähltes Grundgerüst (Detektor),
 * ob/welche Kalibrierung aktiv ist, wie viele Analysen es gibt und wie gut die
 * ausgewählte Analyse abschneidet. Immer sichtbar, oben im Regime-Lab.
 */
export default function RegimeLabSummary({ engine, engineConfig, calibApplied, analysesCount,
  detail, trainPct, jobRunning }) {
  const detector = engine === 'kmeans' ? 'kmeans' : String(engineConfig?.detector || 'reactive');
  const detLabel = engine === 'kmeans' ? 'Cluster (K-Means, alt)' : (DETECTOR_LABELS[detector] || detector);
  const mode = engineConfig?.regime_mode || 5;
  const calMatches = calibApplied && calibApplied.detector === detector;
  const quality = detail?.quality ? Object.values(detail.quality)[0] : null;
  const focus = quality ? (Object.values(quality.classes || {}).length === 1
    ? Object.values(quality.classes)[0] : quality.overall) : null;

  return (
    <div className="rl-steps" data-testid="regime-lab-summary">
      <Step n="1" title="Grundgerüst" state="on" testId="regime-summary-detector">
        <span data-testid="regime-summary-detector-label"><b>{detLabel}</b></span>
        {engine !== 'kmeans' && <span className="opt-small"> · {mode} Regime</span>}
        <div className="opt-small">Das ist die Erkennungs-Methode, die kalibriert und analysiert wird.</div>
      </Step>
      <Step n="2" title="Kalibrierung" state={calMatches ? 'on' : (calibApplied ? 'warn' : 'todo')}
        testId="regime-summary-calibration">
        {calMatches ? (
          <>
            <span data-testid="regime-summary-calibration-active">
              <b>aktiv</b> · {metricLabel(calibApplied)} {fmt(calibApplied.baseline_pct)}% → <b>{fmt(calibApplied.best_pct)}%</b>
            </span>
            <div className="opt-small">
              {calibApplied.symbols?.map(s => s.replace('USDT', '')).join(', ')} · {calibApplied.timeframe}
              {' '}· übernommen {fmtDateTime(calibApplied.applied_at)}
              {calibApplied.source ? ` · via ${calibApplied.source}` : ''}
            </div>
          </>
        ) : calibApplied ? (
          <>
            <span><b>passt nicht</b> zum Grundgerüst</span>
            <div className="opt-small">
              Letzte Kalibrierung war für „{DETECTOR_LABELS[calibApplied.detector] || calibApplied.detector}“ –
              für „{detLabel}“ neu kalibrieren.
            </div>
          </>
        ) : (
          <>
            <span><b>noch nicht kalibriert</b> (optional, empfohlen)</span>
            <div className="opt-small">„Wissenschaftlich kalibrieren“ sucht automatisch die besten Feinwerte und übernimmt sie.</div>
          </>
        )}
      </Step>
      <Step n="3" title="Analyse" state={jobRunning ? 'warn' : (analysesCount ? 'on' : 'todo')}
        testId="regime-summary-analysis">
        <span><b>{analysesCount ?? '–'}</b> gespeichert{jobRunning ? ' · Job läuft' : ''}</span>
        <div className="opt-small">
          Training {trainPct}% · Rest {100 - (trainPct || 0)}% = Holdout (Out-of-Sample), auf dem jede Qualitätsnote gemessen wird.
        </div>
      </Step>
      <Step n="4" title="Ergebnis" state={focus ? (focus.grade === 'gut' ? 'on' : focus.grade === 'mittel' ? 'warn' : 'bad') : 'todo'}
        testId="regime-summary-result">
        {focus ? (
          <>
            <span><b>{detail.name}</b> · Note <b data-testid="regime-summary-grade">{focus.grade}</b>
              {' '}· Holdout Live=Final <b>{fmt(focus.pct)}%</b></span>
            <div className="opt-small">Details & Grafik unten unter „Regime-Insights“.</div>
          </>
        ) : (
          <>
            <span>keine Analyse geöffnet</span>
            <div className="opt-small">Unter „Gespeicherte Analysen“ eine Zeile anklicken.</div>
          </>
        )}
      </Step>
    </div>
  );
}
