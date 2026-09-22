/**
 * Übernahme-Regeln für Kalibrierungen (rein, testbar).
 *
 * Grundsatz: eine Einstellung wird nur AUTOMATISCH übernommen, wenn sie für
 * DASSELBE Grundgerüst nicht schlechter ist als die aktive – sonst bleibt die
 * aktive stehen. Das Ergebnis liegt trotzdem im Kalibrierungs-Verlauf und kann
 * dort jederzeit bewusst (manuell) übernommen werden.
 *
 * Zwei Quellen, zwei Metriken (werden nie stillschweigend vermischt):
 * - Wissenschaftliche Kalibrierung: balancierter Richtungs-Treffer gegen die Referenz
 * - Regime-Autopilot: Live=Final-Treffer auf dem Holdout
 */
export const METRIC_CALIBRATION = 'balanced_direction_pct';
export const METRIC_AUTOPILOT = 'holdout_direction_pct';

export const METRIC_LABELS = {
  [METRIC_CALIBRATION]: 'Treffer (Referenz)',
  [METRIC_AUTOPILOT]: 'Holdout Live=Final',
};

export const metricOf = (reportOrCalib) => reportOrCalib?.metric || METRIC_CALIBRATION;
export const metricLabel = (reportOrCalib) => METRIC_LABELS[metricOf(reportOrCalib)] || metricOf(reportOrCalib);

const num = (v) => (v === null || v === undefined ? null : Number(v));

export const calibPct = (report) => num(report?.best?.[metricOf(report)]);
export const calibBaselinePct = (report) => num(report?.baseline?.[metricOf(report)]);

/** Kalibrierungs-Bericht (beliebige Quelle) -> Eintrag „aktive Kalibrierung“. */
export function calibAppliedFromReport(report, id, source, ctx = {}) {
  return {
    id, detector: report?.detector || ctx.detector || 'regression', source,
    metric: metricOf(report),
    baseline_pct: calibBaselinePct(report), best_pct: calibPct(report),
    symbols: report?.symbols || ctx.symbols || [], timeframe: report?.timeframe || ctx.timeframe,
    truth_source: report?.truth_source, applied_at: new Date().toISOString(),
  };
}

export function calibrationDecision(current, report, detector) {
  const newPct = calibPct(report);
  if (!report?.best_config) return { adopt: false, reason: 'no_config', newPct, curPct: null };
  const sameDet = current && (current.detector || 'regression') === (report.detector || detector || 'regression');
  const sameMetric = sameDet && metricOf(current) === metricOf(report);
  const curPct = sameMetric && current.best_pct !== null && current.best_pct !== undefined
    ? Number(current.best_pct) : null;
  if (curPct === null || newPct === null) return { adopt: true, reason: 'first', newPct, curPct };
  if (newPct + 1e-9 >= curPct) return { adopt: true, reason: 'better', newPct, curPct };
  return {
    adopt: false, reason: 'worse', newPct, curPct,
    sameTruth: !current.truth_source || !report.truth_source || current.truth_source === report.truth_source,
  };
}

/**
 * Autopilot-Ergebnis -> Kalibrierungs-Bericht (gleiches Format wie der Verlauf).
 */
export function autopilotReport(result) {
  const best = result?.best || {};
  const cfg = result?.best_engine_config || best.engine_config;
  if (!cfg) return null;
  return {
    detector: best.detector || cfg.detector || 'reactive', best_config: cfg,
    baseline: result.baseline?.metrics || {}, best: best.metrics || {},
    improved: !!result.improved, holdout_regressed: !!result.holdout_regressed,
    metric: METRIC_AUTOPILOT, truth_source: 'live_final',
    symbols: result.symbols, timeframe: result.timeframe,
  };
}

/**
 * Darf das Autopilot-Ergebnis automatisch übernommen werden?
 * 1) nur bei Verbesserung des Auswahl-Scores,
 * 2) nie, wenn der Holdout gegenüber der Ausgangslage gefallen ist
 *    (Autopilot wählt auf innerer Validierung – der Holdout ist der faire Vergleich),
 * 3) und nie unter die aktive Kalibrierung desselben Grundgerüsts mit gleicher Metrik.
 */
export function autopilotDecision(current, result) {
  const report = autopilotReport(result);
  if (!report) return { adopt: false, reason: 'no_config', report: null };
  if (!result.improved) return { adopt: false, reason: 'not_improved', report };
  const basePct = calibBaselinePct(report);
  const newPct = calibPct(report);
  const regressed = result.holdout_regressed
    || (basePct !== null && newPct !== null && newPct + 1e-9 < basePct);
  if (regressed) return { adopt: false, reason: 'holdout_regressed', report, newPct, curPct: basePct };
  const d = calibrationDecision(current, report, report.detector);
  return { ...d, report };
}
