/**
 * Übernahme-Regel für Kalibrierungen (rein, testbar):
 * Ein neues Kalibrierungs-Ergebnis wird nur übernommen, wenn es für DASSELBE
 * Grundgerüst besser (oder gleich gut) ist als die aktuell aktive Kalibrierung.
 * Sonst bleibt die aktive Kalibrierung stehen – das Ergebnis liegt weiterhin im
 * Verlauf und kann dort bewusst übernommen werden.
 */
export const calibPct = (report) => {
  const v = report?.best?.balanced_direction_pct;
  return v === null || v === undefined ? null : Number(v);
};

export function calibrationDecision(current, report, detector) {
  const newPct = calibPct(report);
  if (!report?.best_config) return { adopt: false, reason: 'no_config', newPct, curPct: null };
  const sameDet = current && (current.detector || 'regression') === (report.detector || detector || 'regression');
  const curPct = sameDet && current.best_pct !== null && current.best_pct !== undefined
    ? Number(current.best_pct) : null;
  if (curPct === null || newPct === null) return { adopt: true, reason: 'first', newPct, curPct };
  if (newPct + 1e-9 >= curPct) return { adopt: true, reason: 'better', newPct, curPct };
  return {
    adopt: false, reason: 'worse', newPct, curPct,
    sameTruth: !current.truth_source || !report.truth_source || current.truth_source === report.truth_source,
  };
}
