import React, { useEffect, useState } from 'react';
import { useWorkerTarget } from '../lib/workerTarget';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Liste der verbundenen lokalen Worker (geteilt über alle Auswahlfelder)
function useOnlineWorkers() {
  const [workers, setWorkers] = useState([]);
  useEffect(() => {
    let alive = true;
    const load = () => fetch(`${API_URL}/api/localworker/status`).then(r => r.json())
      .then(d => { if (alive) setWorkers((d.workers || []).filter(w => w.online)); })
      .catch(() => {});
    load();
    const iv = setInterval(load, 10000);
    return () => { alive = false; clearInterval(iv); };
  }, []);
  return workers;
}

// Auswahl, welcher lokale Worker den Job rechnet – erscheint, sobald 2 Worker verbunden sind
export default function WorkerTargetSelect({ area, execution, testPrefix }) {
  const workers = useOnlineWorkers();
  const [target, setTarget] = useWorkerTarget(area);
  const tid = testPrefix || area;
  const targetOnline = !target || workers.some(w => w.worker_id === target);
  if (execution !== 'local' || (workers.length < 2 && targetOnline)) return null;
  return (
    <label className="opt-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}
      title="Mehrere lokale Worker verbunden (max. 2): wähle, welcher PC diesen Job rechnet – so laufen z.B. Endlos-Suche und Regime-Lab parallel auf zwei PCs"
      data-testid={`${tid}-worker-target`}>
      Worker
      <select value={target} onChange={e => setTarget(e.target.value)} data-testid={`${tid}-worker-target-select`}>
        <option value="">Automatisch (freier Worker)</option>
        {workers.map(w => (
          <option key={w.worker_id} value={w.worker_id}>
            {w.name || w.worker_id}{(w.running_jobs || []).length ? ` · ${(w.running_jobs || []).length} Job(s) aktiv` : ' · frei'}
          </option>
        ))}
        {!targetOnline && <option value={target}>{target} (offline)</option>}
      </select>
      {!targetOnline && <span className="opt-small" style={{ color: '#ffb35c' }} data-testid={`${tid}-worker-target-offline`}>gewählter Worker offline</span>}
    </label>
  );
}
