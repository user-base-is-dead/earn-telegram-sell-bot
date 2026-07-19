// Shared motion tokens so every animated component in the panel moves with the
// same rhythm. See docs/superpowers/plans/2026-07-16-admin-panel.md for the
// research this is based on.
export const DURATION = {
  micro: 0.2,   // button/toggle/hover — 150-300ms band
  panel: 0.3,   // card/sheet/modal enter
  page: 0.28,   // page-level transitions — snappier admin feel
} as const;

export const EXIT_RATIO = 0.65; // exits run at ~65% of their enter duration

export const EASE_OUT_QUART: [number, number, number, number] = [0.25, 1, 0.5, 1];

export const SPRING_INTERACTIVE = {
  type: "spring" as const,
  stiffness: 420,
  damping: 32,
};

export const STAGGER_CHILD_DELAY = 0.03; // 30ms — lower bound of the 30-50ms band

export const GLOW_DELAY = 0.08; // glow layer fades in ~80ms after its parent arrives

export const PRESS_SCALE = 0.96;   // within 0.94-0.98
export const HOVER_SCALE = 1.03;   // within 1.02-1.06

// Framer/Motion variants for a staggered list container + item, reused by any
// list/grid that reveals on mount (dashboard KPI row, order queue, product grid).
export const listContainer = {
  hidden: {},
  show: {
    transition: { staggerChildren: STAGGER_CHILD_DELAY },
  },
};

export const listItem = {
  hidden: { opacity: 0, y: 8 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.panel, ease: EASE_OUT_QUART },
  },
};
