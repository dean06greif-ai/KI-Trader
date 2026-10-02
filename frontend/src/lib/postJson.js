import { authHeaders } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** POST mit Admin-Header; wirft Error(detail) bei HTTP-Fehler. */
export async function postJson(path, body) {
  const r = await fetch(`${API_URL}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body || {}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || 'Fehler');
  return d;
}
