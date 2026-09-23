import React, { useState, useEffect, useCallback } from 'react';
import { Eye, Lightning, ArrowCounterClockwise, ClockCounterClockwise, Stop, CheckSquare } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { fmtDateTime } from '../lib/time';
import RegimeReleaseOverride from './RegimeReleaseOverride';
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

/** Badge in der Analysen-Liste: `SHADOW · Krypto · seit 14.09. · 18/15 Trades`. */
export function RegimeReleaseBadge({ analysis, shadowTrades, minTrades }) {
  const stage = stageOf(analysis);
  if (stage === 'none') return null;
  const rel = analysis.release || {};
  const parts = [STAGE_LABEL[stage], classesText(rel)];
  if (rel.since) parts.push(`seit ${fmtDateTime(rel.since).slice(0, 6)}`);
  if (stage === 'shadow' && shadowTrades != null) parts.push(`${shadowTrades}/${minTrades || 30} Trades je Regime`);
  if (rel.manual_override) parts.push('manuell');
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
          {h.override && <span className="rr-badge override" title={(h.override_missing || []).join('\n')}>MANUELL</span>}
          <span className="opt-small rr-reason">{h.reason}</span>
        </div>
      ))}
    </div>
  );
}

/** Knöpfe „Beobachten (Shadow)“ / „Wirksam schalten“ / Widerruf + History (in der Analyse-Ansicht). */
export function RegimeReleaseControls({ analysis, onChanged, shadowTrades }) {
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

  const suggestKept = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${aid}/keep/suggest`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ scope: analysis.scope || 'combined' }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d?.detail || 'Vorschlag fehlgeschlagen');
      toast.success(`${d.kept_count} Regime behalten, ${d.discarded_count} verworfen`);
      onChanged?.();
      loadChecks();
    } catch (e) { toast.error(e.message); }
    setBusy(false);
  };

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
        {stage === 'none' && (
          <button className="opt-chip rr-btn" disabled={busy} onClick={suggestKept}
            title="Behalten-Vorschlag: Regime mit mindestens 5 Abschnitten werden als „behalten“ markiert, dünne verworfen – ohne Häkchen scheitert das Freigabe-Gate. Gesetzte Häkchen bleiben."
            data-testid={`regime-release-suggest-kept-${aid}`}>
            <CheckSquare size={12} weight="bold" /> Behalten vorschlagen
          </button>
        )}
        {btn('shadow', 'Beobachten (Shadow)', Eye, `regime-release-shadow-${aid}`)}
        {btn('active', 'Wirksam schalten', Lightning, `regime-release-active-${aid}`)}
        {stage === 'none' && (
          <RegimeReleaseOverride aid={aid} stage="shadow" check={checks.shadow} onDone={onChanged} />
        )}
        {stage === 'shadow' && (
          <RegimeReleaseOverride aid={aid} stage="active" check={checks.active} onDone={onChanged} />
        )}
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
        Kalibrierung + Ablation gelaufen sind (Wirksam: zusätzlich Shadow-Trades je Struktur-Regime –
        dynamisch nach Erkennungs-Note: sehr gut 10 · gut 15 · mittel 25 · sonst 30; aktuell
        <b data-testid={`regime-release-min-trades-${aid}`}> {checks.active?.activation_min_trades ?? '…'}</b>).
        Mit „… manuell“ entscheidest du selbst (protokolliert; Wirksam erst ab Note „mittel“).
        {' '}Horizont-Band dieser Analyse: <b data-testid={`regime-release-band-${aid}`}>
          {/^(1m|3m|5m|15m|30m|1h|60m)$/.test(String(analysis.timeframe || '')) ? 'Intraday (Scalps)' : 'Swing (Swing-Trades)'}</b>
        {' '}– je Klasse ist eine Intraday- UND eine Swing-Freigabe möglich; eine neue Freigabe löst nur die des gleichen Bands ab.
      </div>
      <ReleaseWhatNow stage={stage} checks={checks} aid={aid} shadowTrades={shadowTrades} onChanged={onChanged} />
      {showHist && <ReleaseHistory analysis={analysis} />}
    </div>
  );
}

/** „Was jetzt?“ je Freigabe-Stufe: fehlende Nachweise sichtbar (nicht nur im Tooltip) + Shadow erklärt. */
/** Shadow-Phase beschleunigen: Struktur-Regime für bereits geschlossene KI-Trades nachtragen. */
function ShadowBackfillButton({ aid, onChanged }) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/shadow/backfill`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ aid }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d?.detail || 'Nachlabeln fehlgeschlagen');
      toast.success(`${d.labeled} von ${d.scanned} Trades mit Struktur-Regime nachgetragen`
        + (d.skipped_no_release ? ` · ${d.skipped_no_release} ohne freigegebene Analyse` : ''));
      onChanged?.();
    } catch (e) { toast.error(e.message); }
    setBusy(false);
  };
  return (
    <button className="opt-chip" onClick={run} disabled={busy} data-testid={`regime-shadow-backfill-${aid}`}
      title="Beschleunigt die Shadow-Phase ohne Qualitätsverlust: bereits geschlossene KI-Trades der letzten 90 Tage bekommen das Struktur-Regime zum Entry-Zeitpunkt nachgetragen – berechnet aus demselben Modell und nur aus Kerzen VOR dem Entry (kein Lookahead). Läuft nach jeder Freigabe auch automatisch im Hintergrund.">
      {busy ? '⏳ nachlabeln…' : '⚡ Historie nachlabeln'}
    </button>
  );
}

