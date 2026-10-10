import React, { useState } from 'react';
import { ArrowsClockwise, Trash, ArrowCounterClockwise } from '@phosphor-icons/react';
import { modelPost, slugId } from '../lib/aiModelApi';

const TIER = { top: ['sehr gut', 'vgood'], good: ['gut', 'good'], low: ['schwach', 'bad'] };
const SOURCE = { builtin: 'fest', auto: 'automatisch', approved: 'bestätigt' };

function TierBadge({ tier }) {
  const [label, cls] = TIER[tier] || ['unbewertet', 'mid'];
  return <span className={`rl-grade-badge ${cls}`}>{label.toUpperCase()}</span>;
}

function PendingRow({ pm, onChanged }) {
  const id = slugId(pm.provider, pm.model);
  const act = async (path, msg) => { if (await modelPost(path, { provider: pm.provider, model: pm.model }, msg)) onChanged(); };
  return (
    <div className="ai-pending-row" data-testid={`ai-pending-${id}`}>
      <span className="ai-pending-name" title={(pm.reasons || []).join(' · ')}>
        <TierBadge tier={pm.tier} /> {pm.provider} · {pm.model}{pm.paid ? ' · bezahlt' : ''}
      </span>
      <div className="ai-pending-actions">
        <button className="ai-action-btn ai-pending-approve" data-testid={`ai-approve-${id}`}
          onClick={() => act('/api/ai/models/approve', `${pm.provider} · ${pm.model} freigeschaltet – jetzt auswählbar`)}>Bestätigen</button>
        <button className="ai-action-btn ai-pending-dismiss" data-testid={`ai-dismiss-${id}`}
          onClick={() => act('/api/ai/models/dismiss')}>Verwerfen</button>
      </div>
    </div>
  );
}

function ActiveRow({ m, onChanged }) {
  const id = slugId(m.provider, m.model);
  const used = m.in_use || [];
  const remove = async () => {
    if (!window.confirm(`${m.provider} · ${m.model} aus dem Katalog entfernen? (unten unter „Ausgeblendet“ wiederherstellbar)`)) return;
    if (await modelPost('/api/ai/models/remove', { provider: m.provider, model: m.model }, 'Modell aus dem Katalog entfernt')) onChanged();
  };
  return (
    <div className="ai-pending-row" data-testid={`ai-catalog-${id}`}>
      <span className="ai-pending-name">
        {m.provider} · {m.model}
        <span className="ai-catalog-tags"> · Gewicht {m.weight} · {SOURCE[m.source] || m.source}{m.paid ? ' · bezahlt' : ''}
          {used.length ? ` · in Nutzung: ${used.join(', ')}` : ''}</span>
      </span>
      <button className="ai-action-btn ai-pending-dismiss" onClick={remove} disabled={used.length > 0}
        title={used.length ? 'Wird noch verwendet – erst in der Rolle ein anderes Modell wählen' : 'Veraltetes Modell aus der Website entfernen'}
        data-testid={`ai-catalog-remove-${id}`}><Trash size={12} /> Entfernen</button>
    </div>
  );
}

/** KI-Modell-Katalog (KI-Team, ganz unten): neu entdeckt · aktiv · ausgeblendet. */
export default function AIModelCatalog({ catalog, onChanged }) {
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [showWeak, setShowWeak] = useState(false);
  const pending = catalog?.pending || [];
  const active = catalog?.active || [];
  const hidden = catalog?.hidden || [];
  const runCheck = async () => {
    setRunning(true);
    const d = await modelPost('/api/ai/models/watch/run', {}, null);
    setRunning(false);
    if (d) onChanged();
  };
  const restore = async (h) => {
    if (await modelPost('/api/ai/models/restore', { provider: h.provider, model: h.model }, 'Modell wiederhergestellt')) onChanged();
  };
  return (
    <div className="ai-pending-models" data-testid="ai-model-catalog">
      <div className="ai-pending-title ai-catalog-head">
        <span>KI-Modell-Katalog · {active.length} aktiv{pending.length ? ` · ${pending.length} neu entdeckt` : ''}</span>
        <span className="ai-catalog-checked">{catalog?.checked_at ? `zuletzt geprüft ${new Date(catalog.checked_at).toLocaleString('de-DE')}` : ''}</span>
        <button className="ai-action-btn" onClick={runCheck} disabled={running} data-testid="ai-catalog-check-now">
          <ArrowsClockwise size={12} className={running ? 'spin' : ''} /> {running ? 'Prüft …' : 'Jetzt nach neuen Modellen suchen'}
        </button>
      </div>
      <div className="ai-catalog-hint">„Sehr gut“ bewertete neue Modelle (bekannte starke Familie, Nachfolger, kostenlos/günstig, Kurztest bestanden) kommen automatisch in den Katalog – alle anderen warten hier auf Bestätigung.</div>
      {pending.filter(pm => showWeak || pm.tier !== 'low').map(pm => <PendingRow key={`${pm.provider}/${pm.model}`} pm={pm} onChanged={onChanged} />)}
      {pending.some(pm => pm.tier === 'low') && (
        <button className="opt-chip ai-catalog-toggle" onClick={() => setShowWeak(v => !v)} data-testid="ai-catalog-weak-toggle">
          {showWeak ? '▾ schwach bewertete ausblenden' : `▸ ${pending.filter(pm => pm.tier === 'low').length} schwach bewertete anzeigen (Spezial-, Klein- oder unbekannte Modelle)`}
        </button>
      )}
      <button className="opt-chip ai-catalog-toggle" onClick={() => setOpen(v => !v)} data-testid="ai-catalog-toggle">
        {open ? '▾' : '▸'} Aktive Modelle verwalten ({active.length}){hidden.length ? ` · ${hidden.length} ausgeblendet` : ''}
      </button>
      {open && active.map(m => <ActiveRow key={`${m.provider}/${m.model}`} m={m} onChanged={onChanged} />)}
      {open && hidden.length > 0 && (
        <>
          <div className="ai-pending-title">Ausgeblendet (entfernt oder verworfen)</div>
          {hidden.map(h => (
            <div className="ai-pending-row" key={`${h.kind}/${h.provider}/${h.model}`} data-testid={`ai-hidden-${slugId(h.provider, h.model)}`}>
              <span className="ai-pending-name">{h.provider} · {h.model} <span className="ai-catalog-tags">· {h.kind === 'removed' ? 'entfernt' : 'verworfen'}</span></span>
              <button className="ai-action-btn ai-pending-approve" onClick={() => restore(h)}
                data-testid={`ai-restore-${slugId(h.provider, h.model)}`}><ArrowCounterClockwise size={12} /> Wiederherstellen</button>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
