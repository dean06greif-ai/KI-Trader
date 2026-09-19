import React, { useEffect, useState } from 'react';
import { Play, MoonStars, Robot, StopCircle } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { addToSeries } from '../lib/series';
import { EdgeBanner } from './RegimeJobProgress';
import NumInput from './NumInput';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const DET = { reactive: 'Umkehrpunkte (reactive)', ema: 'EMA-Steigung (ema)', kombi: 'Kombi', regression: 'Regression' };
const STOP = { stopped_by_user: 'per „Suche beenden“ gestoppt', time_limit: 'Zeitlimit erreicht',
  target_reached: 'Ziel-Trefferquote erreicht', rounds_limit: 'Runden-Limit erreicht' };
const SETTINGS_KEY = 'regime_autopilot_v1';
const loadSettings = () => { try { return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}; } catch { return {}; } };

/** Kompakte Kennzahlen-Zeile einer Autopilot-Konfiguration. */
function BestLine({ best, testId }) {
  const m = best?.metrics || {};
  return (
    <span className="opt-small" data-testid={testId}>
      <b>{DET[best?.detector] || best?.detector}</b> · Score <b>{fmt(best?.score)}</b>
      {best?.baseline_score != null && ` (Start ${fmt(best.baseline_score)})`} ·
      innere Val. <b>{fmt(m.inner_direction_pct)}%</b> · Holdout <b>{fmt(m.holdout_direction_pct)}%</b> ·
      Trend-Treffer {fmt(m.trend_hit_pct)}% · Ø Live-Phase <b>{fmt(m.avg_live_phase_days)}d</b> ·
      {' '}{Object.keys(best?.changes || {}).length} Parameter geändert
    </span>
  );
}

/**
 * Regime-Autopilot: Endlos-Suche nach der besten Regime-Erkennung für die
 * gewählten Assets (analog zur Endlos-Suche im Strategie-Optimizer). Läuft
 * Cloud oder lokal; das Beste wird automatisch in die Engine-Einstellungen
 * übernommen, danach „Regime suchen & speichern“ starten.
 */
