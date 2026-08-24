import React, { useState } from 'react';
import { ChartBar, CaretDown } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import TradeAIDetails, { SETUP_EXPLAIN } from './TradeAIDetails';
import TradeChart from './TradeChart';

const API_URL = process.env.REACT_APP_BACKEND_URL;

export const fmtTime = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('de-DE', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      second: '2-digit', timeZone: 'Europe/Berlin',
    });
  } catch { return '—'; }
};

export const fmtDur = (s) => {
  if (s == null) return '—';
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
};

export const fmtPct = (p) => (p == null ? '' : `${p > 0 ? '+' : ''}${p}%`);

// Kompakte Uhrzeit für die Trade-Liste: "14.06. 13:42"
export const fmtTimeShort = (iso) => {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('de-DE', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      timeZone: 'Europe/Berlin',
    }).replace(',', '');
  } catch { return ''; }
};

// One price level row in the ladder (Entry / SL / TP1 / Full-TP / Exit)
const LevelRow = ({ label, value, pct, cls, hit }) => {
  if (value == null || value === 0) return null;
  return (
    <div className={`lvl-row ${cls || ''}`}>
      <span className="lvl-label">{label}{hit ? ' ✓' : ''}</span>
      <span className="lvl-value mono">{value}</span>
      {pct != null && <span className="lvl-pct mono">{fmtPct(pct)}</span>}
    </div>
  );
};

