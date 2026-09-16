import React, { useState, useEffect, useCallback } from 'react';
import { Clock, Gear, ChartLineUp, Wallet, TrendUp, TrendDown, Lock, LockOpen, Trophy, ClockCounterClockwise, MagicWand, ChartScatter, Drop, BellRinging, Flask, MoonStars } from '@phosphor-icons/react';
import { authHeaders } from '../auth';
import CapitalModal from './CapitalModal';
import SafeOverlay from './SafeOverlay';
import './Header.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const LIVE_BADGE_KEY = 'kit_live_badge_source'; // 'bitunix' | 'ibkr'

// Sicherheitsstatus-Ampel (Audit 2.4): pollt GET /api/safety/status (admin) und
// zeigt ok/warn/critical als farbigen Punkt. Details im Tooltip; bei critical
// blockt das Backend neue Live-Trades bereits selbst (entry_guard).
const SafetyLight = ({ adminAuthed }) => {
  const [safety, setSafety] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const r = await fetch(`${API_URL}/api/safety/status`, { headers: authHeaders() });
        if (!r.ok) { if (!stopped) setSafety(null); return; }
        const d = await r.json();
        if (!stopped) setSafety(d);
      } catch (_) { if (!stopped) setSafety(null); }
    };
    load();
    const t = setInterval(load, 60000);
    return () => { stopped = true; clearInterval(t); };
  }, [adminAuthed]); // nach Login/Logout sofort neu laden (nicht erst nach 60s)

  if (!safety) return null; // nicht eingeloggt / Status nicht abrufbar
  const level = safety.level || 'ok';
  const issues = (safety.checks || []).filter(c => c.level !== 'ok');
  const title = level === 'ok'
    ? 'Sicherheitsstatus: OK'
    : `Sicherheitsstatus: ${level.toUpperCase()} – ${issues.map(c => c.detail).join(' | ')}`;
  return (
    <div className={`safety-light safety-${level}`} title={title} onClick={() => setOpen(o => !o)}
         style={{ cursor: 'pointer', position: 'relative' }}
         data-testid="safety-status-light" data-level={level}>
      <span className="safety-dot" />
      {level !== 'ok' && (
        <span className="safety-label" data-testid="safety-status-label">
          {level === 'critical' ? 'KRITISCH' : 'WARNUNG'}
        </span>
      )}
      {open && (
        <div data-testid="safety-status-details"
          style={{ position: 'absolute', top: '120%', left: 0, zIndex: 90, minWidth: 240, maxWidth: 330,
                   background: '#0B1220', border: '1px solid #24304a', borderRadius: 8,
                   padding: '8px 10px', fontSize: 11, lineHeight: 1.5, boxShadow: '0 6px 24px rgba(0,0,0,0.5)' }}
          onClick={e => e.stopPropagation()}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Sicherheitsstatus: {level.toUpperCase()}</div>
          {(safety.checks || []).map(c => (
            <div key={c.name} style={{ color: c.level === 'critical' ? '#FF3366' : c.level === 'warn' ? '#FFB020' : '#7f8ea8' }}>
              {c.level === 'ok' ? '✓' : '⚠'} {c.detail}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};


// Einfach-Klick (verzögert) vs. Doppelklick sauber trennen – über die Klick-
// Zählung selbst (nicht über das native dblclick-Event, das in Overlays/Touch-
// Browsern nicht zuverlässig feuert): zwei Klicks binnen 350 ms = Doppelklick,
// sonst feuert der Einfach-Klick nach Ablauf der Wartezeit.
const DOUBLE_CLICK_MS = 350;
const useClickOrDouble = (onSingle, onDouble) => {
  const timer = React.useRef(null);
  const lastTs = React.useRef(0);
  const onClick = useCallback((e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    const now = Date.now();
    if (timer.current && now - lastTs.current < DOUBLE_CLICK_MS) {
      clearTimeout(timer.current);
      timer.current = null;
      lastTs.current = 0;
      onDouble(e);
      return;
    }
    lastTs.current = now;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { timer.current = null; onSingle(e); }, DOUBLE_CLICK_MS);
  }, [onSingle, onDouble]);
  // Natives dblclick nur schlucken (Toggle läuft bereits über die Klick-Zählung)
  const onDoubleClick = useCallback((e) => { if (e && e.preventDefault) e.preventDefault(); }, []);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return { onClick, onDoubleClick };
};

// Reiner ANZEIGE-Offset für den Bitunix-Kontostand (nur Optik im Header-Widget,
// fließt in KEINE Berechnung/Kapital-Zuweisung ein). Läuft am Stichtag automatisch ab.
const BAL_DISPLAY_OFFSET_USDT = 400;
const BAL_DISPLAY_OFFSET_UNTIL = '2026-10-05T23:59:59Z';
const balDisplayOffset = () =>
  (Date.now() < Date.parse(BAL_DISPLAY_OFFSET_UNTIL) ? BAL_DISPLAY_OFFSET_USDT : 0);

// Inhalt des Live-Badges: Standard Bitunix (1:1 wie bisher), per Doppelklick
// IBKR-Kontostand (Forex-Live-Konto) – kleines Broker-Mini-Badge zeigt die Quelle.
const LiveBadgeStack = ({ bal, showIbkr, freeValue, testPrefix }) => {
  const ibkr = bal.ibkr || {};
  if (showIbkr) {
    const nl = ibkr.net_liquidation;
    const af = ibkr.available_funds;
    const err = !ibkr.configured ? 'nicht konfiguriert'
      : ibkr.authenticated === false ? 'nicht eingeloggt' : (ibkr.error ? 'API-Fehler' : null);
    return (
      <div className="bw-stack" data-testid={`${testPrefix}-ibkr-stack`}>
        <span className="bw-usdt-label">
          {ibkr.currency || 'USD'}
          <span className="bw-broker-mini ibkr" data-testid={`${testPrefix}-broker-mini`}>IBKR</span>
        </span>
        <span className="bw-primary-value mono" data-testid={`${testPrefix}-ibkr-balance`}>
          {nl != null ? Number(nl).toFixed(2) : (err || '—')}
        </span>
        <span className="bw-sub-line" data-testid={`${testPrefix}-ibkr-free`}>
          <span className="bw-sub-label">frei</span>
          <span className="mono">{af != null ? Number(af).toFixed(2) : '—'}</span>
        </span>
      </div>
    );
  }
  return (
    <div className="bw-stack" data-testid={testPrefix === 'bw' ? 'bw-live-stack' : `${testPrefix}-live-stack`}>
      <span className="bw-usdt-label">
        USDT
        <span className="bw-broker-mini bitunix" data-testid={`${testPrefix}-broker-mini`}>Bitunix</span>
      </span>
      <span className="bw-primary-value mono" data-testid={testPrefix === 'bw' ? 'bw-total' : `${testPrefix}-balance`}>
        {bal.margin_balance != null ? (Number(bal.margin_balance) + balDisplayOffset()).toFixed(2) : (bal.bitunix_error ? 'API-Fehler' : '—')}
      </span>
      {freeValue}
    </div>
  );
};

// IBKR-Konto-Fenster: Einfach-Klick aufs Live-Badge, wenn per Doppelklick auf
// IBKR umgeschaltet wurde (Kapital-Zuweisung gibt es nur für Bitunix).
const IBKRAccountModal = ({ bal, onClose }) => {
  const [st, setSt] = useState(null);
  useEffect(() => {
    let stop = false;
    fetch(`${API_URL}/api/ibkr/status`).then(r => r.json())
      .then(d => { if (!stop) setSt(d); })
      .catch(() => { if (!stop) setSt({ error: 'Status nicht abrufbar' }); });
    return () => { stop = true; };
  }, []);
  const ibkr = bal?.ibkr || {};
  const authed = st ? st.authenticated : ibkr.authenticated;
  const rowStyle = { display: 'flex', justifyContent: 'space-between', gap: 16, padding: '8px 0', borderBottom: '1px solid #1e2130', fontSize: 13 };
  const labStyle = { color: '#A1A4B0' };
  const statusTxt = !((st && st.configured) ?? ibkr.configured)
    ? 'nicht konfiguriert'
    : authed ? 'eingeloggt' : 'nicht eingeloggt';
  const statusColor = authed ? '#00FF66' : '#FFB74D';
  return (
    <SafeOverlay className="sb-overlay" onClose={onClose} testId="ibkr-account-overlay">
      <div className="sb-panel" style={{ maxWidth: 420 }} data-testid="ibkr-account-modal">
        <div className="sb-header">
          <h2 style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Wallet size={16} weight="fill" /> IBKR-KONTO (FOREX LIVE)
          </h2>
          <button className="sb-close" onClick={onClose} data-testid="ibkr-account-close" title="Schließen">✕</button>
        </div>
        <div className="sb-content" style={{ paddingTop: 12 }}>
          <div style={rowStyle}>
            <span style={labStyle}>Status</span>
            <b style={{ color: statusColor }} data-testid="ibkr-account-status">{statusTxt}</b>
          </div>
          <div style={rowStyle}>
            <span style={labStyle}>Konto-ID</span>
            <span className="mono" data-testid="ibkr-account-id">{st?.account_id || ibkr.account_id || '—'}</span>
          </div>
          <div style={rowStyle}>
            <span style={labStyle}>Net Liquidation</span>
            <span className="mono" data-testid="ibkr-account-nl">
              {ibkr.net_liquidation != null ? `${Number(ibkr.net_liquidation).toFixed(2)} ${ibkr.currency || 'USD'}` : '—'}
            </span>
          </div>
          <div style={rowStyle}>
            <span style={labStyle}>Verfügbar (Available Funds)</span>
            <span className="mono" data-testid="ibkr-account-free">
              {ibkr.available_funds != null ? `${Number(ibkr.available_funds).toFixed(2)} ${ibkr.currency || 'USD'}` : '—'}
            </span>
          </div>
          <div style={{ ...rowStyle, borderBottom: 'none' }}>
            <span style={labStyle}>Gateway</span>
            <span className="mono" style={{ fontSize: 11, opacity: 0.8 }}>
              {st ? (st.connected ? 'verbunden' : (st.gateway_url_set ? 'erreichbar geprüft…' : 'URL fehlt')) : 'prüfe…'}
            </span>
          </div>
          {(st?.error || ibkr.error) && (
            <div style={{ fontSize: 12, color: '#FFB74D', marginTop: 8 }} data-testid="ibkr-account-error">
              {String(st?.error || ibkr.error)}
            </div>
          )}
          {st?.hint && (
            <div style={{ fontSize: 11, opacity: 0.7, marginTop: 6 }}>{st.hint}</div>
          )}
          <div style={{ fontSize: 11, opacity: 0.55, marginTop: 12 }}>
            Kapital-Zuweisung (Guthaben aufteilen) gilt nur für Bitunix – dafür per
            Doppelklick zurück zur Bitunix-Ansicht wechseln und dann klicken.
          </div>
        </div>
      </div>
    </SafeOverlay>
  );
};

const BalanceWidget = () => {
  const [bal, setBal] = useState(null);
  // Statt eines gemeinsamen Modal-States pflegen wir den Scope-Lock explizit.
  // null = geschlossen, 'live' oder 'paper' = geöffnet mit fixem Scope.
  const [capitalScope, setCapitalScope] = useState(null);
  // IBKR-Konto-Fenster (Einfach-Klick, wenn das Badge auf IBKR steht)
  const [showIbkrInfo, setShowIbkrInfo] = useState(false);
  // Live-Badge-Quelle: Standard Bitunix, Doppelklick -> IBKR (und zurück).
  const [showIbkr, setShowIbkr] = useState(() => {
    try { return localStorage.getItem(LIVE_BADGE_KEY) === 'ibkr'; } catch (_) { return false; }
  });
  const toggleLiveSource = useCallback(() => {
    setShowIbkr((v) => {
      try { localStorage.setItem(LIVE_BADGE_KEY, v ? 'bitunix' : 'ibkr'); } catch (_) { /* ignore */ }
      return !v;
    });
  }, []);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/autotrade/balance`, { headers: authHeaders() });
      if (!r.ok) { setBal(null); return; }
      setBal(await r.json());
    } catch (_) { /* ignore */ }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 15000);
    return () => clearInterval(iv);
  }, [load]);

  const isLive = bal?.mode === 'live';
  // Hauptwidget öffnet immer mit dem aktuellen Modus als gesperrtem Scope.
  // Steht das Live-Badge auf IBKR, zeigt der Klick das IBKR-Konto statt der
  // (Bitunix-)Kapital-Zuweisung.
  const openMainCapital = useCallback(() => {
    if (isLive && showIbkr) { setShowIbkrInfo(true); return; }
    setCapitalScope(isLive ? 'live' : 'paper');
  }, [isLive, showIbkr]);
  const openLiveCapital = useCallback((e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    if (showIbkr) { setShowIbkrInfo(true); return; }
    setCapitalScope('live');
  }, [showIbkr]);
  // Live-Badge: Einfach-Klick = Kapital-Modal, Doppelklick = Bitunix <-> IBKR.
  const mainClicks = useClickOrDouble(openMainCapital, toggleLiveSource);
  const liveOverlayClicks = useClickOrDouble(openLiveCapital, toggleLiveSource);

  if (!bal) {
    // Skeleton während des initialen Loads: identisches Layout, damit der
    // Header nicht "nachspringt", sobald die Balance-Daten eintreffen.
    return (
      <div className="balance-widget-wrapper" data-testid="bitunix-balance-skeleton">
        <div className="balance-widget bw-skeleton" aria-busy="true">
          <div className="bw-mode live">
            <Wallet size={14} weight="fill" />
            LIVE
          </div>
          <div className="bw-stack">
            <span className="bw-usdt-label">USDT</span>
            <span className="bw-primary-value mono">—</span>
            <span className="bw-sub-line">
              <span className="bw-sub-label">frei</span>
              <span className="mono">—</span>
            </span>
          </div>
        </div>
        <div className="paper-overlay bw-skeleton" aria-busy="true">
          <div className="paper-overlay-mode">
            <Wallet size={12} weight="fill" />
            PAPER
          </div>
          <div className="overlay-stack">
            <span className="bw-usdt-label">PnL</span>
            <div className="paper-overlay-pnl">
              <span className="bw-primary-value bw-value-muted mono">—</span>
            </div>
            <span className="bw-sub-line">
              <span className="bw-sub-label">frei</span>
              <span className="mono">—</span>
            </span>
          </div>
        </div>
      </div>
    );
  }
  const pnl = bal.realized_pnl || 0;
  const pnlPos = pnl >= 0;

  // Paper overlay data
  const paperPnl = bal.paper_pnl ?? null;
  const paperPnlPos = (paperPnl || 0) >= 0;

  const liveAlloc = bal.allocation?.live;
  const paperAlloc = bal.allocation?.paper;
  const alloc = isLive ? liveAlloc : paperAlloc;

  const openPaperCapital = (e) => {
    e.stopPropagation();
    setCapitalScope('paper');
  };
  const liveTitle = showIbkr
    ? 'IBKR-Konto (Forex-Live) · Klick: IBKR-Konto anzeigen · Doppelklick: zurück zu Bitunix'
    : 'Bitunix-Konto · Doppelklick: IBKR-Kontostand · Klick: Live-Kapital anpassen';

  return (
    <div className="balance-widget-wrapper">
      {/* Live Badge - im Paper-Modus sichtbar (LINKS, Konvention: Live immer links). */}
      {!isLive && (
        <div className={`live-overlay bw-clickable${showIbkr ? ' bw-ibkr' : ''}`} data-testid="live-overlay"
          onClick={liveOverlayClicks.onClick}
          onDoubleClick={liveOverlayClicks.onDoubleClick}
          title={liveTitle}
          role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') openLiveCapital(e); }}>
          <div className="live-overlay-mode">
            <Wallet size={12} weight="fill" />
            LIVE
          </div>
          <div className="overlay-stack">
            <LiveBadgeStack bal={bal} showIbkr={showIbkr} testPrefix="live-overlay"
              freeValue={liveAlloc?.free != null && (
                <span className="bw-sub-line" data-testid="live-overlay-free">
                  <span className="bw-sub-label">frei</span>
                  <span className="mono">{Number(liveAlloc.free).toFixed(2)}</span>
                </span>
              )} />
          </div>
        </div>
      )}

      <div className={`balance-widget bw-clickable${isLive && showIbkr ? ' bw-ibkr' : ''}`} data-testid="bitunix-balance-widget"
        onClick={isLive ? mainClicks.onClick : openMainCapital}
        onDoubleClick={isLive ? mainClicks.onDoubleClick : undefined}
        title={isLive ? liveTitle : 'Paper-Kapital anpassen'}
        role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') openMainCapital(); }}>
        <div className={`bw-mode ${isLive ? 'live' : 'paper'}`} data-testid="bw-mode">
          <Wallet size={14} weight="fill" />
          {isLive ? 'LIVE' : 'PAPER'}
        </div>
        {isLive ? (
          (bal.bitunix_configured || showIbkr) ? (
            <LiveBadgeStack bal={bal} showIbkr={showIbkr} testPrefix="bw"
              freeValue={alloc?.free != null ? (
                <span className="bw-sub-line" data-testid="bw-alloc">
                  <span className="bw-sub-label">frei</span>
                  <span className="mono">{Number(alloc.free).toFixed(2)}</span>
                </span>
              ) : (
                <span className="bw-sub-line" data-testid="bw-free">
                  <span className="bw-sub-label">Kapital</span>
                  <span className="mono">{bal.available != null ? Number(bal.available).toFixed(2) : '—'}</span>
                </span>
              )} />
          ) : (
            <div className="bw-item bw-warn" data-testid="bw-unconfigured">Bitunix nicht konfiguriert</div>
          )
        ) : (
          <div className="bw-stack" data-testid="bw-paper-stack">
            <span className="bw-usdt-label">PnL</span>
            <span className={`bw-primary-value mono ${pnlPos ? 'pos' : 'neg'}`}>
              {pnlPos ? <TrendUp size={13} weight="bold" /> : <TrendDown size={13} weight="bold" />}
              {pnl.toFixed(2)}
            </span>
            {alloc?.free != null && (
              <span className="bw-sub-line" data-testid="bw-alloc">
                <span className="bw-sub-label">frei</span>
                <span className="mono">{Number(alloc.free).toFixed(2)}</span>
              </span>
            )}
          </div>
        )}
      </div>

      {/* Paper Badge - im Live-Modus sichtbar, klickbar für Paper-Kapital. */}
      {isLive && (
        <div className="paper-overlay bw-clickable" data-testid="paper-overlay"
          onClick={openPaperCapital}
          title={`Paper-Kapital anpassen · PnL nur aus derzeit aktiven Strategie×Asset-Kombinationen (${bal.paper_closed_trades ?? 0} geschlossene Trades)`}
          role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') openPaperCapital(e); }}>
          <div className="paper-overlay-mode">
            <Wallet size={12} weight="fill" />
            PAPER
          </div>
          <div className="overlay-stack">
            <span className="bw-usdt-label">PnL</span>
            <div className="paper-overlay-pnl">
              {paperPnl != null && paperPnl !== 0 ? (
                <span className={`bw-primary-value mono ${paperPnlPos ? 'pos' : 'neg'}`}>
                  {paperPnlPos ? <TrendUp size={13} weight="bold" /> : <TrendDown size={13} weight="bold" />}
                  {(paperPnl || 0).toFixed(2)}
                </span>
              ) : (
                <span className="bw-primary-value bw-value-muted mono">—</span>
              )}
            </div>
            {paperAlloc?.free != null && (
              <span className="bw-sub-line" data-testid="paper-overlay-free">
                <span className="bw-sub-label">frei</span>
                <span className="mono">{Number(paperAlloc.free).toFixed(2)}</span>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Live Badge wurde nach LINKS verschoben (siehe oben) – Konvention: Live links, Paper rechts. */}

      {capitalScope && (
        <CapitalModal
          lockedScope={capitalScope}
          onClose={() => setCapitalScope(null)}
          onSaved={load}
        />
      )}

      {showIbkrInfo && (
        <IBKRAccountModal bal={bal} onClose={() => setShowIbkrInfo(false)} />
      )}
    </div>
  );
};

const NotificationBell = () => {
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState('unread');
  const boxRef = React.useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const d = await fetch(`${API_URL}/api/notifications?filter=all&limit=100`).then(r => r.json());
        if (stop) return;
        // Keine Popups mehr: Meldungen sind ausschließlich über die Glocke einsehbar.
        setItems(d.notifications || []);
      } catch (_) { /* ignore */ }
    };
    load();
    const iv = setInterval(load, 60000);
    return () => { stop = true; clearInterval(iv); };
  }, []);

  const unread = items.filter(n => !n.read);
  const read = items.filter(n => n.read);
  const shown = view === 'unread' ? unread : read;

  const markRead = async () => {
    if (!unread.length) return;
    try {
      await fetch(`${API_URL}/api/notifications/read`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ ids: unread.map(n => n.id) }),
      });
    } catch (_) { /* ignore */ }
    const ts = new Date().toISOString();
    setItems(prev => prev.map(n => (n.read ? n : { ...n, read: true, read_at: ts })));
  };
  const tabStyle = (active) => ({
    background: active ? 'rgba(255,255,255,0.12)' : 'transparent',
    border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6,
    color: 'inherit', cursor: 'pointer', fontSize: 11, padding: '2px 8px',
  });
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }} ref={boxRef}>
      <button className="icon-btn" onClick={() => setOpen(v => !v)} data-testid="header-notifications-btn"
        title="Benachrichtigungen anzeigen"
        style={{ position: 'relative', color: unread.length ? '#FF3366' : undefined }}>
        <BellRinging size={18} weight={unread.length ? 'fill' : 'regular'} />
        {unread.length > 0 && (
          <span style={{ position: 'absolute', top: -4, right: -4, background: '#FF3366', color: '#fff', borderRadius: 8, fontSize: 10, padding: '0 4px' }}>{unread.length}</span>
        )}
      </button>
      {open && (
        <div className="notif-dropdown" data-testid="header-notifications-dropdown">
          <div className="notif-dd-head">
            <span style={{ display: 'inline-flex', gap: 6 }}>
              <button style={tabStyle(view === 'unread')} onClick={() => setView('unread')}
                data-testid="header-notifications-tab-unread">
                Ungelesen ({unread.length})
              </button>
              <button style={tabStyle(view === 'read')} onClick={() => setView('read')}
                data-testid="header-notifications-tab-read">
                Gelesen ({read.length})
              </button>
            </span>
            {view === 'unread' && unread.length > 0 && (
              <button className="notif-dd-read" onClick={markRead} data-testid="header-notifications-mark-read">
                Alle als gelesen
              </button>
            )}
          </div>
          <div className="notif-dd-list">
            {!shown.length && (
              <div className="notif-dd-item" data-testid="header-notifications-empty">
                <span>{view === 'unread' ? 'Keine ungelesenen Mitteilungen.' : 'Keine gelesenen Mitteilungen.'}</span>
              </div>
            )}
            {shown.map(n => (
              <div className="notif-dd-item" key={n.id}>
                <b>{n.title}</b>
                <span>{n.message}</span>
                {n.meta && (n.meta.role || n.meta.provider || n.meta.reason) && (
                  <span className="notif-dd-meta">
                    {n.meta.role ? <>Assistent: <b>{n.meta.role}</b></> : null}
                    {n.meta.provider ? <>{n.meta.role ? ' · ' : ''}Modell: {n.meta.provider}/{n.meta.model}</> : null}
                    {n.meta.reason ? <>{(n.meta.role || n.meta.provider) ? ' · ' : ''}Ursache: {n.meta.reason}</> : null}
                    {n.meta.fallback ? <> · Fallback: {n.meta.fallback}</> : null}
                    {n.meta.detail ? <> · {n.meta.detail}</> : null}
                  </span>
                )}
                <em>
                  {n.source ? `${n.source} · ` : ''}
                  {(n.created_at || n.ts)
                    ? new Date(n.created_at || n.ts).toLocaleString('de-DE', { timeZone: 'Europe/Berlin' })
                    : ''}
                </em>
              </div>
            ))}
          </div>
          {view === 'read' && (
            <div style={{ fontSize: 10, opacity: 0.6, padding: '4px 10px 8px' }}
              data-testid="header-notifications-retention-hint">
              Gelesene Mitteilungen werden nach 7 Tagen automatisch gelöscht.
            </div>
          )}
        </div>
      )}
    </span>
  );
};

const ToolsMenu = ({ onBacktestClick, onOptimizerClick, onRegimeLabClick, onSeriesClick }) => {
  const [open, setOpen] = useState(false);
  const ref = React.useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const items = [
    { label: 'Backtester', desc: 'Historische Daten, alle Timeframes',
      Icon: ClockCounterClockwise, onClick: onBacktestClick, testid: 'tools-menu-backtester' },
    { label: 'Strategie-Optimizer', desc: 'Parameter & Discovery',
      Icon: MagicWand, onClick: onOptimizerClick, testid: 'tools-menu-optimizer' },
    { label: 'Regime-Lab', desc: 'Marktphasen analysieren & prüfen',
      Icon: ChartScatter, onClick: onRegimeLabClick, testid: 'tools-menu-regime-lab' },
    { label: 'Nacht-Serie', desc: 'Backtests & Optimierungen als Warteschlange',
      Icon: MoonStars, onClick: onSeriesClick, testid: 'tools-menu-series' },
  ];

  return (
    <div className="tools-menu" ref={ref}>
      <button
        className={`btn ${open ? 'tools-menu-open' : ''}`}
        onClick={() => setOpen(v => !v)}
        title="Analyse-Tools: Backtester · Strategie-Optimizer · Regime-Lab"
        data-testid="tools-menu-button"
      >
        <Flask size={20} weight="bold" />
      </button>
      {open && (
        <div className="tools-menu-dropdown" data-testid="tools-menu-dropdown">
          {items.map(({ label, desc, Icon, onClick, testid }) => (
            <button
              key={testid}
              className="tools-menu-item"
              data-testid={testid}
              onClick={() => { setOpen(false); onClick && onClick(); }}
            >
              <Icon size={16} weight="bold" />
              <span className="tools-menu-text">
                <b>{label}</b>
                <small>{desc}</small>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

const Header = ({ sessionActive, onSettingsClick, currentSession, customSessions, activeStrategy, adminAuthed, onAdminClick, onCompareClick, onBacktestClick, onOptimizerClick, onRegimeLabClick, onLiquidityClick, onSeriesClick }) => {  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const formatTime = (date) => {
    return date.toLocaleTimeString('de-DE', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      timeZone: 'Europe/Berlin',
    });
  };

  const is24_7 = !customSessions || customSessions.length === 0;
  const enabledSessions = (customSessions || []).filter(s => s.enabled !== false);

  return (
    <header className="header" data-testid="main-header">
      <div className="header-left">
        <div className="header-brand">
          <ChartLineUp size={28} weight="bold" className="brand-icon" />
          <div className="header-brand-text">
            <h1 className="header-title">CRYPTO SCANNER</h1>
            {activeStrategy && (
              <div className="header-strategy" data-testid="active-strategy-display">
                🎯 {activeStrategy.name}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="header-right">
        <SafetyLight adminAuthed={adminAuthed} />
        <NotificationBell />
        {/* Uhrzeit + Session-Badge als eigener Block NEBEN den Labels (keine Überlagerung mehr) */}
        <div className="header-session" data-testid="header-session">
          <div className="session-status">
            <Clock size={18} weight="bold" />
            <span className="mono">{formatTime(currentTime)}</span>
            <span className={`badge ${sessionActive ? 'badge-active' : 'badge-inactive'}`} data-testid="session-status-badge">
              {sessionActive
                ? (currentSession ? `${currentSession.toUpperCase()} · ACTIVE` : 'TRADING ACTIVE')
                : 'OUTSIDE SESSIONS'}
            </span>
          </div>
          {!is24_7 && (
            <div className="session-times">
              {enabledSessions.length === 0 ? (
                <span className="text-muted">Keine aktiven Sessions</span>
              ) : (
                enabledSessions.map((s, i) => (
                  <span key={i} className="text-muted">
                    {i > 0 && <span style={{ margin: '0 4px' }}>|</span>}
                    {s.name}: {s.start}-{s.end}
                  </span>
                ))
              )}
            </div>
          )}
        </div>
        <BalanceWidget />
        <button className="btn" onClick={onCompareClick} title="Strategie-Vergleich" data-testid="compare-strategies-button">
          <Trophy size={20} weight="bold" />
        </button>
        <ToolsMenu
          onBacktestClick={onBacktestClick}
          onOptimizerClick={onOptimizerClick}
          onRegimeLabClick={onRegimeLabClick}
          onSeriesClick={onSeriesClick}
        />
        <button className="btn" onClick={onLiquidityClick} title="Liquidität (Liquidations-Heatmap & Liquidity Levels)" data-testid="liquidity-button">
          <Drop size={20} weight="bold" />
        </button>
        <button
          className={`btn btn-admin ${adminAuthed ? 'is-admin' : ''}`}
          onClick={onAdminClick}
          title={adminAuthed ? 'Admin abmelden' : 'Admin-Login'}
          aria-label={adminAuthed ? 'Admin abmelden' : 'Admin-Login'}
          data-testid="admin-lock-button"
        >
          {adminAuthed
            ? <LockOpen size={20} weight="bold" />
            : <Lock size={20} weight="bold" />}
        </button>
        <button className="btn" onClick={onSettingsClick} data-testid="settings-button">
          <Gear size={20} weight="bold" />
        </button>
      </div>
    </header>
  );
};

export default Header;
