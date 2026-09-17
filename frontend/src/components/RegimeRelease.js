import React, { useState, useEffect, useCallback } from 'react';
import { Eye, Lightning, ArrowCounterClockwise, ClockCounterClockwise, Stop } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { fmtDateTime } from '../lib/time';
import './RegimeRelease.css';

/**
 * Regime-Brücke (PLAN_REGIME_BRUECKE 1.3): Freigabe-Stufen einer Lab-Analyse.
 *   none   -> normale Lab-Analyse (keine Wirkung)
 *   shadow -> "Beobachten": Struktur-Regime wird je Trade mitgeschrieben, keine Wirkung auf Prompt/Gate
 *   active -> "Wirksam": Prompt-Block + optional Gate-Quelle `lab`
 * Knöpfe sind grau, solange `release-check` Gründe liefert (Tooltip = fehlende Nachweise).
 */
const API_URL = process.env.REACT_APP_BACKEND_URL;

export const CLASS_LABELS = { crypto: 'Krypto', indices: 'Indizes', resources: 'Rohstoffe', forex: 'Forex' };
const STAGE_LABEL = { shadow: 'SHADOW', active: 'WIRKSAM' };

const stageOf = (a) => a?.release?.stage || 'none';

const classesText = (rel) => (rel?.asset_classes || []).map(c => CLASS_LABELS[c] || c).join('/');

const postRelease = async (aid, body) => {
  const r = await fetch(`${API_URL}/api/regime-lab/${aid}/release`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const reasons = d?.detail?.reasons || (typeof d?.detail === 'string' ? [d.detail] : ['Speichern fehlgeschlagen']);
    throw new Error(reasons.join(' · '));
  }
  return d;
};

/** Badge in der Analysen-Liste: `SHADOW · Krypto · seit 14.09. · 18/30 Trades`. */
export function RegimeReleaseBadge({ analysis, shadowTrades }) {
  const stage = stageOf(analysis);
  if (stage === 'none') return null;
  const rel = analysis.release || {};
  const parts = [STAGE_LABEL[stage], classesText(rel)];
  if (rel.since) parts.push(`seit ${fmtDateTime(rel.since).slice(0, 6)}`);
  if (stage === 'shadow' && shadowTrades != null) parts.push(`${shadowTrades}/30 Trades`);
  return (
    <span className={`rr-badge ${stage}`} data-testid={`regime-release-badge-${analysis.id}`}
      title={stage === 'shadow'
        ? 'Beobachten: Struktur-Regime wird je KI-Trade gespeichert – ohne Wirkung auf Prompt oder Gate'
        : 'Wirksam: KI-Trader sieht das Struktur-Regime im Prompt; Gate-Quelle „Lab“ wählbar'}>
      {parts.filter(Boolean).join(' · ')}
    </span>
  );
}

function ReleaseHistory({ analysis }) {
  const hist = [...(analysis.release?.history || [])].reverse();
  if (!hist.length) return <div className="opt-small">Noch keine Stufenänderung.</div>;
  return (
    <div className="rr-history" data-testid={`regime-release-history-${analysis.id}`}>
      {hist.map((h, i) => (
        <div key={i} className="rr-history-row">
          <span className={`rr-badge ${h.stage}`}>{h.stage === 'none' ? 'NONE' : STAGE_LABEL[h.stage]}</span>
          <span className="opt-small">{fmtDateTime(h.at)}</span>
          <span className="opt-small">{h.by === 'ki_trader' ? 'KI-Trader' : h.by}</span>
          <span className="opt-small rr-reason">{h.reason}</span>
        </div>
      ))}
    </div>
  );
}

