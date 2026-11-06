import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Scales } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Fairer Zeitraum-Vergleich: alle Erkennungen auf exakt demselben Testzeitraum rechnen (Hintergrund-Job). */
export function FairCompareButton({ selCoins, onDone }) {
  const [job, setJob] = useState(null);
  const timer = useRef(null);
  const admin = isAdmin();

  const poll = useCallback(async () => {
    const r = await fetch(`${API_URL}/api/regime-lab/champions/fair-compare/status`).catch(() => null);
    const d = r?.ok ? await r.json() : null;
    setJob(d);
    if (d?.running) { timer.current = setTimeout(poll, 4000); return; }
    if (timer.current) { timer.current = null; onDone?.(); }
  }, [onDone]);

  useEffect(() => { poll(); return () => clearTimeout(timer.current); }, [poll]);

  const start = async () => {
    const r = await fetch(`${API_URL}/api/regime-lab/champions/fair-compare`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ symbols: selCoins }),
    }).catch(() => null);
    if (!r?.ok) { toast.error(r?.status === 409 ? 'Fairer Vergleich läuft bereits' : 'Start fehlgeschlagen (Admin-Login?)'); return; }
    toast.success('Fairer Vergleich gestartet');
    timer.current = setTimeout(poll, 1500);
  };

  const running = job?.running;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <button className="opt-chip" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }} disabled={!admin || running} onClick={start} data-testid="regime-fair-compare-start"
        title="Rechnet alle Erkennungen je Asset auf exakt demselben ungesehenen Zeitraum, auf derselben Zeitachse (feinster Timeframe) und gegen dieselbe Referenz – erst dann zählt der Vergleich für die Champion-Wahl.">
        <Scales size={11} /> Fairer Zeitraum-Vergleich
      </button>
      {running && (
        <span className="opt-small" data-testid="regime-fair-compare-progress">
          {job.done}/{job.total} · {job.current || '…'}
        </span>
      )}
    </span>
  );
}

const REASON = {
  missing: 'noch nicht gerechnet', old: 'veraltet', uncovered: 'neue Analyse – neu rechnen', blocked: 'nicht möglich',
};

/** Badge je Zeile: wurde der faire Gleich-Zeitraum-Vergleich für die Entscheidung genutzt? */
export function FairBadge({ fair, rowKey }) {
  if (!fair) return null;
  const excl = Object.keys(fair.excluded || {}).length;
  const tip = [fair.note, excl ? `${excl} Analyse(n) ausgeschlossen: ${Object.values(fair.excluded).join(' · ')}` : '']
    .filter(Boolean).join(' – ');
  return fair.used
    ? <span style={{ color: '#00E5A0' }} title={tip} data-testid={`regime-fair-badge-${rowKey}`}>fair · {fair.note}{fair.base_timeframe ? ` · Achse ${fair.base_timeframe}` : ''}</span>
    : <span style={{ color: '#8A8F98' }} title={tip} data-testid={`regime-fair-badge-${rowKey}`}>eigene Zeiträume ({REASON[fair.reason] || fair.reason})</span>;
}