// Offener Trade: nur noch der Schließen-Button (das frühere "Trade steuern"-
// Interface wurde auf Nutzerwunsch entfernt – SL/TP managt die KI bzw. Bitunix).
const OpenTradeActions = ({ t, onChanged }) => {
  const [busy, setBusy] = useState(false);

  const closeNow = async () => {
    if (!window.confirm(`${t.side} ${t.symbol} wirklich komplett schließen?`)) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/autotrade/close/${t.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Aktion fehlgeschlagen');
      toast.success(`Trade geschlossen · PnL ${data.result?.realized_pnl ?? '–'} USDT`);
      onChanged && onChanged();
    } catch (e) { toast.error(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="ota" data-testid={`open-trade-actions-${t.id}`}>
      <button className="ota-btn ota-close" onClick={closeNow} disabled={busy}
        data-testid={`ota-close-${t.id}`}>
        {busy ? 'Schließt…' : 'Trade schließen'}
      </button>
    </div>
  );
};

const TradeDetailCard = ({ t, stratName, getCoinName, onChanged, onShowChart }) => {
  const [open, setOpen] = useState(false);
  const [showTradeChart, setShowTradeChart] = useState(false);
  const c = t.computed || {};
  const isLive = t.mode === 'live';
  const closed = t.status === 'closed';
  const resultMeta = t.result === 'win'
  ? { label: 'G', cls: 'res-win' }
  : t.result === 'loss'
    ? { label: 'V', cls: 'res-loss' }
    : t.result === 'breakeven'
      ? { label: 'BEP', cls: 'res-be' }
      : { label: 'OFFEN', cls: 'res-open' };
  const pnl = (!closed && c.live_pnl != null) ? c.live_pnl : (t.realized_pnl || 0);
  // Eine PnL-%-Quelle für ALLE Anzeigen (Trade-Liste, Chart-Badge):
  // IMMER inkl. Gebühren (auf Margin) – passend zum $-Wert daneben.
  // Der reine Kurs-PnL (ohne Gebühren, wie Bitunix) steht in den Details.
  const pnlPct = closed
    ? (c.pnl_pct_margin ?? c.pnl_pct)
    : (c.pnl_pct_margin ?? c.upnl_pct_margin ?? c.pnl_pct);

  return (
    <div className={`tdc ${open ? 'tdc-open' : ''}`} data-testid={`trade-card-${t.id}`}>
      <button className="tdc-head" onClick={() => setOpen(o => !o)} data-testid={`trade-card-toggle-${t.id}`}>
  <span className="tdc-main">
    <span className={`mode-tag ${isLive ? 'mode-live' : 'mode-paper'}`} data-testid={`trade-mode-${t.id}`}>
      {isLive ? 'LIVE' : 'PAPER'}
    </span>
    <span className={`badge ${t.side === 'LONG' ? 'badge-long' : 'badge-short'}`}>{t.side}</span>
    <span className={`tdc-result ${resultMeta.cls}`}>{resultMeta.label}</span>
    <span className="tdc-coin-wrap">
      <span className="mono text-secondary tdc-coin">{getCoinName(t.symbol)}</span>
      {t.horizon === 'swing' && <sup className="tdc-sup tag-swing" title={`Swing-Trade${t.runner ? ' · Runner' : ''}`} data-testid={`trade-swing-badge-${t.id}`}>S{t.runner ? '·R' : ''}</sup>}
      {t.data_collection && <sup className="tdc-sup tag-daten" title="Datensammel-Modus: reiner Paper-Trade zum ML-Datensammeln (zählt nicht zur Live-Performance)" data-testid={`trade-collection-badge-${t.id}`}>D</sup>}
    </span>
    <span className="tdc-pnl-group">
      <span className={`mono tdc-pnl ${pnl >= 0 ? 'text-long' : 'text-short'}`}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}</span>
      <CaretDown size={13} className={`tdc-caret ${open ? 'rot' : ''}`} />
    </span>
  </span>
  <span className="tdc-sub">
    {pnlPct != null && (
      <span className={`mono tdc-pnl-pct ${pnlPct >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-pnl-pct-${t.id}`}>
        ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
      </span>
    )}
  </span>
  <span className="tdc-strat-line">
    <span className="tdc-strat-name" title={stratName(t)}>{stratName(t)}</span>
    <span className="tdc-times mono" title={closed ? 'Eröffnet → Geschlossen (Berlin-Zeit)' : 'Eröffnet (Berlin-Zeit)'} data-testid={`trade-times-${t.id}`}>
      {fmtTimeShort(t.opened_at)}{closed ? ` → ${fmtTimeShort(t.closed_at)}` : ''}
    </span>
  </span>
</button>

      {open && (
        <div className="tdc-body" data-testid={`trade-card-body-${t.id}`}>
          <div className="tdc-chart-row">
            {onShowChart && (
              <button className="tdc-chart-btn" onClick={() => onShowChart(t.symbol)}
                title={`Live-Chart von ${getCoinName(t.symbol)} im Hauptfenster öffnen, um den Trade genauer anzusehen`}
                data-testid={`trade-open-chart-${t.id}`}>
                <ChartBar size={13} weight="bold" /> Live-Chart {getCoinName(t.symbol)} öffnen
              </button>
            )}
            {/* Trade-Chart nur, wenn es für das Symbol Bitunix-Kerzen gibt
                (Forex wie GBPUSD hat keinen Bitunix-Kontrakt -> Button aus) */}
            {closed && c.chart_available !== false && (
              <button className="tdc-chart-btn" onClick={() => setShowTradeChart(v => !v)}
                title="Schlichter Chart des Trade-Zeitraums mit Entry/SL/TP/Exit – Kerzen werden erst beim Öffnen von Bitunix geladen (keine Dauerlast, kein Speicher)"
                data-testid={`trade-history-chart-btn-${t.id}`}>
                <ChartBar size={13} weight="bold" /> {showTradeChart ? 'Trade-Chart ausblenden' : 'Trade-Chart'}
              </button>
            )}
          </div>
          {closed && showTradeChart && <TradeChart trade={t} />}
          <div className="tdc-ladder">
            {/* Preis-Leiter dynamisch nach Kurs sortiert: der aktuelle Kurs
                (bzw. Exit) rutscht an die Stelle, wo er wirklich steht –
                über dem Entry, über TP1 usw. */}
            {[
              { k: 'tpf', label: `TP Full (${c.rr_tpf || '?'}R)`, value: t.tpf, pct: c.tpf_distance_pct, cls: 'lvl-tp' },
              { k: 'tp1', label: `TP1 (${c.rr_tp1 || '?'}R)`, value: t.tp1, pct: c.tp1_distance_pct, cls: 'lvl-tp1', hit: t.tp1_hit },
              { k: 'entry', label: 'Entry', value: t.entry, pct: 0, cls: 'lvl-entry' },
              ...(!closed && c.current_price != null
                ? [{ k: 'cur', label: 'Aktueller Kurs', value: c.current_price, pct: c.price_distance_pct, cls: 'lvl-current' }] : []),
              ...(closed
                ? [{ k: 'exit', label: 'Exit', value: t.exit_price, pct: c.exit_distance_pct, cls: 'lvl-exit' }] : []),
              ...(c.peak_price != null
                ? [{ k: 'peak', label: t.side === 'LONG' ? 'Höchststand im Trade' : 'Tiefststand im Trade', value: c.peak_price, pct: c.peak_distance_pct, cls: 'lvl-peak' }] : []),
              ...(c.trough_price != null
                ? [{ k: 'trough', label: t.side === 'LONG' ? 'Tiefster Gegenlauf' : 'Höchster Gegenlauf', value: c.trough_price, pct: c.trough_distance_pct, cls: 'lvl-trough' }] : []),
              { k: 'sl', label: `SL${c.sl_moved ? ' (aktuell)' : ''}`, value: t.sl, pct: c.sl_distance_pct, cls: 'lvl-sl' },
              ...(c.sl_moved
                ? [{ k: 'sl0', label: 'SL initial', value: t.initial_sl, pct: c.initial_sl_distance_pct, cls: 'lvl-sl-init' }] : []),
            ]
              .filter(r => r.value != null && r.value !== 0)
              .sort((a, b) => b.value - a.value)
              .map(r => (
                <LevelRow key={r.k} label={r.label} value={r.value} pct={r.pct} cls={r.cls} hit={r.hit} />
              ))}
          </div>

          <div className="tdc-meta">
            <div className="tdc-meta-item" title="Zeitpunkt, zu dem der Trade eröffnet wurde."><span>Eröffnet</span><b className="mono">{fmtTime(t.opened_at)}</b></div>
            {(() => {
              const isManual = t.manual_trade || t.strategy_id === 'external';
              const origin = isManual
                ? 'Manuell (Bitunix)'
                : t.adopted_from_limit
                  ? `Website · ${t.strategy_name || t.strategy_id || 'Bot'} (Limit-Fill · Watchdog)`
                  : `Website · ${t.strategy_name || t.strategy_id || 'Bot'}`;
              return (
                <div className="tdc-meta-item" title={isManual
                  ? 'Diese Position wurde NICHT von der Website eröffnet (kein KIT-clientId-Tag an der Börse) – z.B. Bitunix-App, Copy-Trading oder externes Tool. Der Watchdog hat sie übernommen und überwacht sie.'
                  : `Von der Website platziert${t.bitunix_client_id ? ` – Börsen-clientId: ${t.bitunix_client_id}` : ''}. Jede Bot-Order trägt eine KIT-…-clientId, damit die Herkunft an der Börse beweisbar ist.`}
                  data-testid={`trade-origin-${t.id}`}>
                  <span>Herkunft</span>
                  <b className="mono" style={{ color: isManual ? '#f0b90b' : 'var(--long, #0ecb81)' }}>
                    {origin}{!isManual && t.bitunix_client_id ? ` · ${t.bitunix_client_id}` : ''}
                  </b>
                </div>
              );
            })()}
            {closed && <div className="tdc-meta-item" title="Zeitpunkt, zu dem der Trade komplett geschlossen wurde."><span>Geschlossen</span><b className="mono">{fmtTime(t.closed_at)}</b></div>}
            {!closed && c.current_price != null && (
              <div className="tdc-meta-item" title="Letzter Live-Preis des Coins – Basis für den unrealisierten PnL." data-testid={`trade-current-price-${t.id}`}><span>Aktueller Kurs</span><b className="mono">{c.current_price}</b></div>
            )}
            {!closed && c.unrealized_pnl != null && (
              <div className="tdc-meta-item" title="Gewinn/Verlust in $, wenn du JETZT zum aktuellen Kurs schließen würdest – OHNE Gebühren. (Kurs − Entry) × Menge, bei Short umgekehrt."><span>Unrealisierter PnL</span><b className={`mono ${c.unrealized_pnl >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-unrealized-pnl-${t.id}`}>{c.unrealized_pnl >= 0 ? '+' : ''}{c.unrealized_pnl.toFixed(2)} $</b></div>
            )}
            {!closed && c.live_pnl != null && (
              <div className="tdc-meta-item" title="Wie Unrealisierter PnL, aber abzüglich aller schon angefallenen Gebühren (Entry-Fee, ggf. TP1-Teilverkauf) – der ehrliche „was bleibt wirklich übrig“-Wert."><span>Live PnL (inkl. Gebühren)</span><b className={`mono ${c.live_pnl >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-live-pnl-${t.id}`}>{c.live_pnl >= 0 ? '+' : ''}{c.live_pnl.toFixed(2)} $</b></div>
            )}
            <div className="tdc-meta-item" title="Wie lange der Trade offen ist bzw. war."><span>Dauer</span><b className="mono">{fmtDur(c.duration_seconds)}</b></div>
            <div className="tdc-meta-item" title={`Bester Stand innerhalb des Trades (MFE): bei Long der höchste, bei Short der tiefste Kurs seit Entry – zeigt, wie weit der Trade maximal im Plus war. Offene Trades: live berechnet. Geschlossene: wird beim Trade-Management gespeichert (bei vollem TP z.B. der TP-Kurs) – ältere Trades ohne Aufzeichnung zeigen „—“.`}>
              <span>{t.side === 'LONG' ? 'Höchststand im Trade' : 'Tiefststand im Trade'}</span>
              <b className="mono" data-testid={`trade-peak-${t.id}`}>
                {c.peak_price != null
                  ? `${c.peak_price}${c.mfe_pct != null ? ` (${c.mfe_pct >= 0 ? '+' : ''}${c.mfe_pct}% max)` : ''}`
                  : '—'}
              </b>
            </div>
            <div className="tdc-meta-item" title={`Schlechtester Stand innerhalb des Trades (MAE): wie weit der Kurs maximal GEGEN die Trade-Richtung gelaufen ist. Zusammen mit dem Best-Stand zeigt das die Qualität des Entry-Timings. Ältere Trades ohne Aufzeichnung zeigen „—“.`}>
              <span>{t.side === 'LONG' ? 'Tiefster Gegenlauf' : 'Höchster Gegenlauf'}</span>
              <b className="mono" data-testid={`trade-trough-${t.id}`}>
                {c.trough_price != null
                  ? `${c.trough_price}${c.mae_pct != null ? ` (${c.mae_pct >= 0 ? '+' : ''}${c.mae_pct}%)` : ''}`
                  : '—'}
              </b>
            </div>
            <div className="tdc-meta-item" title="PnL gemessen in „R“ = geplantes Risiko (Abstand Entry → initialer SL × Menge). +1R = einen Risiko-Einsatz gewonnen, −1R = SL voll getroffen. Macht Trades unterschiedlicher Größe vergleichbar – die KI wird u.a. daran gemessen."><span>R-Vielfaches</span><b className={`mono ${(c.r_multiple || 0) >= 0 ? 'text-long' : 'text-short'}`}>{c.r_multiple != null ? `${c.r_multiple}R` : '—'}</b></div>
            {!closed && c.upnl_pct_margin != null && (
              <div className="tdc-meta-item" title="Reiner Kurs-PnL in % der Margin, OHNE Gebühren – exakt die Zahl, die Bitunix in der Position anzeigt. Der Hebel wirkt voll: 1% Kursbewegung × Hebel 10 = 10%. Unterschied zu „PnL % Margin (inkl. Gebühren)“ = die Gebühren."><span>uPnL % Margin (ohne Gebühren · wie Bitunix)</span><b className={`mono ${(c.upnl_pct_margin || 0) >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-upnl-pct-margin-${t.id}`}>{fmtPct(c.upnl_pct_margin)}</b></div>
            )}
            <div className="tdc-meta-item" title="PnL INKL. aller Gebühren in % der eingesetzten Margin – dein tatsächlicher Return auf das gebundene Kapital. Dieselbe Zahl wie in der Standard-Ansicht unter dem $-Gewinn."><span>PnL % Margin (inkl. Gebühren)</span><b className={`mono ${(c.pnl_pct_margin || 0) >= 0 ? 'text-long' : 'text-short'}`} data-testid={`trade-pnl-pct-margin-${t.id}`}>{c.pnl_pct_margin != null ? fmtPct(c.pnl_pct_margin) : '—'}</b></div>
            <div className="tdc-meta-item" title="Geplantes Risiko in $: Abstand Entry → initialer SL × Menge. Das verlierst du (plus Gebühren), wenn der SL voll trifft."><span>Risk</span><b className="mono">{c.risk_usd ? `${c.risk_usd} $` : '—'}</b></div>
            {(c.rr_tp1 || c.rr_tpf) && (
              <div className="tdc-meta-item" title="Chance-Risiko-Verhältnis des Setups: Wie viel R (Risiko-Einheiten) an TP1 bzw. TP Full winken. 2R bedeutet: doppelte Gewinnchance gegenüber dem geplanten Risiko." data-testid={`trade-crv-${t.id}`}><span>CRV (TP1 / TP Full)</span><b className="mono">{c.rr_tp1 ?? '—'}R / {c.rr_tpf ?? '—'}R</b></div>
            )}
            {c.entry_fee != null && (
              <div className="tdc-meta-item" title={`Gebühr beim Eröffnen der Position: ${c.fee_percent ?? 0.06}% des Positionswerts (Entry × Menge). Wird sofort vom PnL abgezogen.`} data-testid={`trade-entry-fee-${t.id}`}><span>Entry-Fee ({c.fee_percent ?? 0.06}%)</span><b className="mono text-short">-{Number(c.entry_fee).toFixed(4)} $</b></div>
            )}
            {closed && c.exit_fee != null && (
              <div className="tdc-meta-item" title={`Gebühren beim Schließen (inkl. Teil-Exits wie TP1): ${c.fee_percent ?? 0.06}% des jeweiligen Exit-Volumens.`} data-testid={`trade-close-fee-${t.id}`}><span>Close-Fee(s) ({c.fee_percent ?? 0.06}%)</span><b className="mono text-short">-{Number(c.exit_fee).toFixed(4)} $</b></div>
            )}
            {!closed && c.est_close_fee != null && (
              <div className="tdc-meta-item" title={`Geschätzte Gebühr beim Schließen der Restposition zum aktuellen Kurs (${c.fee_percent ?? 0.06}% des Restvolumens).`} data-testid={`trade-est-close-fee-${t.id}`}><span>Close-Fee (geschätzt)</span><b className="mono text-short">≈{Number(c.est_close_fee).toFixed(4)} $</b></div>
            )}
            {c.fees_total_est != null && (
              <div className="tdc-meta-item" title={closed
                ? 'Alle tatsächlich gezahlten Gebühren dieses Trades: Entry-Fee + alle Close-Fees (inkl. Teil-Exits).'
                : `Bisher gezahlt: ${Number(c.fees_paid ?? 0).toFixed(4)} $ (Entry + evtl. Teil-Exits) + geschätzte Close-Fee = voraussichtliche Gesamt-Gebühren.`}
                data-testid={`trade-fees-total-${t.id}`}>
                <span>Gebühren gesamt{closed ? '' : ' (≈ inkl. Close)'}</span>
                <b className="mono text-short">{closed ? '-' : '≈'}{Number(c.fees_total_est).toFixed(4)} $</b>
              </div>
            )}
            {(() => {
              const risk = c.risk_usd || ((t.qty && t.entry && (t.initial_sl || t.sl))
                ? Math.abs(t.entry - (t.initial_sl || t.sl)) * t.qty : 0);
              const fees = c.fees_total_est != null ? c.fees_total_est
                : (closed ? (t.fees_paid || 0) : (t.fees_paid || 0) * 2);
              if (!risk || !fees) return null;
              const ratio = fees / risk * 100;
              const color = ratio >= 50 ? 'var(--short, #f6465d)' : ratio >= 25 ? '#f0b90b' : 'var(--long, #0ecb81)';
              return (
                <div className="tdc-meta-item" title={`Wie viel deines geplanten Risikos allein die Gebühren auffressen. Beispiel: Risk ${risk.toFixed(2)} $ und ${fees.toFixed(2)} $ Gebühren → ${ratio.toFixed(0)}% deines möglichen SL-Verlusts gehen NUR an die Börse. Praktisch heißt das: dein Trade muss erst die Gebühren verdienen, bevor echter Gewinn entsteht. Unter 25% = gesund, 25-50% = teuer, über 50% = Stop zu eng oder Position zu groß.`}>
                  <span>Fees vs. Risiko</span>
                  <b className="mono" style={{ color }} data-testid={`trade-fees-vs-risk-${t.id}`}>
                    ≈{ratio.toFixed(0)}% ({fees.toFixed(2)} $)
                  </b>
                </div>
              );
            })()}
            <div className="tdc-meta-item" title="Gewählter Hebel: Positionswert = Kapital × Hebel. Höherer Hebel = gleiche Kursbewegung wirkt stärker auf die Margin – und die Gebühren steigen mit, weil sie auf den vollen Positionswert berechnet werden."><span>Hebel</span><b className="mono">{t.leverage ? `${t.leverage}x` : '—'}</b></div>
            <div className="tdc-meta-item" title="Eingesetzte Margin in $ – das für diesen Trade gebundene Kapital."><span>Kapital</span><b className="mono">{t.max_capital ? `${t.max_capital} $` : '—'}</b></div>
            <div className="tdc-meta-item" title="Gehandelte Stückzahl des Coins: Positionswert (Kapital × Hebel) ÷ Entry-Preis."><span>Menge</span><b className="mono">{t.qty ?? '—'}</b></div>
            {c.notional_usdt != null && (
              <div className="tdc-meta-item" title="Positionsgröße in $ (Notional): Entry-Preis × Menge = Kapital × Hebel. Auf diesen Wert werden auch die Gebühren berechnet." data-testid={`trade-notional-${t.id}`}><span>Positionsgröße</span><b className="mono">{Number(c.notional_usdt).toFixed(2)} $</b></div>
            )}
            {(() => {
              const n = (t.events || []).filter(ev => String(ev).includes('TEIL-EXIT') || String(ev).includes('PARTIAL CLOSE')).length;
              if (!n) return null;
              return (
                <div className="tdc-meta-item" title="Gestaffelte Teil-Exits der KI (eigene TP-Leiter TP2/TP3 bzw. Teil-Absicherung statt hartem Voll-Exit) – Details je Stufe im Verlauf unten." data-testid={`trade-partial-exits-${t.id}`}>
                  <span>Teil-Exits (KI-Staffel)</span><b className="mono">{n}×</b>
                </div>
              );
            })()}
          </div>

          {(t.ai_reasoning || t.setup || t.decision_id || t.strategy_id === 'ai_trader') && (
            <div className="tdc-ai" data-testid={`trade-ai-context-${t.id}`}>
              <div className="tdc-tl-title">KI-BEGRÜNDUNG</div>
              <div className="tdc-ai-tags">
                {t.setup && <span className="badge badge-setup" title={SETUP_EXPLAIN[t.setup] || 'Gehandeltes Setup aus dem Strategie-Playbook der KI'} data-testid={`trade-ai-setup-${t.id}`}>{String(t.setup).replace(/_/g, ' ')}</span>}
                {t.ai_confidence != null && <span className="badge" title="Wie sicher sich die KI bei diesem Entry war (0-100%). Nur Entscheidungen über der eingestellten Mindest-Konfidenz werden überhaupt gehandelt.">Konfidenz {t.ai_confidence}%</span>}
                {t.ai_news_impact && t.ai_news_impact !== 'neutral' && <span className="badge" title="Einschätzung der Nachrichtenlage zum Entry-Zeitpunkt: bullish/positive = News sprachen für steigende Kurse, bearish/negative = für fallende. Die konkreten Schlagzeilen stehen in der vollen Begründung (Details).">News: {t.ai_news_impact}</span>}
              </div>
              {t.ai_reasoning && <div className="tdc-ai-text" data-testid={`trade-ai-reasoning-${t.id}`}>{t.ai_reasoning}</div>}
              <TradeAIDetails trade={t} onChanged={onChanged} />
            </div>
          )}
          {(t.events || []).length > 0 && (
            <div className="tdc-timeline" data-testid={`trade-timeline-${t.id}`}>
              <div className="tdc-tl-title">VERLAUF</div>
              {t.events.map((ev, i) => (
                <div key={i} className="tdc-tl-item"><span className="tdc-tl-dot" />{ev}</div>
              ))}
            </div>
          )}

          {!closed && isAdmin() && (
            <OpenTradeActions t={t} onChanged={onChanged} />
          )}
        </div>
      )}
    </div>
  );
};

export default TradeDetailCard;
