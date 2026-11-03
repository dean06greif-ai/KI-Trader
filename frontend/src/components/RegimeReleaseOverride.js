import React, { useState } from 'react';
import { WarningOctagon } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const STAGE_TXT = { shadow: 'Beobachten (Shadow)', active: 'Wirksam' };

/**
 * Manuelle Freigabe trotz fehlender Nachweise (Override). Nur übersteuerbare
 * Gründe; „Wirksam“ erst ab Erkennungs-Note „mittel“. Pflicht: Risiko-Häkchen + Grund.
 * Wird in History/Audit mit den fehlenden Nachweisen protokolliert.
 */
export default function RegimeReleaseOverride({ aid, stage, check, onDone }) {
  const [open, setOpen] = useState(false);
  const [ack, setAck] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  if (!check || check.ok) return null;
  const blocked = !check.override_possible;
  const title = blocked
    ? `Manuell nicht möglich:\n• ${(check.override_blockers || []).join('\n• ')}`
    : 'Selbst entscheiden: Stufe trotz fehlender Nachweise setzen (wird protokolliert)';

  const submit = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!ack || reason.trim().length < 5) { toast.error('Risiko bestätigen und Grund (mind. 5 Zeichen) eintragen'); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${aid}/release`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ stage, reason: `[manuell] ${reason.trim()}`, override: true, override_ack: true }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((d?.detail?.reasons || [d?.detail || 'Freigabe fehlgeschlagen']).join(' · '));
      toast.success(`Stufe ${STAGE_TXT[stage]} manuell gesetzt`);
      setOpen(false); setAck(false); setReason('');
      onDone?.();
    } catch (e) { toast.error(e.message); }
    setBusy(false);
  };

  return (
    <>
      <button className="opt-chip rr-btn rr-override" disabled={blocked || busy} title={title}
        onClick={() => setOpen(v => !v)} data-testid={`regime-release-override-${stage}-${aid}`}>
        <WarningOctagon size={12} weight="bold" /> {STAGE_TXT[stage]} manuell…
      </button>
      {open && !blocked && (
        <div className="rr-override-box" data-testid={`regime-release-override-dialog-${stage}-${aid}`}>
          <b>Manuell „{STAGE_TXT[stage]}“ – du übersteuerst folgende Nachweise:</b>
          <ul>{(check.reasons || []).map((r, i) => <li key={i}>{r}</li>)}</ul>
          <div className="opt-small">
            Erkennungs-Note: <b>{check.quality_grade || 'unbewertet'}</b>.{' '}
            {stage === 'shadow'
              ? 'Shadow hat keine Wirkung auf Entscheidungen – der KI-Trader schreibt nur das Struktur-Regime je Trade mit. Risiko gering.'
              : `Wirksam: das Struktur-Regime geht in den Prompt (und optional ins Gate „Lab“). Bei Fehl-Erkennung trifft die KI Entscheidungen auf falscher Grundlage – Widerruf jederzeit möglich. Regulär wären ${check.activation_min_trades} Shadow-Trades je Struktur-Regime nötig.`}
          </div>
          <label className="opt-check" style={{ marginTop: 6 }}>
            <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)}
              data-testid={`regime-release-override-ack-${stage}-${aid}`} />
            {' '}Ich habe die fehlenden Nachweise gelesen und trage das Risiko.
          </label>
          <input className="opt-input" style={{ width: '100%', marginTop: 6 }} placeholder="Grund (Pflicht, landet in der History)"
            value={reason} onChange={e => setReason(e.target.value)} data-testid={`regime-release-override-reason-${stage}-${aid}`} />
          <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <button className="opt-chip rr-btn rr-override" disabled={busy || !ack || reason.trim().length < 5}
              onClick={submit} data-testid={`regime-release-override-confirm-${stage}-${aid}`}>
              {busy ? 'Setze…' : 'Manuell freigeben'}
            </button>
            <button className="opt-chip" onClick={() => setOpen(false)} data-testid={`regime-release-override-cancel-${stage}-${aid}`}>Abbrechen</button>
          </div>
        </div>
      )}
    </>
  );
}
