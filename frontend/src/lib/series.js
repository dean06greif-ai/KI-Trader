// Nacht-Serie / Job-Warteschlange: Einträge aus Backtester, Optimizer und
// Regime-Lab einreihen (gleicher Request-Body wie beim direkten Start).
import { toast } from './toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

export const SERIES_KIND_LABEL = {
  backtest: 'Backtest',
  optimizer: 'Optimizer',
  regime_analysis: 'Regime-Analyse',
};

export async function addToSeries(kind, body, label) {
  if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return null; }
  try {
    const res = await fetch(`${API_URL}/api/series/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ kind, body, label: label || undefined }),
    });
    const d = await res.json();
    if (!res.ok) { toast.error(d.detail || 'Einreihen fehlgeschlagen'); return null; }
    toast.success(`Zur Nacht-Serie hinzugefügt: ${d.item.label}`);
    return d.item;
  } catch {
    toast.error('Verbindungsfehler');
    return null;
  }
}