/** Knöpfe „Beobachten (Shadow)“ / „Wirksam schalten“ / Widerruf + History (in der Analyse-Ansicht). */
export function RegimeReleaseControls({ analysis, onChanged }) {
  const [checks, setChecks] = useState({});
  const [showHist, setShowHist] = useState(false);
  const [busy, setBusy] = useState(false);
  const stage = stageOf(analysis);
  const aid = analysis.id;

  const loadChecks = useCallback(() => {
    ['shadow', 'active'].forEach(st => {
      fetch(`${API_URL}/api/regime-lab/${aid}/release-check?stage=${st}`).then(r => r.json())
        .then(d => setChecks(prev => ({ ...prev, [st]: d })))
        .catch(() => setChecks(prev => ({ ...prev, [st]: { ok: false, reasons: ['Prüfung nicht erreichbar'] } })));
    });
  }, [aid]);

  useEffect(() => { loadChecks(); }, [loadChecks, analysis.release?.stage]);

  const setStage = async (target, label) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const reason = window.prompt(`${label} – kurzer Grund für die History (Pflicht):`, '');
    if (reason === null) return;
    if (!reason.trim()) { toast.error('Grund ist Pflicht'); return; }
    setBusy(true);
    try {
      await postRelease(aid, { stage: target, reason: reason.trim() });
      toast.success(target === 'none' ? 'Freigabe widerrufen' : `Stufe ${STAGE_LABEL[target]} gesetzt`);
      onChanged?.();
    } catch (e) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const btn = (target, label, Icon, testid) => {
    const chk = checks[target];
    const reasons = chk?.reasons || [];
    const isCurrent = stage === target;
    const disabled = busy || isCurrent || (chk ? !chk.ok : true);
    const title = isCurrent ? `Stufe ${STAGE_LABEL[target]} ist bereits gesetzt`
      : reasons.length ? `Fehlende Nachweise:\n• ${reasons.join('\n• ')}`
        : chk ? 'Alle Nachweise vorhanden – Klick setzt die Stufe' : 'Prüfe Nachweise …';
    return (
      <button className={`opt-chip rr-btn ${isCurrent ? 'current' : ''}`} disabled={disabled}
        onClick={() => setStage(target, label)} title={title} data-testid={testid}>
        <Icon size={12} weight="bold" /> {label}{reasons.length && !isCurrent ? ` (${reasons.length} fehlt)` : ''}
      </button>
    );
  };

  return (
    <div className="rr-box" data-testid={`regime-release-controls-${aid}`}>
      <div className="rr-head">
        <b>FREIGABE AN DEN KI-TRADER</b>
        <RegimeReleaseBadge analysis={analysis} />
        {stage === 'none' && <span className="opt-small">keine Freigabe – reine Forschung</span>}
        <span style={{ flex: 1 }} />
        {btn('shadow', 'Beobachten (Shadow)', Eye, `regime-release-shadow-${aid}`)}
        {btn('active', 'Wirksam schalten', Lightning, `regime-release-active-${aid}`)}
        {stage !== 'none' && (
          <button className="opt-chip rr-btn revoke" disabled={busy} onClick={() => setStage('none', 'Widerruf')}
            title="Widerruf: Stufe sofort auf none – Prompt/Gate exakt wie ohne Freigabe"
            data-testid={`regime-release-revoke-${aid}`}>
            <ArrowCounterClockwise size={12} weight="bold" /> Widerruf
          </button>
        )}
        <button className="opt-chip" onClick={() => setShowHist(v => !v)} title="Wer hat wann warum geschaltet?"
          data-testid={`regime-release-history-toggle-${aid}`}>
          <ClockCounterClockwise size={12} /> History ({(analysis.release?.history || []).length})
        </button>
      </div>
      <div className="opt-small" style={{ marginTop: 4 }}>
        Shadow = Struktur-Regime wird je KI-Trade mitgeschrieben (Fingerprint, Rewards), ohne Wirkung.
        Wirksam = zusätzlich Prompt-Block; Gate-Quelle „Lab“ wählbar. Knöpfe bleiben grau, bis
        Kalibrierung + Ablation gelaufen sind (Wirksam: zusätzlich ≥ 30 Shadow-Trades je Struktur-Regime).
      </div>
      {showHist && <ReleaseHistory analysis={analysis} />}
    </div>
  );
}

/**
 * Anzeige im KI-Trader-Panel (neben Regime-Sperrfilter): aktive Stufe je Klasse,
 * Stichprobenstand, KI-Vorschläge (inkl. Karenz-Stopp) – ohne ins Lab zu wechseln.
 */
export function StructuralStagePanel({ refreshKey }) {
  const [data, setData] = useState(null);

  const load = useCallback(() => {
    fetch(`${API_URL}/api/regime-lab/releases`).then(r => r.json()).then(setData).catch(() => setData(null));
  }, []);
  useEffect(() => { load(); }, [load, refreshKey]);

  const stopProposal = async (pid) => {
    const r = await fetch(`${API_URL}/api/regime-lab/release-proposals/${pid}/stop`, { method: 'POST', headers: authHeaders() });
    if (r.ok) { toast.success('Karenz gestoppt – keine Umschaltung'); load(); } else toast.error('Stopp fehlgeschlagen');
  };

  if (!data) return null;
  const byClass = {};
  (data.releases || []).forEach(r => (r.release?.asset_classes || []).forEach(c => { byClass[c] = r; }));
  const act = data.activation || {};
  const rewards = (data.rewards_by_structural || []).filter(r => r.regime !== 'unbekannt');

  return (
    <div className="rr-stage-panel" data-testid="ai-structural-stage-panel">
      <div className="rr-stage-title">STRUKTUR-REGIME (LAB-BRÜCKE)</div>
      <div className="rr-stage-row">
        {Object.keys(CLASS_LABELS).map(cls => {
          const rel = byClass[cls]?.release;
          const st = rel?.stage || 'none';
          return (
            <span key={cls} className={`rr-badge ${st}`} data-testid={`ai-structural-stage-${cls}`}
              title={rel ? `${byClass[cls].name} · ${byClass[cls].timeframe} · seit ${fmtDateTime(rel.since)}` : 'keine freigegebene Lab-Analyse'}>
              {CLASS_LABELS[cls]}: {st === 'none' ? 'keine' : STAGE_LABEL[st]}
            </span>
          );
        })}
      </div>
      <div className="opt-small" data-testid="ai-structural-activation">
        Shadow-Stichprobe: {act.shadow_trades ?? 0} Trades
        {rewards.length ? ` · ${rewards.map(r => `${r.regime} n=${r.trades} Ø ${r.avg_reward >= 0 ? '+' : ''}${r.avg_reward}`).join(' · ')}` : ''}
        {' · '}{act.ok ? 'Wirksam-Gate grün' : `Wirksam noch nicht möglich: ${(act.reasons || []).join('; ')}`}
      </div>
      {(data.proposals || []).map(p => (
        <div key={p.id} className="rr-proposal" data-testid={`ai-structural-proposal-${p.id}`}>
          <b>KI-Empfehlung ({CLASS_LABELS[p.symbol] || p.symbol}): {p.changes?.structural_regime_stage === 'active' ? 'wirksam schalten' : 'zurück auf Shadow'}</b>
          {' · '}{p.status === 'needs_data' ? 'wartet auf Daten' : p.status === 'auto_applied' ? `Karenz bis ${fmtDateTime(p.pending_auto_until)}` : 'offen (Vorschläge-Panel)'}
          {p.reason ? ` · ${p.reason}` : ''}
          {p.status === 'auto_applied' && !p.applied_at && isAdmin() && (
            <button className="opt-chip rr-btn revoke" onClick={() => stopProposal(p.id)} data-testid={`ai-structural-proposal-stop-${p.id}`}>
              <Stop size={12} weight="bold" /> Stopp
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
