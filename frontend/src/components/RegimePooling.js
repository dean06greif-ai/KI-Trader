import React, { useCallback, useEffect, useRef, useState } from 'react';
import { UsersThree, Play } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import InfoTip from './InfoTip';
import RegimePoolingGuide, { POOL_TIPS } from './RegimePoolingGuide';
import RegimePoolingResult from './RegimePoolingResult';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const PRIORS = [[80, 'vorsichtig (80)'], [40, 'Standard (40)'], [20, 'mutig (20)']];
const srcLabel = (s) => `${s.check?.ok ? '✓' : '✗'} ${s.name || s.id} · ${s.timeframe} · ${s.days} T · ${s.symbols.length} Coins`;

function useJob(onDone) {
  const [job, setJob] = useState(null);
  const timer = useRef(null);
  const poll = useCallback(async (id) => {
    const r = await fetch(`${API_URL}/api/regime-lab/status/${id}`).catch(() => null);
    const d = r?.ok ? await r.json() : null;
    setJob(d);
    if (d?.status === 'running') { timer.current = setTimeout(() => poll(id), 3000); return; }
    if (d?.status === 'done') { toast.success('Pooling-Analyse gespeichert'); onDone?.(); }
    if (d?.status === 'error') toast.error(`Pooling fehlgeschlagen: ${d.error || ''}`);
  }, [onDone]);
  useEffect(() => () => clearTimeout(timer.current), []);
  return [job, poll];
}

function SourceCheck({ src }) {
  if (!src) return null;
  const { reasons = [], warnings = [] } = src.check || {};
  return (
    <div className="opt-small" data-testid="regime-pooling-check">
      {reasons.map((t) => <div key={t} style={{ color: '#FF5C7A' }}>✗ {t}</div>)}
      {warnings.map((t) => <div key={t} style={{ color: '#FFB020' }}>! {t}</div>)}
      {!reasons.length && <div style={{ color: '#00E5A0' }}>✓ Geeignet: {src.symbols.join(', ')} · Training {src.train_pct} %</div>}
    </div>
  );
}

/** Partial Pooling: Gruppen-Modell + vorsichtige Coin-Skalen (Shrinkage). */
export default function RegimePooling() {
  const [data, setData] = useState(null);
  const [aid, setAid] = useState('');
  const [prior, setPrior] = useState(40);
  const [shown, setShown] = useState('');
  const admin = isAdmin();
  const load = useCallback(async () => {
    const r = await fetch(`${API_URL}/api/regime-lab/pooling`).catch(() => null);
    const d = r?.ok ? await r.json() : null;
    setData(d);
    setAid((cur) => cur || d?.sources?.find((s) => s.check?.ok)?.id || '');
  }, []);
  const [job, poll] = useJob(load);
  useEffect(() => { load(); }, [load]);

  const src = data?.sources?.find((s) => s.id === aid);
  const running = job?.status === 'running';
  const start = async () => {
    const r = await fetch(`${API_URL}/api/regime-lab/pooling`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ analysis_id: aid, prior_phases: prior }),
    }).catch(() => null);
    const d = r ? await r.json().catch(() => ({})) : {};
    if (!r?.ok) { toast.error(d.detail || 'Start fehlgeschlagen (Admin-Login?)'); return; }
    toast.success('Pooling gestartet');
    poll(d.job_id);
  };
  const pooled = data?.pooled || [];
  const view = job?.status === 'done' ? { pooling: job.result?.pooling, id: job.result?.analysis_id }
    : (() => { const p = pooled.find((x) => x.id === (shown || pooled[0]?.id)); return p ? { pooling: p.pooling, id: p.id } : null; })();

  return (
    <div className="opt-row rl-tool" data-testid="regime-pooling">
      <div className="opt-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <UsersThree size={13} weight="bold" /> PARTIAL POOLING – GRUPPEN-STRUKTUR + VORSICHTIGE COIN-SKALEN
      </div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        Coins sind verschieden, aber eine Suche auf einem einzelnen Coin lernt dessen Wellen auswendig. Pooling sucht die
        Struktur gemeinsam und erlaubt je Coin nur eine Skalen-Anpassung – umso kleiner, je weniger Phasen der Coin liefert.
      </div>
      <RegimePoolingGuide />
      <div className="opt-setup" style={{ alignItems: 'flex-end', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <label className="opt-field" style={{ minWidth: 280 }}>Gruppen-Analyse
          <select value={aid} onChange={(e) => setAid(e.target.value)} data-testid="regime-pooling-source">
            {!data?.sources?.length && <option value="">– keine v2-Analyse gespeichert –</option>}
            {(data?.sources || []).map((s) => <option key={s.id} value={s.id}>{srcLabel(s)}</option>)}
          </select>
        </label>
        <label className="opt-field">Prior (Gruppen-Zug) <InfoTip testId="regime-pooling-tip-prior">{POOL_TIPS.prior}</InfoTip>
          <select value={prior} onChange={(e) => setPrior(Number(e.target.value))} data-testid="regime-pooling-prior">
            {PRIORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <button className="opt-chip" disabled={!admin || running || !src?.check?.ok} onClick={start}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }} data-testid="regime-pooling-start"
          title={!admin ? 'Nur mit Admin-Login' : ''}>
          <Play size={11} /> Pooling starten
        </button>
      </div>
      <SourceCheck src={src} />
      {running && <div className="opt-small" data-testid="regime-pooling-progress">{job.progress}% · {job.phase}</div>}
      {pooled.length > 0 && job?.status !== 'done' && (
        <label className="opt-field" style={{ marginTop: 8 }}>Bisherige Pooling-Analysen
          <select value={shown || pooled[0].id} onChange={(e) => setShown(e.target.value)} data-testid="regime-pooling-history">
            {pooled.map((p) => <option key={p.id} value={p.id}>{p.name} · {String(p.created_at || '').slice(0, 10)}</option>)}
          </select>
        </label>
      )}
      {view?.pooling && <RegimePoolingResult pooling={view.pooling} analysisId={view.id} />}
    </div>
  );
}
