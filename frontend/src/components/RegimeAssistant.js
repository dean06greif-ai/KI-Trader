import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Compass, Play } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { isAdmin } from '../auth';
import { postJson } from '../lib/postJson';
import RegimeAssistantResult, { lightColor } from './RegimeAssistantResult';
import ResearchModeToggle from './ResearchModeToggle';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const getJson = (p) => fetch(`${API_URL}${p}`).then(r => (r.ok ? r.json() : null)).catch(() => null);

function useAssistantJob(onDone) {
  const [job, setJob] = useState(null);
  const timer = useRef(null);
  const poll = useCallback(async (id) => {
    const d = await getJson(`/api/regime-assistant/job/${id}`);
    setJob(d);
    if (d?.status === 'running') { timer.current = setTimeout(() => poll(id), 4000); return; }
    if (d?.status === 'done') { toast.success('Regime-Assistent fertig'); onDone?.(); }
    if (d?.status === 'error') toast.error(`Assistent fehlgeschlagen: ${d.error || ''}`);
  }, [onDone]);
  useEffect(() => () => clearTimeout(timer.current), []);
  return [job, poll];
}

function ClassTabs({ classes, cls, setCls }) {
  return (
    <div className="sat-regime-tabs" data-testid="ra-class-tabs">
      {classes.map(c => (
        <button key={c.asset_class} className={`sat-regime-tab ${cls === c.asset_class ? 'active' : ''}`}
          onClick={() => setCls(c.asset_class)} data-testid={`ra-class-${c.asset_class}`}
          style={{ borderLeft: `3px solid ${lightColor(c.latest?.summary?.light)}` }}>
          {c.label}{c.latest?.summary?.light ? ` · ${c.latest.summary.light}` : ''}
        </button>
      ))}
    </div>
  );
}

function FallbackToggle({ config, onSaved }) {
  const save = async (v) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try { await postJson('/api/regime-assistant/config', { fallback_enabled: v }); toast.success('Gespeichert'); onSaved(); }
    catch (e) { toast.error(e.message); }
  };
  return (
    <label className="opt-small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}
      title="Solange es für eine Anlageklasse keine Lab-Freigabe gibt, bekommt der KI-Trader das Struktur-Regime vom Assistenten – nur bei Ampel grün/gelb.">
      <input type="checkbox" checked={!!config?.fallback_enabled} onChange={e => save(e.target.checked)} data-testid="ra-fallback-toggle" />
      KI-Trader: Assistenten-Regime nutzen, solange keine Lab-Freigabe existiert (nur grün/gelb)
    </label>
  );
}

/** Regime-Assistent: ein Knopf je Anlageklasse, festes Rezept, Ergebnis als Ampel. */
export default function RegimeAssistant() {
  const [ov, setOv] = useState(null);
  const [cls, setCls] = useState('crypto');
  const [run, setRun] = useState(null);
  const [quick, setQuick] = useState(true);
  const loadRun = useCallback(async (c) => {
    const d = await getJson(`/api/regime-assistant/runs/latest?asset_class=${c}`);
    setRun(d?.run || null);
  }, []);
  const load = useCallback(async () => { setOv(await getJson('/api/regime-assistant/overview')); }, []);
  const refresh = useCallback(() => { load(); loadRun(cls); }, [load, loadRun, cls]);
  const [job, poll] = useAssistantJob(refresh);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadRun(cls); }, [cls, loadRun]);
  useEffect(() => { if (ov?.running_job?.id && !job) poll(ov.running_job.id); }, [ov, job, poll]);

  const info = ov?.classes?.find(c => c.asset_class === cls);
  const running = job?.status === 'running';
  const start = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const body = quick ? { asset_class: cls, symbols: info.symbols.slice(0, 3), days: 365 } : { asset_class: cls };
    try { const d = await postJson('/api/regime-assistant/run', body); toast.success('Assistent gestartet'); poll(d.job_id); }
    catch (e) { toast.error(e.message); }
  };
  if (!ov) return null;
  const rc = info?.recipe;
  return (
    <div className="opt-row rl-tool" data-testid="regime-assistant">
      <div className="opt-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Compass size={13} weight="bold" /> 0 · REGIME-ASSISTENT – EIN KNOPF JE ANLAGEKLASSE (EMPFOHLENER START)
      </div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        Festes Rezept statt Suche, damit nichts auf die Vergangenheit überangepasst wird: übergeordnetes Regime (Makro) × Haupt-Regime.
        Bewertet wird, was die Erkennung <b>für den Handel taugt</b>: Wie unterschiedlich lief der Kurs danach in Auf- und Abwärts-Phasen?
        Wie stabil ist die Erkennung (Phasendauer, Ping-Pong)? Das Ziel ist die aktuelle Phase zu erkennen, keine Kursprognose.
      </div>
      <ClassTabs classes={ov.classes} cls={cls} setCls={setCls} />
      {rc && (
        <div className="opt-small" data-testid="ra-recipe" style={{ margin: '6px 0' }}>
          Rezept {rc.label}: Makro <b>{rc.macro_tf}</b> × Haupt <b>{rc.primary_tf}</b> · gehandelt auf {rc.trade_tfs} · {rc.days} Tage
          {rc.leader && <> · Leitmarkt {rc.leader}</>} · Assets: {info.symbols.slice(0, 8).join(', ')}
        </div>
      )}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="opt-chip" onClick={start} disabled={running} data-testid="ra-run-button" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Play size={13} weight="bold" /> {running ? 'läuft …' : `${info?.label || ''} prüfen`}
        </button>
        <select value={quick ? 'quick' : 'full'} onChange={e => setQuick(e.target.value === 'quick')} disabled={running} data-testid="ra-run-mode" style={{ fontSize: 12, padding: '3px 6px' }}>
          <option value="quick">Schnell: 3 Assets · 365 Tage (~3–5 Min.)</option>
          <option value="full">Vollständig: bis 8 Assets · {rc?.days} Tage (15–30 Min.)</option>
        </select>
        {running && <span className="opt-small" data-testid="ra-job-phase">{job.progress} % · {job.phase} (dauert einige Minuten)</span>}
      </div>
      <RegimeAssistantResult run={run} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
        <FallbackToggle config={ov.config} onSaved={load} />
        <ResearchModeToggle mode={ov.research_mode} onSaved={load} />
      </div>
    </div>
  );
}