function ReleaseWhatNow({ stage, checks, aid, shadowTrades, onChanged }) {
  const sh = checks.shadow, ac = checks.active;
  if (stage === 'none') {
    if (!sh) return null;
    return (
      <div className="rl-whatnow" data-testid={`regime-release-whatnow-${aid}`}>
        <b>Was jetzt?</b>
        <div style={{ flex: 1, minWidth: 220 }}>
          {sh.ok ? (
            <>Alle Nachweise für <b style={{ color: 'inherit' }}>Beobachten (Shadow)</b> sind da – Knopf drücken, kurzen Grund eintragen.
              Danach musst du nichts weiter tun: der KI-Trader (Paper/Live) sammelt die Shadow-Trades von selbst.</>
          ) : (
            <>Für „Beobachten (Shadow)“ fehlt noch:
              <ul>{(sh.reasons || []).map((r, i) => <li key={i} data-testid={`regime-release-missing-${aid}-${i}`}>{reasonHint(r)}</li>)}</ul>
            </>
          )}
        </div>
      </div>
    );
  }
  if (stage === 'shadow') {
    return (
      <div className="rl-whatnow" data-testid={`regime-release-whatnow-${aid}`}>
        <b>Was jetzt?</b>
        <div style={{ flex: 1, minWidth: 220 }}>
          Shadow läuft: jeder KI-Trade bekommt das Struktur-Regime mitgeschrieben – ohne Wirkung auf Entscheidungen.
          Du musst nichts tun außer den KI-Trader laufen lassen. Stand: <b style={{ color: 'inherit' }}>{shadowTrades ?? 0}</b> Shadow-Trades.
          „Wirksam schalten“ wird frei, wenn je Struktur-Regime ≥ {ac?.activation_min_trades ?? 30} Trades vorliegen
          (Ziel richtet sich nach der Erkennungs-Note „{ac?.quality_grade || 'unbewertet'}“) und sich die Ø-Rewards
          der Regime um ≥ 0,25 R unterscheiden.
          <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <ShadowBackfillButton aid={aid} onChanged={onChanged} />
            <span className="opt-small" style={{ color: 'inherit', opacity: 0.8 }}>
              Schneller: geschlossene KI-Trades der letzten 90 Tage nachlabeln (gleiches Modell, nur Kerzen vor dem Entry).
            </span>
          </div>
          {ac && !ac.ok && (ac.reasons || []).length > 0 && (
            <ul>{ac.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="rl-whatnow" data-testid={`regime-release-whatnow-${aid}`}>
      <b>Was jetzt?</b>
      <div style={{ flex: 1, minWidth: 220 }}>
        Wirksam: der KI-Trader sieht das Struktur-Regime im Prompt; im KI-Trader-Panel kann die Gate-Quelle „Lab“
        gewählt werden. Bei Zweifeln jederzeit „Widerruf“ – dann ist alles wie ohne Freigabe.
      </div>
    </div>
  );
}

/** Fehlende Nachweise in Handlungs-Sprache übersetzen (Server-Text bleibt als Basis erhalten). */
function reasonHint(r) {
  const t = String(r || '');
  if (t.startsWith('Kalibrierung fehlt')) return `${t} → oben unter „1“ mit denselben Coins/Timeframe „Wissenschaftlich kalibrieren“ starten.`;
  if (t.startsWith('Ablation fehlt')) return `${t} → oben unter „1b · C“ die Ablation mit denselben Coins/Timeframe starten.`;
  if (t.startsWith('Ablation ohne auswertbare')) return `${t} → Ablation liefert für dieses Grundgerüst keine Holdout-Zahlen der Alternative; Ablation mit Grundgerüst „Umkehrpunkte“ oder „Kombi“ wiederholen.`;
  if (t.startsWith('Ablation:')) return `${t} → die einfache Alternative war im Holdout besser; Erkennung verbessern (Autopilot) und neu analysieren.`;
  if (t.startsWith("kein Regime als 'behalten'")) return `${t} → „Behalten vorschlagen“ klicken.`;
  if (t.startsWith('Regime ') && t.includes('Abschnitte')) return `${t} → dieses Regime bei „behalten“ abhaken (verwerfen) oder längeren Zeitraum analysieren.`;
  return t;
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
  const BAND_TXT = { intraday: 'Intraday', swing: 'Swing' };
  const byClass = {};
  (data.releases || []).forEach(r => (r.release?.asset_classes || []).forEach(c => {
    (byClass[c] = byClass[c] || []).push(r);
  }));
  const act = data.activation || {};
  const rewards = (data.rewards_by_structural || []).filter(r => r.regime !== 'unbekannt');
  const allRel = Object.values(byClass).flat();

  return (
    <div className="rr-stage-panel" data-testid="ai-structural-stage-panel">
      <div className="rr-stage-title">STRUKTUR-REGIME (LAB-BRÜCKE)</div>
      <div className="rr-stage-row">
        {Object.keys(CLASS_LABELS).map(cls => {
          const rels = byClass[cls] || [];
          if (!rels.length) {
            return (
              <span key={cls} className="rr-badge none" data-testid={`ai-structural-stage-${cls}`}
                title="keine freigegebene Lab-Analyse">{CLASS_LABELS[cls]}: keine</span>
            );
          }
          return rels.map(r => (
            <span key={`${cls}-${r.id}`} className={`rr-badge ${r.release.stage}`}
              data-testid={`ai-structural-stage-${cls}${rels.length > 1 ? `-${r.band}` : ''}`}
              title={`${r.name} · ${r.timeframe} · seit ${fmtDateTime(r.release.since)}`}>
              {CLASS_LABELS[cls]}{rels.length > 1 ? ` ${BAND_TXT[r.band] || ''}` : ''}: {STAGE_LABEL[r.release.stage]}
              {' '}<span className="opt-small">({r.timeframe})</span>
            </span>
          ));
        })}
      </div>
      <div className="opt-small" data-testid="ai-structural-activation">
        Shadow-Stichprobe: {act.shadow_trades ?? 0} Trades (Ziel {act.min_trades ?? 30} je Struktur-Regime)
        {rewards.length ? ` · ${rewards.map(r => `${r.regime} n=${r.trades} Ø ${r.avg_reward >= 0 ? '+' : ''}${r.avg_reward}`).join(' · ')}` : ''}
        {' · '}{act.ok ? 'Wirksam-Gate grün' : `Wirksam noch nicht möglich: ${(act.reasons || []).join('; ')}`}
      </div>
      <div className="opt-small" data-testid="ai-structural-timeframe-note">
        Je Anlageklasse bis zu zwei Freigaben: <b>Intraday</b> (Analyse ≤ 1h) für Scalps und <b>Swing</b> (≥ 2h)
        für Swing-Trades – die KI nutzt die zum Trade-Horizont passende. Gibt es nur eine Freigabe, gilt sie für
        alle Trades der Klasse{allRel.length ? ` (aktuell: ${[...new Set(allRel.map(r => `${BAND_TXT[r.band] || r.band} ${r.timeframe}`))].join(' / ')})` : ''}.
        Shadow = nur mitschreiben; Wirksam = Kontext im Prompt, Sperre nur bei Gate-Quelle „Lab“. Normale Backtests bleiben davon unberührt.
        Ist das Shadow-Ziel erreicht, kommt eine Telegram-Meldung.
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
