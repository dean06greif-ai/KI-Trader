import React from 'react';
import { Sparkle, X, ArrowRight } from '@phosphor-icons/react';
import { modelPost, slugId } from '../lib/aiModelApi';

const price = (m) => (m.paid
  ? (typeof m.price_out === 'number' ? `bezahlt · ${m.price_out.toLocaleString('de-DE')} $/1M Output` : 'bezahlt')
  : 'kostenlos');

function NewsModel({ m, onApplyRole }) {
  const id = slugId(m.provider, m.model);
  return (
    <div className="ai-news-model" data-testid={`ai-news-model-${id}`}>
      <div className="ai-news-model-head">
        <b>{m.provider} · {m.model}</b>
        <span className={`ai-news-price ${m.paid ? 'paid' : 'free'}`}>{price(m)}</span>
      </div>
      <div className="ai-news-why">{m.why}</div>
      {m.better_than && <div className="ai-news-better">besser als: <b>{m.better_than}</b></div>}
      {(m.roles || []).length > 0 && (
        <div className="ai-news-roles">
          {m.roles.map(r => (
            <button key={r.role} className="ai-action-btn ai-news-role-btn" title={r.why}
              onClick={() => onApplyRole(r.role, m.provider, m.model)}
              data-testid={`ai-news-apply-${r.role}-${id}`}>
              <ArrowRight size={11} /> {String(r.label || r.role).split(' – ')[0]}
              {r.replaces ? <span className="ai-news-replaces"> statt {r.replaces}</span> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Mitteilung im KI-Team: automatisch aufgenommene Modelle – bleibt bis weggeklickt. */
export default function AIModelNewsBanner({ announcements, onChanged, onApplyRole }) {
  if (!announcements?.length) return null;
  const dismiss = async (id) => {
    if (await modelPost(`/api/ai/models/announcements/${id}/dismiss`)) onChanged?.();
  };
  return announcements.map(a => (
    <div className="ai-news-banner" key={a.id} data-testid={`ai-news-banner-${a.id}`}>
      <div className="ai-news-head">
        <Sparkle size={15} weight="fill" />
        <span className="ai-news-title">Neue KI-Modelle automatisch in den Katalog aufgenommen</span>
        <span className="ai-news-date">{new Date(a.created_at).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
          {a.source === 'llm' && a.model_used ? ` · Einschätzung: ${a.model_used}` : ' · Regel-Einschätzung'}</span>
        <button className="ai-news-close" onClick={() => dismiss(a.id)} title="Mitteilung ausblenden"
          data-testid={`ai-news-dismiss-${a.id}`}><X size={14} weight="bold" /></button>
      </div>
      <div className="ai-news-summary" data-testid={`ai-news-summary-${a.id}`}>{a.summary}</div>
      <div className="ai-news-models">
        {(a.models || []).map(m => <NewsModel key={`${m.provider}/${m.model}`} m={m} onApplyRole={onApplyRole} />)}
      </div>
      <div className="ai-news-foot">Klick auf eine Rolle = Modell dort als Haupt-Modell setzen. Bezahlte Modelle werden nie automatisch als Fallback genutzt.</div>
    </div>
  ));
}