export default function RegimeAutopilot({ selCoins, timeframe, days, trainPct, engineConfig, setEngineConfig,
  setCalibApplied, execution, lwOnline, jobBlocked, activeJob, lastResult, onStarted, onQueued }) {
  const saved = loadSettings();
  const [maxMin, setMaxMin] = useState(saved.maxMin ?? 0);
  const [targetPct, setTargetPct] = useState(saved.targetPct ?? 0);
  const [maxRounds, setMaxRounds] = useState(saved.maxRounds ?? 0);
  const [minPhase, setMinPhase] = useState(saved.minPhase ?? 3);
  const [searchDet, setSearchDet] = useState(saved.searchDet ?? true);
  const [banner, setBanner] = useState(null);
  const [runs, setRuns] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [appliedId, setAppliedId] = useState(null);

  useEffect(() => {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify({ maxMin, targetPct, maxRounds, minPhase, searchDet })); } catch { /* quota */ }
  }, [maxMin, targetPct, maxRounds, minPhase, searchDet]);

  const loadRuns = () => fetch(`${API_URL}/api/regime-lab/autopilot/runs`).then(r => r.json())
    .then(d => setRuns(d.runs || [])).catch(() => {});
  useEffect(() => { loadRuns(); }, [lastResult]);

  const applyBest = (res, id, source) => {
    const best = res?.best;
    if (!best?.engine_config) return false;
    setEngineConfig({ ...(engineConfig || {}), ...best.engine_config });
    setCalibApplied?.({
      id, detector: best.detector, source,
      baseline_pct: res.baseline?.metrics?.holdout_direction_pct, best_pct: best.metrics?.holdout_direction_pct,
      symbols: res.symbols || selCoins, timeframe: res.timeframe || timeframe,
      truth_source: 'live_final', applied_at: new Date().toISOString(),
    });
    setAppliedId(id || null);
    return true;
  };

  // Fertiger Lauf (aus dem Haupt-Balken gemeldet): Bestes automatisch übernehmen
  useEffect(() => {
    if (!lastResult?.best) return;
    const improved = !!lastResult.improved;
    if (improved) applyBest(lastResult, lastResult.job_id, 'Regime-Autopilot');
    const m = lastResult.best.metrics || {};
    const b = lastResult.baseline?.metrics || {};
    setBanner({
      improved,
      title: improved
        ? `Autopilot: bessere Erkennung gefunden & übernommen (${DET[lastResult.best.detector] || lastResult.best.detector})`
        : 'Autopilot: keine bessere Erkennung als deine Ausgangswerte gefunden – Einstellungen bleiben',
      detail: `${lastResult.tested} Varianten · ${lastResult.improvements} Verbesserungen · innere Val. ${fmt(b.inner_direction_pct)}% → ${fmt(m.inner_direction_pct)}%`
        + ` · Holdout ${fmt(b.holdout_direction_pct)}% → ${fmt(m.holdout_direction_pct)}% · Ø Phase ${fmt(m.avg_live_phase_days)}d`
        + ` · Ende: ${STOP[lastResult.stop_reason] || lastResult.stop_reason || '–'}`
        + (lastResult.evidence === 'insufficient_evidence' ? ' · ⚠ zu wenig Holdout-Daten' : '')
        + (improved ? ' – jetzt oben „Regime suchen & speichern“ starten' : ''),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastResult]);

  const buildBody = () => ({
    symbols: selCoins, timeframe, days, train_pct: trainPct, engine_config: engineConfig || {},
    max_minutes: maxMin, target_pct: targetPct, max_rounds: maxRounds,
    min_phase_days_target: minPhase, search_detectors: searchDet, execution,
  });

  const validate = () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return false; }
    if (!selCoins.length) { toast.error('Mindestens 1 Coin wählen'); return false; }
    return true;
  };

  const start = async () => {
    if (!validate()) return;
    if (execution === 'local' && !lwOnline) { toast.error('Kein lokaler Worker verbunden – Worker starten oder Cloud wählen'); return; }
    setBanner(null);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/autopilot`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(buildBody()),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
      onStarted?.(d.job_id, 'autopilot');
      toast.success(`Regime-Autopilot gestartet (${d.execution === 'local' ? 'lokaler Worker' : 'Cloud'})`);
    } catch { toast.error('Verbindungsfehler'); }
  };

  const queue = async () => {
    if (!validate()) return;
    const item = await addToSeries('regime_autopilot', buildBody());
    if (item) onQueued?.();
  };

  const softStop = async () => {
    if (!activeJob?.id) return;
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/autopilot/stop/${activeJob.id}`, { method: 'POST', headers: authHeaders() });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Stop fehlgeschlagen'); return; }
      toast.info('Autopilot wird beendet – beste Erkennung bleibt erhalten');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const running = activeJob?.status === 'running' && activeJob?.kind === 'autopilot';
  return (
    <div className="opt-row rl-tool" data-testid="autopilot-section">
      <div className="opt-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Robot size={13} weight="bold" /> D · REGIME-AUTOPILOT – ENDLOS-SUCHE DER BESTEN ERKENNUNG (WENN DU UNSICHER BIST)
      </div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        <b>Was passiert?</b> Wie die Endlos-Suche im Strategie-Optimizer: der Autopilot verändert die Erkennungs-Einstellungen
        Runde für Runde (Schwellen, Fenster, Bestätigungen – optional auch das Grundgerüst), bewertet jede Variante mit der
        Live=Final-Kennzahl und behält die beste. Auswahl auf <b>innerer Validierung + Trainingsfenster</b>, der Holdout bleibt unangetasteter finaler Test.
        <b> Wirtschaftlich:</b> zu kurze Live-Phasen (nicht handelbar) werden bestraft. Läuft im Hintergrund (Cloud oder lokaler
        Worker, auch über Nacht) – <b>das Beste wird automatisch übernommen</b>, danach „Regime suchen &amp; speichern“ starten.
      </div>
      <div className="opt-setup" style={{ alignItems: 'center' }}>
        <label className="opt-field" title="Sicherheits-Zeitlimit in Minuten. 0 = unbegrenzt (bis Ziel/Runden oder „Suche beenden“)">
          Zeitlimit (Min · 0 = ∞)
          <NumInput int min={0} max={1440} value={maxMin} onCommit={(v) => setMaxMin(v || 0)}
            data-testid="autopilot-max-minutes" style={{ width: 60 }} />
        </label>
        <label className="opt-field" title="Stoppt, sobald der Auswahl-Score (innere Validierung minus Phasen-Strafe) diesen Wert erreicht. 0 = aus">
          Ziel-Score % (0 = aus)
          <NumInput int min={0} max={100} value={targetPct} onCommit={(v) => setTargetPct(v || 0)}
            data-testid="autopilot-target-pct" style={{ width: 55 }} />
        </label>
        <label className="opt-field" title="Maximale Anzahl getesteter Varianten. 0 = endlos">
          Max. Runden (0 = ∞)
          <NumInput int min={0} max={100000} value={maxRounds} onCommit={(v) => setMaxRounds(v || 0)}
            data-testid="autopilot-max-rounds" style={{ width: 65 }} />
        </label>
        <label className="opt-field" title="Wirtschaftlichkeit: liegt die Ø Live-Phasendauer darunter, gibt es Strafpunkte (bis 15) – so gewinnt die handelbar-treffsichere Erkennung, nicht die flatterhafteste">
          Min. Ø Phase (Tage)
          <NumInput min={0} max={30} step={0.5} value={minPhase} onCommit={(v) => setMinPhase(v ?? 3)}
            data-testid="autopilot-min-phase" style={{ width: 55 }} />
        </label>
        <label className="opt-check" title="Auch andere Grundgerüste (Umkehrpunkte / EMA-Steigung / Kombi) ausprobieren, nicht nur die Feineinstellungen des aktuellen">
          <input type="checkbox" checked={searchDet} onChange={e => setSearchDet(e.target.checked)}
            data-testid="autopilot-search-detectors" />
          {' '}Grundgerüste mitsuchen
        </label>
        {!running ? (
          <button className="opt-run" onClick={start} disabled={jobBlocked} data-testid="autopilot-start"
            title="Startet die Endlos-Suche mit den oben gewählten Coins, Timeframe, Zeitraum und Training-% (Ausführung wie oben gewählt)">
            <Play size={13} weight="fill" /> Autopilot starten
          </button>
        ) : (
          <button className="opt-chip" onClick={softStop} data-testid="autopilot-stop"
            title="Suche sanft beenden – die bisher beste Erkennung wird als Ergebnis übernommen">
            <StopCircle size={13} weight="fill" /> Suche beenden (Bestes behalten)
          </button>
        )}
        <button className="opt-chip" onClick={queue} data-testid="autopilot-add-series"
          title="In die Regime-Lab-Warteschlange einreihen – läuft automatisch nach den anderen Jobs">
          <MoonStars size={13} /> + Warteschlange
        </button>
      </div>
      {running && activeJob?.best && (
        <div className="opt-small" style={{ marginTop: 4 }} data-testid="autopilot-live-best">
          Aktuell Bestes: <BestLine best={activeJob.best} testId="autopilot-live-best-line" />
        </div>
      )}
      {banner && <EdgeBanner {...banner} testId="autopilot-banner" />}
      {lastResult?.best && (
        <div className="opt-setup" style={{ alignItems: 'center', marginTop: 4 }}>
          <BestLine best={lastResult.best} testId="autopilot-result-best" />
          <button className="opt-chip" onClick={() => applyBest(lastResult, lastResult.job_id, 'Regime-Autopilot') && toast.success('Beste Erkennung übernommen – jetzt „Regime suchen & speichern“ starten')}
            data-testid="autopilot-apply">Beste Erkennung erneut übernehmen</button>
        </div>
      )}
      {runs.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <button className="opt-chip" style={{ fontSize: 10 }} onClick={() => setShowHistory(v => !v)} data-testid="autopilot-history-toggle">
            {showHistory ? '▾' : '▸'} Autopilot-Verlauf ({runs.length})
          </button>
          {showHistory && (
            <table className="rl-compare-table" data-testid="autopilot-history" style={{ marginTop: 4 }}>
              <thead>
                <tr><th>Datum</th><th>Coins</th><th>Grundgerüst</th><th>Innere Val.</th><th>Holdout</th><th>Ø Phase</th><th>Varianten</th><th></th></tr>
              </thead>
              <tbody>
                {runs.map(r => {
                  const res = r.result || {};
                  const m = res.best?.metrics || {};
                  return (
                    <tr key={r.id} data-testid={`autopilot-run-${r.id}`} style={appliedId === r.id ? { background: 'rgba(80,200,120,0.12)' } : undefined}>
                      <td>{new Date(r.created_at).toLocaleString('de-DE')}</td>
                      <td>{(res.symbols || []).map(s => s.replace('USDT', '')).join(', ')} · {res.timeframe} · {res.days}d</td>
                      <td>{DET[res.best?.detector] || res.best?.detector}</td>
                      <td>{fmt(m.inner_direction_pct)}%</td>
                      <td>{fmt(m.holdout_direction_pct)}%</td>
                      <td>{fmt(m.avg_live_phase_days)}d</td>
                      <td>{res.tested} · {res.improvements} ↑</td>
                      <td>
                        <button className="opt-chip" data-testid={`autopilot-run-apply-${r.id}`}
                          onClick={() => applyBest(res, r.id, 'Autopilot-Verlauf') && toast.success('Erkennung aus dem Verlauf übernommen')}>
                          übernehmen
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
