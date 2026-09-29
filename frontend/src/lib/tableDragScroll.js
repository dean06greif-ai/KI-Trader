/**
 * Seitliches Wisch-Scrollen für breite Tabellen (".bt-table-wrap") – auch am
 * Desktop wie auf dem Handy: Tabelle greifen und ziehen. Touch scrollt nativ.
 *
 * Delegierter Listener auf document-Ebene: gilt automatisch für ALLE
 * jetzigen und künftigen Tabellen mit dieser Klasse (Backtester, Seeding,
 * Edge-Register, Portfolio-Backtest …) ohne Änderungen an den Komponenten.
 * Ein Klick wird nur unterdrückt, wenn wirklich gezogen wurde
 * (gleiche Logik wie hooks/useDragScroll.js).
 */
let installed = false;
let moved = false;

export function initTableDragScroll(selector = '.bt-table-wrap') {
  if (installed || typeof document === 'undefined') return;
  installed = true;

  document.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'touch') return; // Touch scrollt nativ
    if (!e.target.closest) return;
    if (e.target.closest('input, select, textarea, button, a')) return;
    const el = e.target.closest(selector);
    if (!el || el.scrollWidth <= el.clientWidth) return;
    const startX = e.clientX;
    const startScroll = el.scrollLeft;
    moved = false;
    const move = (ev) => {
      const dx = ev.clientX - startX;
      if (Math.abs(dx) > 4) {
        moved = true;
        el.classList.add('dragging');
      }
      el.scrollLeft = startScroll - dx;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      el.classList.remove('dragging');
      // erst nach dem Click-Event zurücksetzen, damit der Klick unterdrückt wird
      setTimeout(() => { moved = false; }, 0);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  });

  document.addEventListener('click', (e) => {
    if (moved && e.target.closest && e.target.closest(selector)) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);
}
