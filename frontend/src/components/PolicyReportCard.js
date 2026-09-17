import React, { useEffect, useState } from 'react';
import { CaretRight, Info } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const WORLD_LABELS = { live: 'LIVE', paper: 'PAPER', collect: 'SAMMEL' };

const fmtUsd = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Math.round(v * 100) / 100} $`);
const fmtR = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v}R`);
const fmtDay = (ts) => {
  const s = String(ts || '').slice(0, 10);
  return s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.` : '—';
};

// Sprechender Name einer Policy-Version (UI-Wunsch 09/2026): Nummer + Zeitraum
// + Modell + was sich gegenüber der Vorversion geändert hat – statt Hash.
export const policyTitle = (r) => {
  if (!r.combined) return 'Alt-Trades (vor Einführung der Versionierung)';
  return `Version ${r.version_no ?? '?'}${r.is_current ? ' · aktuell' : ''}`;
};
export const policySubtitle = (r) => {
  if (!r.combined) return 'ohne Fingerprint – nicht zuordenbar';
  const p = r.policy || {};
  const parts = [`${fmtDay(r.first_ts)}–${fmtDay(r.last_ts)}`, p.model || 'Modell ?'];
  if (p.gate_version) parts.push(`ML-Gate v${p.gate_version}`);
  if ((r.changed || []).length) parts.push(`geändert: ${r.changed.join(', ')}`);
  else if (r.version_no === 1) parts.push('erste Version');
  return parts.join(' · ');
};

const HELP_TEXT = (
  <>
    <b>Was ist eine Policy-Version?</b> Der KI-Trader entscheidet nach einem „Regelwerk“ aus
    Prompt, Lektionen, Playbook, Modell, ML-Gate, Sizing und Einstellungen. Sobald sich eines
    davon ändert, entsteht automatisch eine neue Version (Fingerprint). Jeder Trade merkt sich,
    unter welcher Version er entstand.
    <br /><br />
    <b>Was zeigt die Tabelle?</b> Je Version den <b>Netto-Erfolg</b> (Fees/Funding bereits
    abgezogen) getrennt nach LIVE, PAPER und SAMMEL-Trades. So siehst du, welcher Stand
    wirklich Geld verdient hat – und ob eine Änderung (z.&nbsp;B. neue Lektionen) besser oder
    schlechter war als die Version davor. Betriebskosten (Tokens) werden separat gezeigt und
    nie mit dem PnL verrechnet.
  </>
);

const PolicyReportCard = () => {
  const [days, setDays] = useState(90);
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const [openRows, setOpenRows] = useState({});

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setData(null);
    fetch(`${API_URL}/api/analytics/policy-report?days=${days}`)
      .then(r => r.json())
      .then(d => { if (alive) setData(d); })
      .catch(() => { if (alive) setData({ rows: [], ops: {}, days }); });
    return () => { alive = false; };
  }, [days, open]);

  const rows = data?.rows || [];
  const cell = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
  const gridCols = '48px 40px 26px 26px 40px 62px 56px 56px';

  return (
    <div className="analytics-section policy-card" data-testid="policy-report-card">
      <div className="section-title policy-card-head">
        <button type="button" className={`policy-card-toggle ${open ? 'open' : ''}`}
          onClick={() => setOpen(o => !o)} data-testid="policy-report-toggle"
          aria-expanded={open}
          title={open ? 'Zuklappen' : 'Aufklappen: Netto-Erfolg je Version anzeigen'}>
          <CaretRight size={11} weight="bold" className="policy-card-caret" />
          <span className="policy-card-title">Policy-Versionen</span>
          <span className="policy-card-sub" data-testid="policy-report-subtitle">
            Welcher Stand des KI-Traders hat netto Geld verdient?
          </span>
        </button>
        <span className="policy-card-right">
          <span data-testid="policy-report-headline">
            {open && data === null ? 'lädt…' : (data ? `${rows.length} Versionen` : '')}
          </span>
          <button type="button" className={`policy-info-btn ${help ? 'on' : ''}`}
            onClick={(e) => { e.stopPropagation(); setHelp(h => !h); }}
            aria-label="Erklärung zu Policy-Versionen" title="Was bedeutet das?"
            data-testid="policy-report-info-btn">
            <Info size={14} weight={help ? 'fill' : 'bold'} />
          </button>
        </span>
      </div>

      {help && (
        <div className="policy-help" data-testid="policy-report-help">{HELP_TEXT}</div>
      )}

      {open && (
        <div data-testid="policy-report-body">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0 8px', flexWrap: 'wrap' }}>
            <select className="trade-filter-strat" value={days}
              onChange={e => setDays(Number(e.target.value))}
              data-testid="policy-report-days-select">
              <option value={30}>30 Tage</option>
              <option value={90}>90 Tage</option>
              <option value={180}>180 Tage</option>
            </select>
            {data?.ops && (
              <span style={{ fontSize: 10, opacity: 0.7 }} data-testid="policy-report-ops"
                title="Betriebskosten separat: geschätzte LLM-Tokens und Analyse-Zyklen im Zeitraum (nicht mit dem Trade-PnL verrechnet)">
                Betriebskosten: ~{(data.ops.token_estimate_total || 0).toLocaleString('de-DE')} Tokens ·{' '}
                {data.ops.analysis_cycles || 0} Analyse-Zyklen
              </span>
            )}
          </div>

          {data === null && <div className="no-data">Lädt…</div>}
          {data !== null && rows.length === 0 && (
            <div className="no-data" data-testid="policy-report-empty">
              Noch keine geschlossenen KI-Trades im Zeitraum – der Bericht füllt sich,
              sobald Trades mit Policy-Fingerprint schließen.
            </div>
          )}

          {rows.map((r, idx) => {
            const key = r.combined || 'legacy';
            const rowOpen = !!openRows[key];
            const tot = r.total || {};
            return (
              <div key={key} style={{ marginBottom: 6 }} data-testid={`policy-row-${idx}`}>
                <div onClick={() => setOpenRows(o => ({ ...o, [key]: !o[key] }))}
                  data-testid={`policy-row-toggle-${idx}`}
                  className={`policy-row ${r.is_current ? 'current' : ''}`}
                  title={r.policy
                    ? `Fingerprint ${r.combined} · Prompt ${r.policy.prompt_hash} · Lektionen ${r.policy.lessons_hash} · Playbook ${r.policy.playbook_version} · Sizing ${r.policy.sizing_hash}`
                    : 'Trades vor Einführung des Policy-Fingerprints'}>
                  <span style={{ ...cell, minWidth: 0 }}>
                    <span style={{ fontSize: 9, opacity: 0.7, marginRight: 5 }}>{rowOpen ? '▼' : '▶'}</span>
                    <b data-testid={`policy-row-title-${idx}`}>{policyTitle(r)}</b>
                    <span className="policy-row-sub" data-testid={`policy-row-sub-${idx}`}>{policySubtitle(r)}</span>
                  </span>
                  <b className="mono policy-row-stats" style={{ whiteSpace: 'nowrap' }}>
                    {tot.trades || 0} Trades · {tot.wr != null ? `${tot.wr}%` : '—'} ·{' '}
                    <span style={{ color: (tot.pnl || 0) >= 0 ? 'var(--long, #0ecb81)' : 'var(--short, #f6465d)' }}>
                      {fmtUsd(tot.pnl)}
                    </span>
                    {' · '}Ø {fmtR(tot.avg_r)}
                  </b>
                </div>
                {rowOpen && (
                  <div className="policy-worlds" style={{ padding: '2px 6px' }} data-testid={`policy-row-worlds-${idx}`}>
                    <div style={{ display: 'grid', gridTemplateColumns: gridCols, gap: 6,
                      fontSize: 9, opacity: 0.6, padding: '4px 0 2px', textTransform: 'uppercase' }}>
                      <span>Welt</span><span>Trades</span><span>W</span><span>L</span><span>WR</span>
                      <span title="Netto-PnL (Fees/Funding bereits abgezogen, Slippage im Fill)">PnL netto</span>
                      <span title="gezahlte Gebühren (informativ, bereits im PnL)">Σ Fees</span>
                      <span title="Ø R-Multiple in Geld (PnL / riskiertes Kapital)">Ø R</span>
                    </div>
                    {['live', 'paper', 'collect'].map(w => {
                      const b = (r.worlds || {})[w] || {};
                      if (!b.trades) return null;
                      return (
                        <div key={w} className="mono" data-testid={`policy-world-${idx}-${w}`}
                          style={{ display: 'grid', gridTemplateColumns: gridCols, gap: 6,
                            fontSize: 11, padding: '3px 0',
                            borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                          <span style={{ ...cell, fontWeight: w === 'live' ? 700 : 400 }}>{WORLD_LABELS[w]}</span>
                          <span style={cell}>{b.trades}</span>
                          <span style={cell} className="text-long">{b.wins}</span>
                          <span style={cell} className="text-short">{b.losses}</span>
                          <span style={cell}>{b.wr != null ? `${b.wr}%` : '—'}</span>
                          <span style={{ ...cell, color: (b.pnl || 0) >= 0 ? 'var(--long, #0ecb81)' : 'var(--short, #f6465d)' }}>
                            {fmtUsd(b.pnl)}
                          </span>
                          <span style={{ ...cell, opacity: 0.8 }}>{fmtUsd(-(b.fees || 0))}</span>
                          <span style={cell}>{fmtR(b.avg_r)}</span>
                        </div>
                      );
                    })}
                    <div style={{ fontSize: 10, opacity: 0.65, padding: '4px 0' }}>
                      {r.decisions || 0} KI-Entscheidungen unter dieser Version
                      {r.policy && (
                        <span className="mono" style={{ marginLeft: 6, opacity: 0.7 }}>· Fingerprint {r.combined}</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default PolicyReportCard;
