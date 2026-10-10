import { wrapFetch, isStartRequest } from '../lib/startFeedback';

const mkUi = () => {
  const calls = [];
  const rec = (kind) => (msg, opts) => calls.push({ kind, msg, id: opts?.id ?? msg });
  return { calls, loading: rec('loading'), message: rec('message'), dismiss: (id) => calls.push({ kind: 'dismiss', id }) };
};
const okRes = (body = { job_id: 'j1' }) => ({ ok: true, status: 200, clone() { return okRes(body); }, json: async () => body });
const errRes = (detail) => ({ ok: false, status: 409, clone() { return errRes(detail); }, json: async () => ({ detail }) });
const defer = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

describe('startFeedback', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  test('erkennt nur POST auf Start-Endpunkte', () => {
    expect(isStartRequest('https://x/api/optimizer/run', { method: 'POST' })).toBe(true);
    expect(isStartRequest('https://x/api/regime-lab/analyze', { method: 'POST' })).toBe(true);
    expect(isStartRequest('https://x/api/regime-lab/abc123/walkforward', { method: 'POST' })).toBe(true);
    expect(isStartRequest('https://x/api/regime-lab/autopilot', { method: 'POST' })).toBe(true);
    expect(isStartRequest('https://x/api/regime-lab/autopilot/stop/1', { method: 'POST' })).toBe(false);
    expect(isStartRequest('https://x/api/optimizer/run', {})).toBe(false);
    expect(isStartRequest('https://x/api/autotrade/trades?limit=200', { method: 'POST' })).toBe(false);
  });

  test('andere Anfragen laufen unverändert durch', async () => {
    const ui = mkUi();
    const orig = jest.fn(async () => okRes());
    const f = wrapFetch(orig, ui);
    await f('https://x/api/health');
    expect(orig).toHaveBeenCalledTimes(1);
    expect(ui.calls).toEqual([]);
  });

  test('langsamer Start zeigt sofort „Wird gestartet“ und räumt danach auf', async () => {
    const ui = mkUi();
    const d = defer();
    const f = wrapFetch(() => d.promise, ui);
    const p = f('https://x/api/optimizer/run', { method: 'POST', body: '{}' });
    jest.advanceTimersByTime(200);
    expect(ui.calls[0].kind).toBe('loading');
    jest.advanceTimersByTime(3000);
    expect(ui.calls[1].msg).toMatch(/ausgelastet/);
    d.resolve(okRes());
    const res = await p;
    expect(res.ok).toBe(true);
    expect(ui.calls[ui.calls.length - 1].kind).toBe('dismiss');
  });

  test('abgelehnter Start nennt den Grund sichtbar', async () => {
    const ui = mkUi();
    const f = wrapFetch(async () => errRes('Es läuft bereits ein Regime-Lab-Job (analysis)'), ui);
    const res = await f('https://x/api/regime-lab/analyze', { method: 'POST', body: '{}' });
    expect(res.ok).toBe(false);
    expect(ui.calls.pop().msg).toBe('Nicht gestartet: Es läuft bereits ein Regime-Lab-Job (analysis)');
    expect(await res.json()).toEqual({ detail: 'Es läuft bereits ein Regime-Lab-Job (analysis)' });
  });

  test('Doppelklick startet nicht zweimal', async () => {
    const ui = mkUi();
    const d = defer();
    const orig = jest.fn(() => d.promise);
    const f = wrapFetch(orig, ui);
    const a = f('https://x/api/optimizer/run', { method: 'POST', body: '{"a":1}' });
    const b = f('https://x/api/optimizer/run', { method: 'POST', body: '{"a":1}' });
    d.resolve(okRes({ job_id: 'same' }));
    const [ra, rb] = await Promise.all([a, b]);
    expect(orig).toHaveBeenCalledTimes(1);
    expect((await ra.json()).job_id).toBe('same');
    expect((await rb.json()).job_id).toBe('same');
  });
});
