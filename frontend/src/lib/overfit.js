/** Überanpassung im Autopilot-Ergebnis erkennen (rein): Score gestiegen,
 *  Abschlusstest (Holdout) gefallen. Funktioniert auch für Alt-Läufe. */
export function overfitInfo(res) {
  const best = res?.best;
  const base = res?.baseline;
  if (!best || !base) return null;
  const m = best.metrics || {};
  const b = base.metrics || {};
  const useRef = m.holdout_reference_f1_pct != null && b.holdout_reference_f1_pct != null;
  const key = useRef ? 'holdout_reference_f1_pct' : 'holdout_direction_pct';
  const ho0 = b[key];
  const ho1 = m[key];
  if (ho0 == null || ho1 == null || best.score == null || base.score == null) return null;
  const gain = Number(best.score) - Number(base.score);
  const drop = Number(ho0) - Number(ho1);
  if (!(gain > 0.05) || !(drop > 0.05)) return null;
  const tested = Number(res.tested || 0);
  return {
    gain, drop, ho0: Number(ho0), ho1: Number(ho1), tested,
    scoreFrom: Number(base.score), scoreTo: Number(best.score),
    metric: useRef ? 'Holdout-F1 (Referenz v2)' : 'Live=Final im Holdout',
    severe: drop >= 5 || tested >= 1000,
  };
}
