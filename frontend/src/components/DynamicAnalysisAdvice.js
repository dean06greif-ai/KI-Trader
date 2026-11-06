import React, { useEffect, useState } from 'react';
import { Compass } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

export const VERDICT_STYLE = {
  empfohlen: { cls: 'ok', icon: '★', text: 'empfohlen' },
  geeignet: { cls: 'ok', icon: '✓', text: 'geeignet' },
  bedingt: { cls: 'warn', icon: '!', text: 'nur bedingt geeignet' },
  ungeeignet: { cls: 'warn', icon: '✗', text: 'nicht geeignet' },
};

/** Eignung aller Regime-Erkennungen für dynamische Strategien (Backend: services/dynamic_advisor). */
export function useAnalysisAdvice(refreshKey) {
  const [advice, setAdvice] = useState({});
  useEffect(() => {
    let alive = true;
    fetch(`${API_URL}/api/dynamic-workbench/analysis-advice`).then(r => r.json())
      .then(d => { if (alive && d.analyses) setAdvice(Object.fromEntries(d.analyses.map(a => [a.id, a]))); })
      .catch(() => {});
    return () => { alive = false; };
  }, [refreshKey]);
  return advice;
}

export const adviceTag = (a) => (a ? ` · ${VERDICT_STYLE[a.verdict]?.icon || ''} ${VERDICT_STYLE[a.verdict]?.text || a.verdict}` : '');

const LEVEL_ICON = { ok: '✓', warn: '⚠', bad: '✗' };

function Reasons({ reasons }) {
  return (
    <ul data-testid="dwb-advice-reasons">
      {(reasons || []).map((r, i) => (
        <li key={i} className={r.level === 'ok' ? '' : 'neg'}>{LEVEL_ICON[r.level] || '·'} {r.text}</li>
      ))}
    </ul>
  );
}

function TimeframeHint({ tf, current, onApply, disabled }) {
  if (!tf?.recommended) return null;
  const same = (current || tf.analysis_tf) === tf.recommended;
  return (
    <div className="opt-small" style={{ marginTop: 6 }} data-testid="dwb-advice-tf">
      <b>Timeframe:</b> Erkennung bleibt auf {tf.analysis_tf} · Strategie-Empfehlung <b>{tf.recommended}</b>
      {tf.filter_tfs?.length > 0 && <> · Regel-Filter auf {tf.filter_tfs.join(' / ')} werden in der Suche mitgetestet</>}
      {!same && <button className="bt-exec-btn" style={{ marginLeft: 8 }} disabled={disabled}
        onClick={() => onApply(tf.recommended)} data-testid="dwb-advice-tf-apply">{tf.recommended} übernehmen</button>}
      <div style={{ opacity: 0.7 }}>{tf.why}</div>
    </div>
  );
}

/** Empfehlung zur gewählten Regime-Erkennung + Alternativen. */
export default function DynamicAnalysisAdvice({ advice, selectedId, onSelect, strategyTf, onApplyTf, disabled }) {
  const [open, setOpen] = useState(false);
  const list = Object.values(advice || {}).sort((a, b) => b.score - a.score);
  const cur = advice?.[selectedId];
  const recs = list.filter(a => a.recommended && a.id !== selectedId).slice(0, 3);
  if (!list.length) return null;
  const st = cur ? VERDICT_STYLE[cur.verdict] || VERDICT_STYLE.bedingt : null;
  return (
    <div style={{ margin: '6px 0 10px' }} data-testid="dwb-analysis-advice">
      {cur && (
        <div className={`dyn-verdict ${st.cls}`} data-testid="dwb-advice-verdict">
          <b>{st.icon} Diese Regime-Erkennung ist für dynamische Strategien {st.text}</b>
          <span className="opt-small"> · Eignung {cur.score}/100{cur.grade ? ` · Note ${cur.grade}` : ''}</span>
          <button className="bt-exec-btn" style={{ marginLeft: 8 }} onClick={() => setOpen(o => !o)} data-testid="dwb-advice-toggle">
            {open ? 'Gründe ausblenden' : 'Gründe ansehen'}
          </button>
          {(open || cur.verdict === 'ungeeignet') && <Reasons reasons={cur.reasons} />}
          <TimeframeHint tf={cur.timeframe_advice} current={strategyTf} onApply={onApplyTf} disabled={disabled} />
        </div>
      )}
      {!cur && recs.length === 0 && (
        <div className="dyn-verdict warn opt-small" style={{ marginTop: 6 }} data-testid="dwb-advice-none">
          Keine Regime-Erkennung ist derzeit klar für dynamische Strategien empfohlen (Note, Holdout, Phasendauer) –
          nach der Auswahl siehst du die Gründe. Tipp: im Regime-Lab eine Analyse mit Holdout und guter Note erstellen.
        </div>
      )}
      {recs.length > 0 && (
        <div className="opt-small" style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }} data-testid="dwb-advice-recs">
          <Compass size={14} weight="bold" style={{ color: '#7CFFB2' }} />
          {cur ? 'Besser geeignet:' : 'Empfohlene Regime-Erkennungen:'}
          {recs.map(a => (
            <button key={a.id} className="bt-exec-btn" disabled={disabled} onClick={() => onSelect(a.id)}
              title={(a.reasons || []).map(r => r.text).join('\n')} data-testid={`dwb-advice-rec-${a.id}`}>
              ★ {a.name} ({a.score})
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
