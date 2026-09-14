import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Robot, PaperPlaneRight, CheckCircle, XCircle, Trash, CaretDown, CaretUp, Gear } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import './StrategyCopilot.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() });

const QUICK_PROMPTS = [
  { label: 'Einstellungen prüfen', text: 'Prüfe meine aktuellen Einstellungen. Sind sie sinnvoll? Was würdest du ändern?' },
  { label: 'Für mich einstellen', text: 'Stelle die Suche sinnvoll für mich ein und schicke die Änderungen als Einstellungs-Vorschlag zum Übernehmen. Erkläre kurz warum.' },
  { label: 'Ergebnis bewerten', text: 'Bewerte das letzte Ergebnis. Ist alles korrekt berechnet? Ist es robust oder Overfitting?' },
  { label: 'Strategien-Check', text: 'Schau dir meine vorhandenen Strategien mit ihren Indikatoren und echten Ergebnissen an: Welche sollte ich optimieren, warum – und fehlt ein sinnvolles Setup?' },
  { label: 'Welche Suche?', text: 'Welcher Such-Modus (Discovery, Deep-Test, Endlos-Suche, Parameter-Optimierung, Dynamisch) ist für mein Ziel am besten?' },
];

const PROPOSAL_META = {
  definition: '📐 Strategie-Vorschlag',
  params: '🎛 Parameter-Vorschlag',
  settings: '⚙️ Einstellungs-Vorschlag',
};

const ProposalCard = ({ proposal, onApply, onDismiss, applied, applying }) => {
  const isDef = proposal.type === 'definition';
  const isSettings = proposal.type === 'settings';
  const payload = isDef ? proposal.definition
    : isSettings ? proposal.settings
      : { params: proposal.params, trade_params: proposal.trade_params, timeframe: proposal.timeframe };
  return (
    <div className="cp-proposal" data-testid="copilot-proposal">
      <div className="cp-proposal-title">
        {PROPOSAL_META[proposal.type] || 'Vorschlag'}
        {isSettings ? '' : (proposal.strategy_id ? ` · ${proposal.strategy_id}` : ' · neue Strategie')}
      </div>
      {proposal.summary && <div className="cp-proposal-summary">{proposal.summary}</div>}
      <pre className="cp-proposal-json">{JSON.stringify(payload, null, 1)}</pre>
      {applied ? (
        <div className="cp-proposal-applied"><CheckCircle size={14} weight="bold" /> Übernommen</div>
      ) : (
        <div className="cp-proposal-actions">
          <button className="cp-apply" onClick={onApply} disabled={applying} data-testid="copilot-apply-btn">
            <CheckCircle size={14} weight="bold" /> {applying ? 'Übernehme…' : 'Übernehmen'}
          </button>
          <button className="cp-dismiss" onClick={onDismiss} data-testid="copilot-dismiss-btn">
            <XCircle size={14} weight="bold" /> Ablehnen
          </button>
        </div>
      )}
    </div>
  );
};

