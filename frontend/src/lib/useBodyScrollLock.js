import { useEffect } from 'react';

// Sperrt das Scrollen der Seite im Hintergrund, solange ein Overlay/Panel
// offen ist (Bugfix: Mausrad über dem Panel scrollte "durch" aufs Dashboard).
// Ref-Zähler: auch bei verschachtelten Overlays wird erst beim Schließen des
// letzten Overlays wieder freigegeben.
let lockCount = 0;
let prevOverflow = '';

export default function useBodyScrollLock(active = true) {
  useEffect(() => {
    if (!active) return undefined;
    if (lockCount === 0) {
      prevOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    }
    lockCount += 1;
    return () => {
      lockCount -= 1;
      if (lockCount === 0) document.body.style.overflow = prevOverflow;
    };
  }, [active]);
}
