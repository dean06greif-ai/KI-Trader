import React, { useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const WORLD_LABELS = { live: 'LIVE', paper: 'PAPER', collect: 'SAMMEL' };

const fmtUsd = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Math.round(v * 100) / 100} $`);
const fmtR = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v}R`);

// Netto-Erfolgsbericht je Policy-Version (Audit 3.4): welcher Policy-Stand
// (Prompt/Lektionen/Playbook/Modell/Gate/Sizing) hat NETTO was verdient?
// Betriebskosten (Token) werden separat ausgewiesen, nie mit PnL verrechnet.
const PolicyReportCard = () => {
  const [days, setDays] = useState(90);
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
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
  const gridCols = '56px 46px 34px 34px 44px 1fr 1fr 1fr';

  const policyLabel = (r) => {
    if (!r.combined) return 'Alt-Trades (ohne Fingerprint)';
    const p = r.policy || {};
    return `${r.combined} · ${p.model || '?'}${p.gate_version ? ` · Gate v${p.gate_version}` : ''}`;
  };

  return (
    <div className="analytics-section" data-testid="policy-report-card">
      <div className="section-title" style={{ cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setOpen(o => !o)} data-testid="policy-report-toggle"
        title="Netto-Erfolg je Policy-Version: jede Änderung an Prompt, Lektionen, Playbook, Modell, ML-Gate oder Sizing erzeugt eine neue Version (Fingerprint). So ist beweisbar, WELCHER Stand Geld verdient hat. Klicken zum Auf-/Zuklappen.">
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 10, opacity: 0.8 }}>{open ? '▼' : '▶'}</span>
          POLICY-VERSIONEN (NETTO-ERFOLG)
        </span>
        <span style={{ fontSize: 10, opacity: 0.75, fontWeight: 400 }} data-testid="policy-report-headline">
          {open && data === null ? 'lädt…' : (data ? `${rows.length} Versionen` : 'Champion vs. Kandidat')}
        </span>
      </div>

      {open && (
        <div data-testid="policy-report-body">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0 8px' }}>
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
                  title={r.policy
                    ? `Prompt ${r.policy.prompt_hash} · Lektionen ${r.policy.lessons_hash} · Playbook ${r.policy.playbook_version} · Sizing ${r.policy.sizing_hash} · aktiv ${String(r.first_ts || '').slice(0, 10)} bis ${String(r.last_ts || '').slice(0, 10)}`
                    : 'Trades vor Einführung des Policy-Fingerprints'}
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                    gap: 8, padding: '5px 6px', fontSize: 11, cursor: 'pointer', userSelect: 'none',
                    background: 'rgba(255,255,255,0.03)', borderRadius: 6 }}>
                  <span style={{ ...cell, fontWeight: 600 }} className="mono">
                    <span style={{ fontSize: 9, opacity: 0.7, marginRight: 5 }}>{rowOpen ? '▼' : '▶'}</span>
                    {policyLabel(r)}
                  </span>
                  <b className="mono" style={{ whiteSpace: 'nowrap' }}>
                    {tot.trades || 0} Trades · {tot.wr != null ? `${tot.wr}%` : '—'} ·{' '}
                    <span style={{ color: (tot.pnl || 0) >= 0 ? 'var(--long, #0ecb81)' : 'var(--short, #f6465d)' }}>
                      {fmtUsd(tot.pnl)}
                    </span>
                    {' · '}Ø {fmtR(tot.avg_r)}
                  </b>
                </div>
                {rowOpen && (
                  <div style={{ padding: '2px 6px' }} data-testid={`policy-row-worlds-${idx}`}>
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
                      {r.decisions || 0} KI-Entscheidungen unter dieser Policy
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
