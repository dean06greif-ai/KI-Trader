import { toast } from './toast';
import { authHeaders } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Admin-POST für Modell-Katalog-Aktionen; true bei Erfolg, Fehler als Toast. */
export async function modelPost(path, body, okMsg) {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast.error(res.status === 401 ? 'Admin-Login erforderlich' : (data.detail || 'Aktion fehlgeschlagen'));
      return false;
    }
    if (okMsg) toast.success(okMsg);
    return data || true;
  } catch (e) {
    toast.error('Verbindungsfehler');
    return false;
  }
}

export const slugId = (p, m) => `${p}-${String(m).replace(/[^a-z0-9]/gi, '-')}`;
