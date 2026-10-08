
import { useState } from 'react';

// Draggable dividers: the chart column's share of the workspace (%), and the sidebar width (px).
export const SPLIT = { key: 'sec-intel-chart-split', initial: 57.4, min: 25, max: 75, step: 2, bigStep: 10 }; // 57.4% = old 1.35fr/1fr

export const SIDEBAR = { key: 'sec-intel-sidebar-width', initial: 248, min: 180, max: 420, step: 16, bigStep: 64 };

// One draggable divider: pointer drag, arrow keys (Shift for bigger steps), Home/End,
// double-click to reset, value remembered in localStorage. `toValue` turns a pointer event
// into the new value.
export function useDivider(config, toValue) {
  const { key, initial, min, max, step, bigStep } = config;
  const [value, setValue] = useState(() => {
    try {
      const stored = parseFloat(localStorage.getItem(key));
      if (stored >= min && stored <= max) return stored;
    } catch {
      // localStorage unavailable — use the default.
    }
    return initial;
  });
  const [dragging, setDragging] = useState(false);
  const clamp = (v) => Math.min(max, Math.max(min, v));
  const save = (v) => {
    try {
      localStorage.setItem(key, String(v));
    } catch {
      // Storage unavailable — the size just won't be remembered.
    }
  };
  const commit = (v) => {
    const next = clamp(v);
    setValue(next);
    save(next);
  };
  const endDrag = () => {
    if (!dragging) return;
    setDragging(false);
    save(value);
  };
  const handlers = {
    role: 'separator',
    'aria-orientation': 'vertical',
    'aria-valuemin': min,
    'aria-valuemax': max,
    'aria-valuenow': Math.round(value),
    tabIndex: 0,
    title: 'Drag to resize · double-click to reset',
    onPointerDown: (e) => {
      e.currentTarget.setPointerCapture(e.pointerId);
      setDragging(true);
    },
    onPointerMove: (e) => {
      if (dragging) setValue(clamp(toValue(e)));
    },
    onPointerUp: endDrag,
    onPointerCancel: endDrag,
    onKeyDown: (e) => {
      const moves = { ArrowLeft: -1, ArrowRight: 1 };
      if (e.key in moves) commit(value + moves[e.key] * (e.shiftKey ? bigStep : step));
      else if (e.key === 'Home') commit(min);
      else if (e.key === 'End') commit(max);
      else return;
      e.preventDefault();
    },
    onDoubleClick: () => commit(initial),
  };
  return { value, dragging, handlers };
}
