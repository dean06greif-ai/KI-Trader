import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Play, X, Repeat, ArrowRight, ArrowCounterClockwise, Clock, Brain, Cloud, Desktop, Gear } from '@phosphor-icons/react';
import LocalWorkerPanel from './LocalWorkerPanel';
import EventSetupSeeding, { EVENT_META, eventCell } from './EventSetupSeeding';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const CLASS_LABELS = { crypto: 'Krypto', indices: 'Indizes', resources: 'Rohstoffe', forex: 'Forex' };
const DAY_OPTIONS = [30, 60, 90, 180, 365];
const MODE_LABELS = { single: 'Einmal-Durchlauf', loop: 'Auto-Schleife', ai_loop: 'KI-Schleife' };
const STATUS = {
  passed: { l: 'Edge bestätigt', c: '#00FF66' },
  tuned: { l: 'Edge nach Feintuning/KI-Revision', c: '#00E5A0' },
  failed: { l: 'kein Edge → nächste Variante', c: '#FFB020' },
  exhausted: { l: 'alle Varianten + Feintuning ohne Edge', c: '#FF3366' },
  live: { l: 'schon live (übersprungen)', c: '#8FB3FF' },
  no_data: { l: 'keine Historie', c: '#FF3366' },
};
const STATE_KEY = 'ai_seed_ui_v1';
const fmtTs = (ts) => (ts ? String(ts).slice(0, 16).replace('T', ' ') : '—');
const money = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${Number(v ?? 0).toFixed(2)}`;
const cell = (st) => (st && st.trades ? `${st.trades}T · ${st.winrate}% · ${money(st.pnl)}` : '—');
const diagCell = (d) => {
  if (!d || !d.n) return null;
  const ex = Object.entries(d.exits || {}).map(([k, v]) => `${k} ${v}`).join(' · ');
  return `PF ${d.pf ?? '—'} · Payoff ${d.payoff ?? '—'} · DD ${money(-(d.max_dd ?? 0))} · Serie ${d.loss_streak ?? 0}${ex ? ` · Exits: ${ex}` : ''}`;
};
const lastLesson = (r) => (r.lessons || []).slice(-1)[0];
const loadSaved = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY) || '{}'); } catch { return {}; } };
const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() });

export default function AITraderSeeding() {
  const saved = loadSaved();
  const [info, setInfo] = useState(null);
  const [classes, setClasses] = useState(saved.classes || ['crypto']);
  const [days, setDays] = useState(saved.days || 90);
  const [mode, setMode] = useState(saved.mode || 'single');
  const [aiRevise, setAiRevise] = useState(saved.aiRevise ?? true);
  const [aiRounds, setAiRounds] = useState(saved.aiRounds || 3);
  const [targetPassed, setTargetPassed] = useState(saved.targetPassed || 3);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [lwOnline, setLwOnline] = useState(false);
  const [showLW, setShowLW] = useState(false);
  const [evState, setEvState] = useState(null);
  const handleEvState = useCallback((s) => setEvState(s), []);
  const pollRef = useRef(null);
  const eventRef = useRef(null);
  const admin = isAdmin();

  useEffect(() => {
    try { localStorage.setItem(STATE_KEY, JSON.stringify({ classes, days, mode, aiRevise, aiRounds, targetPassed })); } catch { /* ignore */ }
  }, [classes, days, mode, aiRevise, aiRounds, targetPassed]);

  const refreshInfo = () => fetch(`${API_URL}/api/ai/playbook/backtest`).then(r => r.json()).then(setInfo).catch(() => {});

  const load = () => fetch(`${API_URL}/api/ai/playbook/backtest`).then(r => r.json()).then(d => {
    setInfo(d);
    if (d.last_result && !result) setResult(d.last_result);
    if (d.active) { setJob(d.active); poll(d.active.id); }
  }).catch(() => {});

  const poll = (jobId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API_URL}/api/ai/playbook/backtest/status/${jobId}`);
        if (!r.ok) return;
        const j = await r.json();
        setJob(j);
        if (j.status !== 'running') {
          clearInterval(pollRef.current);
          if (j.status === 'done') { setResult(j.result); toast.success('KI-Trader Setup-Backtest abgeschlossen'); }
          else if (j.status === 'error') toast.error(`Setup-Backtest fehlgeschlagen: ${j.error}`);
          else toast.info('Setup-Backtest abgebrochen');
          refreshInfo();
        }
      } catch { /* transient */ }
    }, 1500);
  };

  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []);

  useEffect(() => {
    const check = () => fetch(`${API_URL}/api/localworker/status`).then(r => r.json())
      .then(d => setLwOnline(!!d.online)).catch(() => setLwOnline(false));
    check();
    const iv = setInterval(check, 10000);
    return () => clearInterval(iv);
  }, []);

  const toggleClass = (c) => setClasses(prev => (prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c]));

  const run = async () => {
    const evSel = eventRef.current?.getSelected() || [];
    if (!classes.length && !evSel.length) { toast.error('Mindestens 1 Anlageklasse oder Event-Setup wählen'); return; }
    if (evSel.length) {
      const ok = await eventRef.current.start({ aiRevise, aiRounds: mode === 'ai_loop' ? aiRounds : 2 });
      if (ok) toast.success(`Event-Setups laufen mit: ${evSel.map(e => e.toUpperCase()).join(', ')}`);
    }
    if (!classes.length) return; // nur Event-Setups gewählt
    const body = { asset_classes: classes, days, mode, ai_revise: aiRevise, ai_rounds: aiRounds, target_passed: targetPassed };
    const r = await fetch(`${API_URL}/api/ai/playbook/backtest/run`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
    setJob({ id: d.job_id, status: 'running', progress: 0, phase: 'Startet' });
    setResult(null);
    poll(d.job_id);
  };

  const cancel = () => job && fetch(`${API_URL}/api/ai/playbook/backtest/cancel/${job.id}`, { method: 'POST', headers: authHeaders() });

  const resetCls = async (cls) => {
    if (!window.confirm(`Setup-Backtest für ${CLASS_LABELS[cls]} zurücksetzen (Trades + Varianten-Stand + KI-Vorschläge)?`)) return;
    await fetch(`${API_URL}/api/ai/playbook/backtest/reset`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ asset_class: cls }) });
    toast.success('Zurückgesetzt');
    load();
  };

  const running = job?.status === 'running';
  // Gemeinsamer Fortschritt: Playbook-Setups + Event-Setups in EINEM Balken
  const evRunning = !!evState?.running;
  const evJob = evState?.job;
  const anyRunning = running || evRunning;
  const combinedProgress = running && evRunning
    ? Math.round(((job?.progress || 0) + (evJob?.progress || 0)) / 2)
    : running ? (job?.progress || 0) : (evJob?.progress || 0);
  const combinedPhase = [
    running ? job?.phase : null,
    evRunning ? `Events: ${evJob?.phase || '…'}` : null,
  ].filter(Boolean).join(' · ');
  const evEvents = evState?.events || {};
  const evClassMeta = evState?.assetClasses || [];
  const CLS_ORDER = ['crypto', 'indices', 'resources', 'forex'];
  const clsLabelOf = (c) => (evClassMeta.find(m => m.key === c)?.label) || c;
  const eventRows = [];
  Object.keys(EVENT_META).forEach(k => {
    const ev = evEvents[k];
    if (!ev) return;
    const bts = ev.backtests || (ev.backtest ? { crypto: ev.backtest } : {});
    CLS_ORDER.filter(c => bts[c]).forEach((c, idx) => {
      eventRows.push({ k, cls: c, ev, bt: bts[c], first: idx === 0 });
    });
  });
  const rules = info?.rules || {};
  const rows = (result?.rows || []).filter(r => r.setup);
  const auto = info?.auto;
  const pendingProposals = Object.entries(info?.classes || {}).flatMap(([cls, setups]) =>
    Object.entries(setups || {}).filter(([, e]) => e && e.ai_proposal).map(([sid, e]) => ({ cls, sid, meta: e.ai_proposal_meta || {} })));

  const saveAuto = async (patch) => {
    const r = await fetch(`${API_URL}/api/ai/playbook/backtest/auto`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ ...(auto || {}), ...patch }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast.error(d.detail || 'Speichern fehlgeschlagen'); return; }
    setInfo(prev => ({ ...(prev || {}), auto: d }));
    toast.success(d.enabled ? `Automatik aktiv – nächster Lauf ${fmtTs(d.next_run_at)}` : 'Automatik aus');
  };

  const toggleAutoClass = (c) => {
    const cur = auto?.asset_classes || [];
    const next = cur.includes(c) ? cur.filter(x => x !== c) : [...cur, c];
    if (!next.length) { toast.error('Mindestens eine Anlageklasse'); return; }
    saveAuto({ asset_classes: next });
  };

  return (
    <div data-testid="ai-seed-panel">
      <details className="bt-hint ai-seed-intro" data-testid="ai-seed-intro">
        <summary>So funktioniert der Setup-Backtest des KI Traders</summary>
        Der KI Trader testet seine Playbook-Setups (rückgestufte und Datensammel-Setups) regelbasiert auf Vergangenheitsdaten
        (5m · In-Sample {Math.round((rules.is_share || 0.7) * 100)}% wählt die Variante, Out-of-Sample bestätigt). Setups ohne Edge werden
        anschließend von der KI überarbeitet: sie bekommt je Satz eine Tiefen-Diagnose (Exit-Verteilung, Long/Short, Uhrzeit, Profit-Faktor,
        Drawdown, MFE/MAE, Equity-Kurve) und die Lernschleife ihrer bisherigen Revisionen (was geändert wurde, ob es besser/schlechter wurde) und darf
        neben den Basis-Parametern auch Teilgewinn, Zeit-Exit, Seite, Volatilitäts-Regime, Uhrzeit und 1h-Trend-Filter anpassen.
        Im Einmal-Durchlauf / der Auto-Schleife wird der Vorschlag für den nächsten Lauf vorgemerkt,
        die <b>KI-Schleife</b> testet ihn sofort weiter – bis das Ziel bestandener Setups erreicht ist oder die Runden aufgebraucht sind.
        Bestandene Setups zählen ×{rules.weight ?? 0.5} gedeckelt ({rules.max_backtest_weighted ?? 3} gewichtete Trades) fürs Reife-Gate –
        <b> Live erst nach {rules.min_real_trades ?? 2}+ profitablen echten Paper-Trades</b>.
        Nicht testbar: {Object.keys(info?.not_backtestable || {}).join(', ') || '—'}.
        <b> Mindest-Trades adaptiv</b> (Overfitting-Bremse): je Setup aus Anzahl Assets der Klasse × Zeitraum
        (z.B. Krypto {info?.min_trades?.crypto?.trend_follow ? `IS ≥ ${info.min_trades.crypto.trend_follow.is} / OOS ≥ ${info.min_trades.crypto.trend_follow.oos}` : '—'},
        Indizes {info?.min_trades?.indices?.trend_follow ? `IS ≥ ${info.min_trades.indices.trend_follow.is} / OOS ≥ ${info.min_trades.indices.trend_follow.oos}` : '—'} bei {rules.default_days ?? 90} Tagen);
        die KI darf das Ziel je Setup begründet nachjustieren (min_trades_factor 0,7–1,5).
        <b> Mindest-Trades adaptiv</b> (Overfitting-Bremse): je Setup aus Anzahl Assets der Klasse × Zeitraum
        (z.B. Krypto {info?.min_trades?.crypto?.trend_follow ? `IS ≥ ${info.min_trades.crypto.trend_follow.is} / OOS ≥ ${info.min_trades.crypto.trend_follow.oos}` : '—'},
        Indizes {info?.min_trades?.indices?.trend_follow ? `IS ≥ ${info.min_trades.indices.trend_follow.is} / OOS ≥ ${info.min_trades.indices.trend_follow.oos}` : '—'} bei {rules.default_days ?? 90} Tagen);
        die KI darf das Ziel je Setup begründet nachjustieren (min_trades_factor 0,7–1,5).
      </details>

      <div className="ai-seed-grid">
        <div className="bt-col">
          <div className="bt-label">ANLAGEKLASSEN <span className="btc-hint-inline">(getestet wird immer die ganze Klasse)</span></div>
          <div className="bt-chips" data-testid="ai-seed-classes">
            {Object.keys(CLASS_LABELS).map(c => (
              <button key={c} className={`bt-chip ${classes.includes(c) ? 'on' : ''}`} onClick={() => toggleClass(c)}
                data-testid={`ai-seed-class-${c}`}>
                {CLASS_LABELS[c]}
                {info?.eligible?.[c] && <span className="btc-tf-tag">{info.eligible[c].length} Setups</span>}
              </button>
            ))}
          </div>
          <div className="ai-seed-kv" data-testid="ai-seed-eligible">
            {classes.map(c => (
              <span key={c}><b>{CLASS_LABELS[c]}:</b> {(info?.eligible?.[c] || []).join(', ') || '—'}</span>
            ))}
          </div>
        </div>
        <div className="bt-col">
          <div className="bt-label">MODUS &amp; ZEITRAUM</div>
          <div className="bt-exec" data-testid="ai-seed-mode" style={{ flexWrap: 'wrap' }}>
            <button className={`bt-exec-btn ${mode === 'single' ? 'on' : ''}`} onClick={() => setMode('single')} data-testid="ai-seed-mode-single"
              title="Ein Durchlauf der aktuellen Variante je Setup; bei Misserfolg wird die nächste Variante bzw. der KI-Vorschlag für den nächsten Lauf vorgemerkt">
              <ArrowRight size={13} weight="bold" /> Einmal
            </button>
            <button className={`bt-exec-btn ${mode === 'loop' ? 'on' : ''}`} onClick={() => setMode('loop')} data-testid="ai-seed-mode-loop"
              title="Alle Varianten nacheinander + Feintuning, bis ein Setup Out-of-Sample besteht; KI-Vorschlag für den nächsten Lauf">
              <Repeat size={13} weight="bold" /> Auto-Schleife
            </button>
            <button className={`bt-exec-btn ${mode === 'ai_loop' ? 'on' : ''}`} onClick={() => setMode('ai_loop')} data-testid="ai-seed-mode-ai_loop"
              title="Wie Auto-Schleife, zusätzlich überarbeitet die KI jedes Setup ohne Edge sofort und testet den Vorschlag erneut – bis Ziel erreicht oder Runden aufgebraucht">
              <Brain size={13} weight="bold" /> KI-Schleife
            </button>
          </div>
          <div className="ai-seed-kv" data-testid="ai-seed-mode-hint">
            {mode === 'single' && <span>Ein Durchlauf je Setup; ohne Edge wird die nächste Variante bzw. der KI-Vorschlag für den nächsten Lauf vorgemerkt.</span>}
            {mode === 'loop' && <span>Alle Varianten nacheinander + Feintuning; ohne Edge KI-Vorschlag für den nächsten Lauf.</span>}
            {mode === 'ai_loop' && <span>Wie Auto-Schleife, zusätzlich überarbeitet die KI jedes Setup ohne Edge sofort und testet erneut – bis Ziel erreicht oder Runden aufgebraucht.</span>}
          </div>
        </div>
      </div>

      <EventSetupSeeding ref={eventRef} onState={handleEvState} />

      <div className="bt-params" data-testid="ai-seed-run-row">
        <label>Zeitraum
          <select value={days} onChange={e => setDays(Number(e.target.value))} data-testid="ai-seed-days">
            {DAY_OPTIONS.map(d => <option key={d} value={d}>{d} Tage</option>)}
          </select>
        </label>
        <label className="bt-check" title="Nach jedem Setup ohne Edge schlägt die KI einen überarbeiteten Parameter-Satz vor (Rolle Forschungs-Analyst)">
          <input type="checkbox" checked={aiRevise} onChange={e => setAiRevise(e.target.checked)} data-testid="ai-seed-ai-revise" />
          KI überarbeitet Setups ohne Edge
        </label>
        {mode === 'ai_loop' && (
          <>
            <label title="Maximale KI-Revisions-Runden je Setup">Max. Runden
              <select value={aiRounds} onChange={e => setAiRounds(Number(e.target.value))} data-testid="ai-seed-ai-rounds">
                {[1, 2, 3, 5, 8, 10].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <label title="Schleife endet je Anlageklasse, sobald so viele Setups bestanden haben">Ziel: Setups mit Edge
              <select value={targetPassed} onChange={e => setTargetPassed(Number(e.target.value))} data-testid="ai-seed-target">
                {[1, 2, 3, 4, 5, 6, 9].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          </>
        )}
        <div className="bt-exec" data-testid="ai-seed-execution-toggle">
          <span className="bt-exec-label">Ausführung</span>
          <button className="bt-exec-btn on" data-testid="ai-seed-exec-cloud" title="Berechnung auf dem Server">
            <Cloud size={13} weight="bold" /> Cloud
          </button>
          <button className="bt-exec-btn" disabled style={{ opacity: 0.55, cursor: 'not-allowed' }} data-testid="ai-seed-exec-local"
            title="Lokal noch nicht verfügbar: Der Setup-Backtest schreibt Playbook-Daten (Reife-Gate, Setup-Stand) direkt in die Datenbank und läuft deshalb serverseitig. Lokaler Worker: Marktdaten & Strategie-Backtests.">
            <Desktop size={13} weight="bold" /> Lokal
            <span className={`bt-exec-dot ${lwOnline ? 'on' : ''}`} data-testid="ai-seed-exec-dot" />
          </button>
          <button className="bt-exec-manage" onClick={() => setShowLW(true)} title="Lokale Ausführung verwalten: Worker, Einstellungen & Marktdaten"
            data-testid="ai-seed-exec-manage">
            <Gear size={13} weight="bold" />
          </button>
        </div>
        {admin ? (
          <button className="bt-run" onClick={run} disabled={anyRunning} data-testid="ai-seed-run">
            <Play size={15} weight="fill" /> {anyRunning ? 'Läuft...' : 'KI-Trader Backtest starten'}
          </button>
        ) : (
          <button className="bt-run" disabled data-testid="ai-seed-run-locked" title="Bitte oben rechts als Admin anmelden">
            <Play size={15} weight="fill" /> Admin-Login zum Starten erforderlich
          </button>
        )}
      </div>
      <div className="bt-tools" style={{ marginTop: -6, marginBottom: 10 }}>
        <span className="bt-ram" data-testid="ai-seed-summary-line">
          {classes.map(c => CLASS_LABELS[c]).join(', ') || 'keine Klasse'} · {days} Tage · {MODE_LABELS[mode]}
          {aiRevise ? (mode === 'ai_loop' ? ` · KI: max. ${aiRounds} Runden, Ziel ${targetPassed} Setups` : ' · KI-Vorschlag für nächsten Lauf') : ''}
        </span>
      </div>
      {showLW && <LocalWorkerPanel onClose={() => setShowLW(false)} />}

      {anyRunning && (
        <div className="bt-progress" data-testid="ai-seed-progress">
          <div className="bt-progress-bar"><div style={{ width: `${combinedProgress}%` }} /></div>
          <div className="bt-progress-row">
            <div className="bt-progress-text" data-testid="ai-seed-progress-text">{combinedPhase} · {combinedProgress}%</div>
            {running && (
              <button className="bt-cancel" onClick={cancel} title="Bricht den Setup-Backtest ab (laufende Event-Backtests laufen bis zum Abschluss)"
                data-testid="ai-seed-cancel"><X size={13} weight="bold" /> Abbrechen</button>
            )}
          </div>
        </div>
      )}

      {pendingProposals.length > 0 && !running && (
        <div className="bt-rule-warnings" data-testid="ai-seed-pending">
          <b>KI-Revisionen vorgemerkt ({pendingProposals.length})</b> – werden beim nächsten Lauf zuerst getestet:
          <ul>
            {pendingProposals.map(p => (
              <li key={`${p.cls}-${p.sid}`} data-testid={`ai-seed-pending-${p.cls}-${p.sid}`}>
                <span className="mono">{CLASS_LABELS[p.cls] || p.cls} · {p.sid}</span>
                {p.meta.version ? ` (KI-Rev.${p.meta.version}${p.meta.model ? `, ${p.meta.model}` : ''}${p.meta.base ? `, Basis ${p.meta.base}` : ''})` : ''}
                {p.meta.reason ? `: ${p.meta.reason}` : ''}
                {(p.meta.changes || []).length > 0 && (
                  <div className="mono" style={{ opacity: 0.8, fontSize: 11 }} data-testid={`ai-seed-pending-changes-${p.cls}-${p.sid}`}>
                    Änderung: {p.meta.changes.join(' · ')}{p.meta.expect ? ` — erwartet: ${p.meta.expect}` : ''}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {auto && (
        <div className="bt-tools" style={{ marginTop: 8, flexWrap: 'wrap', gap: 8 }} data-testid="ai-seed-auto">
          <label className="bt-ram" style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: admin ? 'pointer' : 'default' }}>
            <input type="checkbox" checked={!!auto.enabled} disabled={!admin} onChange={e => saveAuto({ enabled: e.target.checked })}
              data-testid="ai-seed-auto-toggle" />
            <Clock size={13} weight="bold" /> Automatik: regelmäßig ohne Klick
          </label>
          <select className="bt-select" value={auto.mode} disabled={!admin} onChange={e => saveAuto({ mode: e.target.value })} data-testid="ai-seed-auto-mode">
            {Object.entries(MODE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <span className="bt-ram">alle</span>
          <select className="bt-select" value={auto.interval_hours} disabled={!admin} onChange={e => saveAuto({ interval_hours: Number(e.target.value) })}
            data-testid="ai-seed-auto-interval">
            {[6, 12, 24, 48, 72, 168].map(h => <option key={h} value={h}>{h < 48 ? `${h} h` : `${h / 24} Tage`}</option>)}
          </select>
          <span className="bt-ram">Zeitraum</span>
          <select className="bt-select" value={auto.days} disabled={!admin} onChange={e => saveAuto({ days: Number(e.target.value) })} data-testid="ai-seed-auto-days">
            {DAY_OPTIONS.map(d => <option key={d} value={d}>{d} Tage</option>)}
          </select>
          {auto.mode === 'ai_loop' && (
            <>
              <span className="bt-ram">Runden</span>
              <select className="bt-select" value={auto.ai_rounds} disabled={!admin} onChange={e => saveAuto({ ai_rounds: Number(e.target.value) })} data-testid="ai-seed-auto-rounds">
                {[1, 2, 3, 5, 8, 10].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
              <span className="bt-ram">Ziel</span>
              <select className="bt-select" value={auto.target_passed} disabled={!admin} onChange={e => saveAuto({ target_passed: Number(e.target.value) })} data-testid="ai-seed-auto-target">
                {[1, 2, 3, 4, 5, 6, 9].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </>
          )}
          <span className="bt-ram">Klassen:</span>
          {Object.keys(CLASS_LABELS).map(c => (
            <button key={c} className={`bt-tool-btn ${(auto.asset_classes || []).includes(c) ? 'on' : ''}`}
              style={{ opacity: (auto.asset_classes || []).includes(c) ? 1 : 0.45 }}
              disabled={!admin} onClick={() => toggleAutoClass(c)} data-testid={`ai-seed-auto-class-${c}`}>
              {CLASS_LABELS[c]}
            </button>
          ))}
          <span className="bt-ram" data-testid="ai-seed-auto-next">
            {auto.enabled ? `nächster Lauf: ${fmtTs(auto.next_run_at)}` : 'Automatik aus'}
            {auto.last_run_at ? ` · letzter: ${fmtTs(auto.last_run_at)}${auto.last_summary ? ` (${auto.last_summary.passed ?? 0}/${auto.last_summary.tested ?? 0} mit Edge)` : ''}` : ''}
            {auto.last_error ? ` · Fehler: ${auto.last_error}` : ''}
          </span>
          {(auto.history || []).length > 0 && (
            <div style={{ width: '100%' }} data-testid="ai-seed-auto-history">
              <div className="btc-sub" style={{ marginTop: 6 }}>AUTOMATIK-VERLAUF (letzte {Math.min(10, auto.history.length)} Läufe)</div>
              <table className="bt-table" data-testid="ai-seed-auto-history-table">
                <thead><tr><th>Zeit</th><th>Modus</th><th>Zeitraum</th><th>Klassen</th><th title="Setups mit Edge / getestete Setups">Edge-Treffer</th><th>Status</th></tr></thead>
                <tbody>
                  {[...auto.history].reverse().map((h, i) => (
                    <tr key={h.at || i} data-testid={`ai-seed-auto-history-row-${i}`}>
                      <td className="mono">{fmtTs(h.at)}</td>
                      <td>{MODE_LABELS[h.mode] || h.mode}</td>
                      <td className="mono">{h.days} Tage</td>
                      <td>{(h.asset_classes || []).map(c => CLASS_LABELS[c] || c).join(', ')}</td>
                      <td className={`mono ${(h.passed || 0) > 0 ? 'pos' : ''}`}>{h.passed ?? 0}/{h.tested ?? 0}</td>
                      <td style={{ color: h.error ? '#FF3366' : '#00FF66' }}>{h.error ? `Fehler: ${h.error}` : 'ok'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {(result || eventRows.length > 0) && (
        <div style={{ marginTop: 10 }} data-testid="ai-seed-result">
          <div className="btc-sub">
            {result ? (
              <>ERGEBNIS · {MODE_LABELS[result.mode] || result.mode}{result.trigger === 'auto' ? ' (Automatik)' : ''} · {result.days} Tage ·
                {' '}{result.passed ?? 0}/{result.tested ?? 0} Setups mit Edge
                {result.ai_rounds ? ` · ${result.ai_rounds} KI-Revisionen` : ''}
                {result.ai_proposals ? ` · ${result.ai_proposals} für nächsten Lauf vorgemerkt` : ''}</>
            ) : 'ERGEBNIS'}
            {eventRows.length > 0 && (
              <> · Events: {eventRows.filter(r => r.bt?.validated).length}/{eventRows.length} Klassen-Backtests validiert</>
            )}
          </div>
          <div className="bt-table-wrap">
          <table className="bt-table" data-testid="ai-seed-table">
            <thead>
              <tr>
                <th>Klasse</th><th>Setup</th><th>Variante</th>
                <th title="In-Sample: Trades · Winrate · PnL (USDT je 100 Notional)">In-Sample</th>
                <th title="Out-of-Sample: Trades · Winrate · PnL">Out-of-Sample</th>
                <th title="Gespeicherte OOS-Trades fürs Reife-Gate">Gespeichert</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={`${r.asset_class}-${r.setup}`} data-testid={`ai-seed-row-${r.asset_class}-${r.setup}`}>
                  <td>{CLASS_LABELS[r.asset_class] || r.asset_class}</td>
                  <td className="bt-name">{r.setup}</td>
                  <td className="mono">{r.variant ? `${r.variant} (${(r.variant_idx ?? 0) + 1}/${r.variants_total})${r.tried > 1 ? ` · ${r.tried} getestet` : ''}${r.ai_rounds ? ` · ${r.ai_rounds}× KI` : ''}` : '—'}</td>
                  <td className="mono" title={r.min_trades ? `Mindest-Trades (adaptiv: ${r.min_trades.expected} erwartet): IS ≥ ${r.min_trades.is}` : ''}>
                    {cell(r.is)}
                    {r.min_trades && <div style={{ opacity: 0.6, fontSize: 10 }} data-testid={`ai-seed-min-${r.asset_class}-${r.setup}`}>min {r.min_trades.is} / OOS {r.min_trades.oos}</div>}
                  </td>
                  <td className={`mono ${(r.oos?.pnl || 0) > 0 ? 'pos' : (r.oos?.pnl || 0) < 0 ? 'neg' : ''}`} title={diagCell(r.diag?.oos) || ''}>
                    {cell(r.oos)}
                    {diagCell(r.diag?.oos) && (
                      <div style={{ opacity: 0.7, fontSize: 10 }} data-testid={`ai-seed-diag-${r.asset_class}-${r.setup}`}>{diagCell(r.diag.oos)}</div>
                    )}
                  </td>
                  <td className="mono">{r.stored ?? 0}</td>
                  <td style={{ color: STATUS[r.status]?.c }} title={r.note || ''} data-testid={`ai-seed-status-${r.asset_class}-${r.setup}`}>
                    {STATUS[r.status]?.l || r.status}
                    {r.ai_proposal && (
                      <div className="ai-seed-proposal" data-testid={`ai-seed-proposal-${r.asset_class}-${r.setup}`}>
                        <Brain size={11} weight="bold" /> KI-Rev.{r.ai_proposal.version} vorgemerkt{r.ai_proposal.reason ? `: ${r.ai_proposal.reason}` : ''}
                        {(r.ai_proposal.changes || []).length > 0 && (
                          <div className="mono" style={{ opacity: 0.8 }}>Änderung: {r.ai_proposal.changes.join(' · ')}{r.ai_proposal.expect ? ` — erwartet: ${r.ai_proposal.expect}` : ''}</div>
                        )}
                      </div>
                    )}
                    {lastLesson(r) && (
                      <div style={{ opacity: 0.75, fontSize: 10, marginTop: 2 }} title={(r.lessons || []).join('\n')} data-testid={`ai-seed-lesson-${r.asset_class}-${r.setup}`}>
                        Lernschleife: {lastLesson(r)}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {eventRows.map(({ k, cls, ev, bt, first }) => {
                const agg = bt.aggregate || {};
                const jr = evJob?.results?.[`${k}:${cls}`] || (cls === 'crypto' ? evJob?.results?.[k] : null);
                const ai = (ev.ai_params_by_class || {})[cls] || (cls === 'crypto' ? ev.ai_params : null);
                return (
                  <tr key={`event-${k}-${cls}`} data-testid={`ai-seed-row-events-${k}-${cls}`}>
                    <td title={bt.data_note || ''}>Events · {clsLabelOf(cls)}</td>
                    <td className="bt-name">{EVENT_META[k].label} <span className="mono" style={{ opacity: 0.6, fontSize: 10 }}>{ev.setup} · {(bt.symbols || []).join('/')}</span></td>
                    <td className="mono" title={ai?.reason || 'feste Basis-Regeln (ex-ante, kein Tuning)'}>
                      {ai ? `KI-Rev.${ai.version}` : 'Basis'}
                      {ai && admin && (
                        <button className="bt-tool-btn" style={{ marginLeft: 6 }} onClick={() => eventRef.current?.resetParams(k, cls)}
                          title={`KI-Parameter (${clsLabelOf(cls)}) löschen – zurück zu den festen Basis-Regeln`} data-testid={`event-seed-reset-${k}-${cls}`}>
                          <ArrowCounterClockwise size={11} weight="bold" />
                        </button>
                      )}
                      {ai?.changes?.length > 0 && (
                        <div className="mono" style={{ opacity: 0.7, fontSize: 10 }}>{ai.changes.slice(0, 2).join(' · ')}</div>
                      )}
                    </td>
                    <td className="mono">{eventCell(agg.in_sample)}</td>
                    <td className={`mono ${(agg.out_of_sample?.pnl || 0) > 0 ? 'pos' : (agg.out_of_sample?.pnl || 0) < 0 ? 'neg' : ''}`}>{eventCell(agg.out_of_sample)}</td>
                    <td className="mono" title="Event-Setups speichern keine OOS-Trades fürs Reife-Gate – Live-Freigabe direkt per Opt-in">—</td>
                    <td style={{ color: bt.validated ? '#00FF66' : '#FFB020' }} title={bt.validation_reason || ''} data-testid={`event-seed-status-${k}-${cls}`}>
                      {bt.validated ? 'Edge bestätigt (validiert)' : 'kein Edge'}
                      {jr?.rounds ? ` · ${jr.rounds}× KI` : ''}
                      {first ? (
                        <label className="bt-check" style={{ marginTop: 2 }}
                          title="Live-Opt-in gilt je Event – live geht eine Anlageklasse aber nur, wenn IHR Backtest validiert ist"
                          data-testid={`event-seed-live-${k}`}>
                          <input type="checkbox" checked={!!ev.live_enabled} disabled={!admin} onChange={() => eventRef.current?.toggleLive(k, ev.live_enabled)} />
                          Live {ev.live_enabled ? 'AN' : 'aus'}
                        </label>
                      ) : (
                        <div style={{ opacity: 0.6, fontSize: 10 }}>Live-Schalter s. oben ({EVENT_META[k].label})</div>
                      )}
                      {jr?.lessons?.length > 0 && (
                        <div style={{ opacity: 0.75, fontSize: 10 }} title={jr.lessons.join('\n')} data-testid={`event-seed-lesson-${k}-${cls}`}>
                          <Brain size={10} weight="bold" /> {jr.lessons[jr.lessons.length - 1]}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {info?.classes && Object.keys(info.classes).length > 0 && admin && (
        <div className="bt-tools" style={{ marginTop: 8 }} data-testid="ai-seed-reset-row">
          <span className="bt-ram">Stand zurücksetzen:</span>
          {Object.keys(info.classes).map(c => (
            <button key={c} className="bt-tool-btn" onClick={() => resetCls(c)} data-testid={`ai-seed-reset-${c}`}>
              <ArrowCounterClockwise size={12} weight="bold" /> {CLASS_LABELS[c] || c}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