const StrategyCopilot = ({ panel, getContext, onApplied, onApplySettings }) => {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [appliedIds, setAppliedIds] = useState({});
  const [dismissedIds, setDismissedIds] = useState({});
  const [status, setStatus] = useState(null);
  const [showCfg, setShowCfg] = useState(false);
  const [savingModel, setSavingModel] = useState(false);
  const [weekly, setWeekly] = useState(null);
  const [sendingWeekly, setSendingWeekly] = useState(false);
  const bodyRef = useRef(null);

  const loadHistory = useCallback(() => {
    fetch(`${API_URL}/api/copilot/history?limit=40`)
      .then(r => r.json()).then(d => setMessages(d.messages || [])).catch(() => {});
  }, []);

  const loadStatus = useCallback(() => {
    fetch(`${API_URL}/api/copilot/status`).then(r => r.json()).then(setStatus).catch(() => {});
  }, []);

  useEffect(() => {
    if (!open) return;
    loadHistory();
    loadStatus();
  }, [open, loadHistory, loadStatus]);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [messages, busy]);

  const changeModel = async (model) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setSavingModel(true);
    try {
      const res = await fetch(`${API_URL}/api/copilot/model`, {
        method: 'POST', headers: jsonHeaders(),
        body: JSON.stringify({ model: model || null }),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(typeof d.detail === 'string' ? d.detail : 'Modell-Wechsel fehlgeschlagen'); return; }
      setStatus(s => ({ ...(s || {}), model: d.model }));
      toast.success(d.model ? `Copilot-Modell: ${d.model}` : 'Copilot-Modell: Auto (mit Fallback)');
    } catch { toast.error('Verbindungsfehler'); }
    finally { setSavingModel(false); }
  };

  useEffect(() => {
    if (!showCfg) return;
    fetch(`${API_URL}/api/copilot/weekly`).then(r => r.json()).then(setWeekly).catch(() => {});
  }, [showCfg]);

  const saveWeekly = async (updates) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try {
      const res = await fetch(`${API_URL}/api/copilot/weekly`, {
        method: 'POST', headers: jsonHeaders(), body: JSON.stringify(updates),
      });
      const d = await res.json();
      if (res.ok) setWeekly(d);
    } catch { toast.error('Verbindungsfehler'); }
  };

  const sendWeeklyNow = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setSendingWeekly(true);
    try {
      const res = await fetch(`${API_URL}/api/copilot/weekly/send`, {
        method: 'POST', headers: jsonHeaders(),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(typeof d.detail === 'string' ? d.detail : 'Report fehlgeschlagen', { duration: 8000 }); return; }
      toast.success(d.sent ? 'Wochen-Report per Telegram gesendet' : 'Report erstellt – Telegram-Versand nicht möglich (Token/Chat prüfen)', { duration: 8000 });
      setWeekly(w => ({ ...(w || {}), last_sent: d.sent_at }));
    } catch { toast.error('Verbindungsfehler'); }
    finally { setSendingWeekly(false); }
  };

  const send = async (text) => {
    const msg = (text || input).trim();
    if (!msg || busy) return;
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setInput('');
    setBusy(true);
    setMessages(m => [...m, { id: `local_${Date.now()}`, role: 'user', content: msg }]);
    try {
      // Hintergrund-Job statt direktem Request: Render/Ingress kappt HTTP nach
      // ~60s – langsame Modelle (z.B. DeepSeek Flash) brachen dadurch ab.
      // Jetzt: Job starten und Ergebnis pollen (bis ~150s).
      const context = { panel, ...(getContext ? getContext() : {}) };
      const res = await fetch(`${API_URL}/api/copilot/chat/start`, {
        method: 'POST', headers: jsonHeaders(),
        body: JSON.stringify({ message: msg, context }),
      });
      const start = await res.json().catch(() => null);
      if (!res.ok || !start?.job_id) {
        toast.error(start?.detail || 'Copilot-Start fehlgeschlagen – bitte erneut senden', { duration: 8000 });
        return;
      }
      let d = null;
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 2500));
        let j = null;
        try {
          const pr = await fetch(`${API_URL}/api/copilot/chat/job/${start.job_id}`);
          j = await pr.json().catch(() => null);
          if (!pr.ok) { toast.error(j?.detail || 'Copilot-Job verloren – bitte erneut senden'); return; }
        } catch { continue; /* kurzer Netzwerk-Schluckauf -> weiter pollen */ }
        if (j?.status === 'done') { d = j.result; break; }
        if (j?.status === 'error') {
          toast.error(j.error || 'Copilot-Antwort fehlgeschlagen – bitte erneut senden', { duration: 8000 });
          return;
        }
      }
      if (!d) { toast.error('Copilot-Antwort dauert zu lange – bitte erneut senden', { duration: 8000 }); return; }
      setMessages(m => [...m, { id: d.id, role: 'assistant', content: d.reply, proposal: d.proposal, checks: d.checks, model: d.model }]);
    } catch { toast.error('Verbindungsfehler zum Copilot'); }
    finally { setBusy(false); }
  };

  const apply = async (msg) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    // Einstellungs-Vorschläge werden direkt ins Formular des Panels übernommen
    if (msg.proposal?.type === 'settings') {
      if (!onApplySettings) {
        toast.info('Einstellungs-Vorschläge können in diesem Panel nicht übernommen werden');
        return;
      }
      const n = onApplySettings(msg.proposal.settings || {});
      setAppliedIds(a => ({ ...a, [msg.id]: true }));
      toast.success(n ? `Einstellungen übernommen (${n} Felder)` : 'Einstellungen übernommen');
      return;
    }
    setApplying(true);
    try {
      const res = await fetch(`${API_URL}/api/copilot/apply`, {
        method: 'POST', headers: jsonHeaders(),
        body: JSON.stringify({ proposal: msg.proposal }),
      });
      const d = await res.json();
      if (!res.ok) {
        const det = d.detail;
        const t = det && det.problems ? `Abgewiesen: ${det.problems.slice(0, 2).join(' · ')}`
          : (typeof det === 'string' ? det : 'Übernahme fehlgeschlagen');
        toast.error(t, { duration: 8000 });
        return;
      }
      setAppliedIds(a => ({ ...a, [msg.id]: true }));
      toast.success(d.type === 'definition' ? `Strategie "${d.definition?.name}" gespeichert` : 'Parameter übernommen');
      onApplied && onApplied(d);
    } catch { toast.error('Verbindungsfehler'); }
    finally { setApplying(false); }
  };

  const clearChat = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    await fetch(`${API_URL}/api/copilot/history`, { method: 'DELETE', headers: authHeaders() }).catch(() => {});
    setMessages([]);
  };

  const keyLabel = status?.key_source === 'copilot' ? 'eigene Copilot-Keys'
    : status?.key_source === 'shared' ? 'OpenRouter-Keys (geteilt)' : 'keine Keys';

  return (
    <div className="cp-wrap" data-testid="strategy-copilot">
      <button className="cp-toggle" onClick={() => setOpen(o => !o)} data-testid="copilot-toggle">
        <Robot size={16} weight="bold" />
        <span>STRATEGIE-COPILOT</span>
        <span className="cp-toggle-hint">berät, prüft & ändert Einstellungen nur nach Bestätigung – entwickelt nie selbst</span>
        {open ? <CaretUp size={14} /> : <CaretDown size={14} />}
      </button>
      {open && (
        <div className="cp-panel">
          <div className="cp-topbar">
            <span className="cp-model" data-testid="copilot-model-label">
              {status ? `${status.model || 'Auto (mit Fallback)'} · ${keyLabel}` : '…'}
            </span>
            <button className="cp-clear" onClick={() => setShowCfg(c => !c)}
              title="Copilot-Einstellungen (Modell wählen)" data-testid="copilot-settings-btn">
              <Gear size={13} /> Modell
            </button>
            <button className="cp-clear" onClick={clearChat} title="Verlauf löschen" data-testid="copilot-clear-btn">
              <Trash size={13} /> Verlauf
            </button>
          </div>
          {showCfg && (
            <div className="cp-settings" data-testid="copilot-settings">
              <label>Copilot-Modell (OpenRouter)</label>
              <select value={status?.model || ''} disabled={savingModel}
                onChange={e => changeModel(e.target.value)} data-testid="copilot-model-select">
                <option value="">Auto – schnellstes freies Modell (mit Fallback)</option>
                {(status?.models || []).map(m => (
                  <option key={m} value={m}>
                    {m}{(status?.paid_models || []).includes(m) ? ' · bezahlt (OpenRouter-Guthaben)' : ' · free'}
                  </option>
                ))}
              </select>
              <div className="cp-settings-hint">
                Bezahlte Modelle (z.B. DeepSeek, GPT/GLM/Grok) laufen über dein OpenRouter-Guthaben
                und werden nie automatisch als Fallback genutzt – nur wenn du sie hier fest auswählst.
              </div>
              <div className="cp-weekly" data-testid="copilot-weekly">
                <label style={{ marginTop: 10 }}>Wöchentlicher Setup-Report (Telegram)</label>
                <div className="cp-weekly-row">
                  <label className="cp-weekly-check">
                    <input type="checkbox" checked={!!weekly?.enabled}
                      onChange={e => saveWeekly({ enabled: e.target.checked })}
                      data-testid="copilot-weekly-toggle" />
                    <span>aktiv</span>
                  </label>
                  <select value={weekly?.weekday ?? 0} onChange={e => saveWeekly({ weekday: Number(e.target.value) })}
                    data-testid="copilot-weekly-weekday">
                    {['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
                      .map((d, i) => <option key={d} value={i}>{d}</option>)}
                  </select>
                  <select value={weekly?.hour ?? 9} onChange={e => saveWeekly({ hour: Number(e.target.value) })}
                    data-testid="copilot-weekly-hour">
                    {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                  </select>
                  <button className="cp-weekly-send" onClick={sendWeeklyNow} disabled={sendingWeekly}
                    data-testid="copilot-weekly-send">
                    {sendingWeekly ? 'Erstelle…' : 'Jetzt senden'}
                  </button>
                </div>
                <div className="cp-settings-hint">
                  Der Copilot analysiert wöchentlich alle Strategien + den KI-Setup-Katalog
                  (was optimieren, welche Setups fehlen) und schickt das Ergebnis per Telegram.
                  {weekly?.last_sent ? ` Zuletzt gesendet: ${new Date(weekly.last_sent).toLocaleString('de-DE')}.` : ''}
                </div>
              </div>
            </div>
          )}
          {status && !status.ready && (
            <div className="cp-nokey" data-testid="copilot-nokey-hint">
              Kein OpenRouter-Key gefunden: Bitte <code>{status.key_env || 'COPILOT_OPENROUTER_API_KEY'}</code> (eigene
              Copilot-Keys) oder <code>OPENROUTER_API_KEY</code> in der Backend-.env setzen – der Copilot nutzt
              automatisch die KI-Trader-Keys als Fallback, wenn keine eigenen gesetzt sind.
            </div>
          )}
          <div className="cp-quick">
            {QUICK_PROMPTS.map(q => (
              <button key={q.label} onClick={() => send(q.text)} disabled={busy}
                data-testid={`copilot-quick-${q.label.replace(/[^a-z]/gi, '-').toLowerCase()}`}>{q.label}</button>
            ))}
          </div>
          <div className="cp-body" ref={bodyRef} data-testid="copilot-messages">
            {messages.length === 0 && !busy && (
              <div className="cp-empty">
                Ich bin dein Strategie-Copilot: Ich sehe deine aktuellen Einstellungen und alle
                vorhandenen Strategien (Indikatoren + echte Ergebnisse), erkläre jede Funktion,
                bewerte Ergebnisse und ändere Einstellungen auf Wunsch – immer erst nach deiner
                Bestätigung. Ich entwickle keine Strategien auf eigene Faust.
              </div>
            )}
            {messages.map((m, i) => (
              <div key={m.id || i} className={`cp-msg ${m.role}`}>
                <div className="cp-bubble">
                  {m.content}
                  {(m.checks || []).length > 0 && (
                    <ul className="cp-checks">{m.checks.map((c, j) => <li key={j}>⚠ {c}</li>)}</ul>
                  )}
                </div>
                {m.proposal && !dismissedIds[m.id] && (
                  <ProposalCard proposal={m.proposal} applying={applying}
                    applied={!!appliedIds[m.id]}
                    onApply={() => apply(m)}
                    onDismiss={() => setDismissedIds(x => ({ ...x, [m.id]: true }))} />
                )}
              </div>
            ))}
            {busy && <div className="cp-msg assistant"><div className="cp-bubble cp-thinking">Copilot denkt nach…</div></div>}
          </div>
          <div className="cp-inputrow">
            <input value={input} onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && send()}
              placeholder="Frage zu Einstellungen, Strategien oder Ergebnissen…"
              disabled={busy} data-testid="copilot-input" />
            <button className="cp-send" onClick={() => send()} disabled={busy || !input.trim()} data-testid="copilot-send-btn">
              <PaperPlaneRight size={16} weight="bold" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default StrategyCopilot;
