import React, { useState, useEffect, useCallback } from 'react';
import { authHeaders, isAdmin } from '../auth';
import { toast } from '../lib/toast';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const r = (v) => (v == null ? '–' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)} R`);
const fmt = (v) => (typeof v === 'boolean' ? (v ? 'an' : 'aus') : v == null ? '–' : String(v));

const post = async (path) => {
  const res = await fetch(`${API_URL}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() } });
  if (res.status === 401) throw new Error('Admin-Login erforderlich');
  if (!res.ok) throw new Error(`Fehler ${res.status}`);
  return res.json();
};

/** Live-Freigabe-Check: Out-of-sample-Beweis des Edge-Filters (Rettungsplan Phase 1). */
const Readiness = ({ ready, blocks }) => {
  if (!ready) return null;
  return (
    <div className={`diag-finding ${ready.ready ? 'diag-info' : 'diag-warn'}`} data-testid="diag-edge-readiness">
      <span className="diag-sev">{ready.ready ? 'BEREIT' : 'BEWEIS'}</span>
      <span>
        <b>Live-Freigabe-Check:</b> {ready.trades}/{ready.min_trades} Trades „Edge-Filter besteht“ · Ø {r(ready.exp_r)} (Ziel ≥ {r(ready.min_r)}) · jüngere Hälfte {r(ready.recent_half_r)}. {ready.text}
        {blocks?.with_risk > 0 && <> · Geblockte Live-Einstiege als Schattentrade: {blocks.with_risk}, Ø {r(blocks.exp_r)} (negativ = Block war richtig)</>}
      </span>
    </div>
  );
};

/** Empfohlene Einstellungen (Rettungsplan Phase 0) – Übernahme nur per Klick. */
const Recommended = ({ onApplied }) => {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    fetch(`${API_URL}/api/ai/recommended-settings`).then(x => x.json()).then(d => setItems(d.items || [])).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  const open = items.filter(i => i.differs);
  const apply = async () => {
    if (!window.confirm(`${open.length} Einstellung(en) auf die Empfehlung setzen?\n\n${open.map(i => `${i.label}: ${fmt(i.current)} → ${fmt(i.value)}`).join('\n')}`)) return;
    setBusy(true);
    try {
      await post('/api/ai/recommended-settings/apply');
      toast.success('Empfohlene Einstellungen übernommen');
      load();
      onApplied?.();
    } catch (e) { toast.error(String(e.message || e)); } finally { setBusy(false); }
  };
  if (!items.length) return null;
  return (
    <div className="diag-block" data-testid="diag-recommended">
      <div className="diag-block-title">EMPFOHLENE EINSTELLUNGEN · aus der Trade-Auswertung (nur per Klick)</div>
      <table className="diag-table">
        <thead><tr><th>Einstellung</th><th>Aktuell</th><th>Empfohlen</th><th>Warum</th></tr></thead>
        <tbody>
          {items.map(i => (
            <tr key={i.key} data-testid={`diag-rec-${i.key}`} className={i.differs ? '' : 'diag-row-ok'}>
              <td>{i.label}</td>
              <td className={`mono ${i.differs ? 'neg' : 'pos'}`}>{fmt(i.current)}</td>
              <td className="mono">{fmt(i.value)}</td>
              <td className="diag-why">{i.why}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {open.length > 0 && isAdmin() && (
        <button className="diag-action-btn" disabled={busy} onClick={apply} data-testid="diag-rec-apply-btn">
          {busy ? 'Übernehme…' : `${open.length} Empfehlung(en) übernehmen`}
        </button>
      )}
      {open.length === 0 && <div className="diag-sub" data-testid="diag-rec-all-ok">Alle Empfehlungen sind aktiv.</div>}
    </div>
  );
};

const WeeklySend = () => {
  const [busy, setBusy] = useState(false);
  if (!isAdmin()) return null;
  const send = async () => {
    setBusy(true);
    try {
      const d = await post('/api/ai/edge-weekly/send');
      toast.success(d.sent ? 'Wochen-Edge per Telegram gesendet' : 'Bericht erstellt – Telegram aus/nicht konfiguriert');
    } catch (e) { toast.error(String(e.message || e)); } finally { setBusy(false); }
  };
  return (
    <button className="diag-action-btn diag-action-ghost" disabled={busy} onClick={send} data-testid="diag-edge-weekly-send-btn"
      title="Wochen-Edge (letzte 7 Tage in R + Live-Freigabe-Check) jetzt per Telegram senden. Automatisch jeden Sonntag 19:00.">
      {busy ? 'Sende…' : 'Wochen-Edge jetzt per Telegram senden'}
    </button>
  );
};

export default function AIEdgeActions({ edge, onChanged }) {
  return (
    <>
      <div className="diag-block" data-testid="diag-edge-actions">
        <Readiness ready={edge?.edge_readiness} blocks={edge?.edge_filter_blocks} />
        <WeeklySend />
      </div>
      <Recommended onApplied={onChanged} />
    </>
  );
}
