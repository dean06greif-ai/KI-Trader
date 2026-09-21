import React, { useState } from 'react';
import { CaretDown, CaretUp, PaperPlaneRight } from '@phosphor-icons/react';
import { authHeaders } from '../auth';
import { toast } from '../lib/toast';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmtR = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1).replace('.', ',')} R`);
const fmtPct = (v) => (v == null ? '—' : `${Math.round(v)} %`);
const fmtTs = (ts) => (ts ? new Date(ts).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '');

const STYLE = {
  edge: { background: 'rgba(80,200,120,0.18)', color: '#5fd38a' },
  neutral: { background: 'rgba(160,160,180,0.16)', color: '#b8b8c8' },
  hinderlich: { background: 'rgba(255,150,60,0.2)', color: '#ffa254' },
  zu_weich: { background: 'rgba(255,150,60,0.2)', color: '#ffa254' },
  zu_wenig_daten: { background: 'rgba(120,120,140,0.12)', color: '#8a8a9a' },
};

const badgeText = (row) => {
  switch (row.verdict) {
    case 'edge': return `EDGE ${fmtR(row.net_contribution_r)}`;
    case 'hinderlich': return `hinderlich ${fmtR(row.prevented?.net_r)}`;
    case 'zu_weich': return 'zu weich';
    case 'zu_wenig_daten': return `n=${row.sample} · sammelt`;
    default: return 'neutral';
  }
};

/** Bilanz-Zusammenfassung über der Lektionen-Liste (Attribution-Quote, Gegenproben). */
export const LessonImpactSummary = ({ impact }) => {
  const t = impact?.totals;
  if (!t || !t.trades) return null;
  return (
    <div className="ai-learn-empty" style={{ marginBottom: 4 }} data-testid="lesson-impact-summary">
      Attribution: {t.attribution_pct != null ? `${t.attribution_pct} %` : '—'} der {t.trades} Trades tragen Lektions-IDs
      {' · '}{t.cf_done} Gegenproben, {t.cf_pending} offen{t.cf_no_data ? `, ${t.cf_no_data} ohne Daten` : ''}
      {impact?.flags && !impact.flags.lesson_impact_in_prompt ? ' · Prompt-Block aus (nur Anzeige)' : ''}
    </div>
  );
};

/** Badge + Ausklapper „Bilanz“ je Lektion. */
export const LessonImpactBadge = ({ row, recent, onShowChart, onReload }) => {
  const [open, setOpen] = useState(false);
  if (!row) return null;
  const prevented = (recent || []).filter((r) => (r.blocked_by || []).includes(row.id)).slice(0, 5);
  const canSuggest = ['lockern', 'verschaerfen'].includes(row.suggestion);

  const handOver = async () => {
    try {
      const res = await fetch(`${API_URL}/api/ai/lessons/reevaluate`, {
        method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: `Lektions-Bilanz: ${row.id} ${row.suggestion} – ${row.reason}` }),
      });
      const data = await res.json();
      if (data.status === 'ok') toast.success('Vorschlag an den Lernlauf übergeben – Gates entscheiden');
      else toast.error(data.detail || 'Übergabe fehlgeschlagen');
      onReload && onReload();
    } catch (e) { toast.error('Verbindungsfehler'); }
  };

  return (
    <>
      <span className="ai-lesson-locked" style={STYLE[row.verdict] || STYLE.neutral} title={row.reason}
        data-testid={`lesson-impact-badge-${row.id}`}>{badgeText(row)}</span>
      <button className="ai-lesson-btn" style={{ padding: '0 4px' }} onClick={() => setOpen(!open)}
        title="Bilanz dieser Lektion ein-/ausblenden" data-testid={`lesson-impact-toggle-${row.id}`}>
        {open ? <CaretUp size={12} weight="bold" /> : <CaretDown size={12} weight="bold" />}
      </button>
      {open && (
        <div style={{ width: '100%', marginTop: 6, fontSize: 12 }} data-testid={`lesson-impact-table-${row.id}`}>
          <table className="ai-lesson-impact-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ opacity: 0.7 }}><th align="left">Gruppe</th><th>n</th><th>WR</th><th>Ø R</th><th>Σ R</th></tr></thead>
            <tbody>
              <tr><td>mit Lektion</td><td align="center">{row.with.n}</td><td align="center">{fmtPct(row.with.wr)}</td><td align="center">{fmtR(row.with.avg_r)}</td><td align="center">{fmtR(row.with.sum_r)}</td></tr>
              <tr><td>ohne (gleiche Klasse/Zeitraum)</td><td align="center">{row.without.n}</td><td align="center">{fmtPct(row.without.wr)}</td><td align="center">{fmtR(row.without.avg_r)}</td><td align="center">—</td></tr>
              <tr><td>verhindert (Gegenprobe)</td><td align="center">{row.prevented.n}</td><td align="center" colSpan={2}>{row.prevented.would_loss} wären Verlierer · {row.prevented.would_win} Gewinner</td><td align="center">{fmtR(row.prevented.net_r)}</td></tr>
            </tbody>
          </table>
          <div style={{ marginTop: 4, opacity: 0.85 }}>
            Beitrag netto <b>{fmtR(row.net_contribution_r)}</b> · Urteil <b>{row.verdict}</b> → {row.suggestion} · {row.reason}
            {row.collection_n ? ` · ${row.collection_n} Sammel-Trades separat` : ''}
          </div>
          {prevented.length > 0 && (
            <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
              {prevented.map((p) => (
                <li key={p.decision_id}>
                  <button className="ai-lesson-btn" style={{ padding: 0 }} onClick={() => onShowChart && onShowChart(p.symbol)}
                    data-testid={`lesson-impact-prevented-${p.decision_id}`}>{p.symbol}</button>
                  {' '}{fmtTs(p.ts)} · wäre {p.action} · {fmtR(p.r)} ({p.exit_reason})
                </li>
              ))}
            </ul>
          )}
          {canSuggest && (
            <button className="ai-action-btn" style={{ marginTop: 6 }} onClick={handOver}
              title="Kein Direkt-Edit: Vorschlag geht in die Neubewertung, die Gates entscheiden"
              data-testid={`lesson-impact-handover-${row.id}`}>
              <PaperPlaneRight size={12} weight="bold" /> Vorschlag „{row.suggestion}“ an Lernlauf übergeben
            </button>
          )}
        </div>
      )}
    </>
  );
};
