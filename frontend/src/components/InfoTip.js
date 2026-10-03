import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Info } from '@phosphor-icons/react';
import './InfoTip.css';

// Kleines Info-Symbol mit Erklär-Popover (Hover am Desktop, Klick/Tap pinnt).
// Portal + fixed-Position: wird von scrollenden Modals nicht abgeschnitten.
export default function InfoTip({ children, testId, width = 360 }) {
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState(null);
  const btnRef = useRef(null);
  const popRef = useRef(null);
  const open = hover || pinned;

  useLayoutEffect(() => {
    if (!open || !btnRef.current) return;
    const r = btnRef.current.getBoundingClientRect();
    const w = Math.min(width, window.innerWidth - 16);
    const left = Math.min(Math.max(r.left - 8, 8), window.innerWidth - w - 8);
    const below = window.innerHeight - r.bottom > 300 || r.top < 300;
    setPos(below ? { left, top: r.bottom + 6, width: w } : { left, bottom: window.innerHeight - r.top + 6, width: w });
  }, [open, width]);

  useEffect(() => {
    if (!pinned) return undefined;
    const close = (e) => {
      if (!btnRef.current?.contains(e.target) && !popRef.current?.contains(e.target)) setPinned(false);
    };
    const hide = () => setPinned(false);
    document.addEventListener('mousedown', close);
    window.addEventListener('scroll', hide, true);
    return () => { document.removeEventListener('mousedown', close); window.removeEventListener('scroll', hide, true); };
  }, [pinned]);

  return (
    <span className="info-tip" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button type="button" ref={btnRef} className="info-tip-btn" aria-label="Erklärung" data-testid={testId}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setPinned(p => !p); }}>
        <Info size={13} weight="bold" />
      </button>
      {open && pos && createPortal(
        <span className="info-tip-pop" ref={popRef} style={pos} role="tooltip" data-testid={testId && `${testId}-pop`}>{children}</span>,
        document.body)}
    </span>
  );
}
