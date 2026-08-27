import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Robot, PaperPlaneRight, CheckCircle, XCircle, Trash, CaretDown, CaretUp } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import './StrategyCopilot.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() });

const QUICK_PROMPTS = [
  { label: 'Einstellungen prüfen', text: 'Prüfe meine aktuellen Einstellungen. Sind sie sinnvoll? Was würdest du ändern?' },
  { label: 'Ergebnis bewerten', text: 'Bewerte das letzte Ergebnis. Ist alles korrekt berechnet? Ist es robust oder Overfitting?' },
  { label: 'Verbesserung vorschlagen', text: 'Schlage eine konkrete Verbesserung der aktuellen Strategie vor (als Vorschlag zum Übernehmen).' },
  { label: 'Welche Suche?', text: 'Welcher Such-Modus (Discovery, Deep-Test, Endlos-Suche, Parameter-Optimierung) ist für mein Ziel am besten?' },
];

const ProposalCard = ({ proposal, onApply, onDismiss, applied, applying }) => {
  const isDef = proposal.type === 'definition';
  return (
    <div className="cp-proposal" data-testid="copilot-proposal">
      <div className="cp-proposal-title">
        {isDef ? '📐 Strategie-Vorschlag' : '🎛 Parameter-Vorschlag'}
        {proposal.strategy_id ? ` · ${proposal.strategy_id}` : ' · neue Strategie'}
      </div>
      {proposal.summary && <div className="cp-proposal-summary">{proposal.summary}</div>}
      <pre className="cp-proposal-json">
        {JSON.stringify(isDef ? proposal.definition : {
          params: proposal.params, trade_params: proposal.trade_params, timeframe: proposal.timeframe,
        }, null, 1)}
      </pre>
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

const StrategyCopilot = ({ panel, getContext, onApplied }) => {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [appliedIds, setAppliedIds] = useState({});
  const [dismissedIds, setDismissedIds] = useState({});
  const [status, setStatus] = useState(null);
  const bodyRef = useRef(null);

  const loadHistory = useCallback(() => {
    fetch(`${API_URL}/api/copilot/history?limit=40`)
      .then(r => r.json()).then(d => setMessages(d.messages || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!open) return;
    loadHistory();
    fetch(`${API_URL}/api/copilot/status`).then(r => r.json()).then(setStatus).catch(() => {});
  }, [open, loadHistory]);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [messages, busy]);

  const send = async (text) => {
    const msg = (text || input).trim();
    if (!msg || busy) return;
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setInput('');
    setBusy(true);
    setMessages(m => [...m, { id: `local_${Date.now()}`, role: 'user', content: msg }]);
    try {
      const context = { panel, ...(getContext ? getContext() : {}) };
      const res = await fetch(`${API_URL}/api/copilot/chat`, {
        method: 'POST', headers: jsonHeaders(),
        body: JSON.stringify({ message: msg, context }),
      });
      const raw = await res.text();
      let d = null;
      try { d = JSON.parse(raw); } catch { /* 502/HTML vom Ingress */ }
      if (!res.ok || !d) {
        const det = d && typeof d.detail === 'string' ? d.detail
          : `Copilot-Antwort dauert zu lange (${res.status}) – bitte erneut senden`;
        toast.error(det, { duration: 8000 });
        return;
      }
      setMessages(m => [...m, { id: d.id, role: 'assistant', content: d.reply, proposal: d.proposal, checks: d.checks, model: d.model }]);
    } catch { toast.error('Verbindungsfehler zum Copilot'); }
    finally { setBusy(false); }
  };

  const apply = async (msg) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
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

  return (
    <div className="cp-wrap" data-testid="strategy-copilot">
      <button className="cp-toggle" onClick={() => setOpen(o => !o)} data-testid="copilot-toggle">
        <Robot size={16} weight="bold" />
        <span>STRATEGIE-COPILOT</span>
        <span className="cp-toggle-hint">eigene KI · berät, prüft & ändert nur nach Bestätigung</span>
        {open ? <CaretUp size={14} /> : <CaretDown size={14} />}
      </button>
      {open && (
        <div className="cp-panel">
          <div className="cp-topbar">
            <span className="cp-model">{status ? `OpenRouter · ${status.model || 'Auto (mit Fallback)'}` : '…'}</span>
            <button className="cp-clear" onClick={clearChat} title="Verlauf löschen" data-testid="copilot-clear-btn">
              <Trash size={13} /> Verlauf
            </button>
          </div>
          <div className="cp-quick">
            {QUICK_PROMPTS.map(q => (
              <button key={q.label} onClick={() => send(q.text)} disabled={busy}
                data-testid={`copilot-quick-${q.label.replace(/[^a-z]/gi, '-').toLowerCase()}`}>{q.label}</button>
            ))}
          </div>
          <div className="cp-body" ref={bodyRef} data-testid="copilot-messages">
            {messages.length === 0 && !busy && (
              <div className="cp-empty">
                Ich bin dein Strategie-Copilot – unabhängig vom KI-Trader.
                Ich sehe deine aktuellen Einstellungen, bewerte Ergebnisse und
                schlage Änderungen vor, die du erst bestätigen musst.
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
              placeholder="Frage zum Strategie-Bau, Einstellungen oder Ergebnis…"
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
