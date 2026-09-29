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
 * - Regime-Autopilot: Macro-F1 gegen die unabhängige Referenz v2 auf dem Holdout
 *   (Live=Final nur noch als Rückfall für Alt-Läufe ohne Referenz v2 – Live=Final
 *   misst Selbst-Übereinstimmung und würde träge Detektoren bevorzugen)
 */
export const METRIC_CALIBRATION = 'balanced_direction_pct';
export const METRIC_AUTOPILOT = 'holdout_direction_pct';
export const METRIC_AUTOPILOT_REF = 'holdout_reference_f1_pct';

export const METRIC_LABELS = {
  [METRIC_CALIBRATION]: 'Treffer (Referenz)',
  [METRIC_AUTOPILOT]: 'Holdout Live=Final',
  [METRIC_AUTOPILOT_REF]: 'Holdout Referenz-F1',
};

const hasNum = (v) => v !== null && v !== undefined && !Number.isNaN(Number(v));
/** Autopilot-Metrik: Referenz-F1, wenn Bestwert UND Ausgangslage sie haben. */
export const autopilotMetric = (best, baseline) => (
  hasNum(best?.[METRIC_AUTOPILOT_REF]) && hasNum(baseline?.[METRIC_AUTOPILOT_REF])
    ? METRIC_AUTOPILOT_REF : METRIC_AUTOPILOT);

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
  // Timeframe-Regel: Kalibrierungen sind timeframe-spezifisch (1d-Werte sagen
  // nichts über 4h aus). Anderes Zeitfenster => nicht vergleichbar, das neue
  // Ergebnis zählt für SEINEN Timeframe wie eine Erst-Kalibrierung.
  if (sameDet && current?.timeframe && report?.timeframe
      && String(current.timeframe) !== String(report.timeframe)) {
    return { adopt: true, reason: 'other_timeframe', newPct, curPct: null };
  }
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
    metric: autopilotMetric(best.metrics, result.baseline?.metrics),
    truth_source: autopilotMetric(best.metrics, result.baseline?.metrics) === METRIC_AUTOPILOT_REF
      ? 'reference_v2' : 'live_final',
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
