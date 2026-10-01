// Inline line icons (24x24 grid, stroked with currentColor) that replace the v4 emoji glyphs (copied from tiny-the-reachy, plus the ones NEON needs). Decorative by default:
// aria-hidden, the owning button carries the aria-label. Pass a `label` to render a labelled, standalone icon.
import type { SVGProps } from 'react'

type P = SVGProps<SVGSVGElement> & { label?: string }
const base = (p: P) => {
  const { label, ...rest } = p
  return { xmlns: 'http://www.w3.org/2000/svg', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
    ...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true }), ...rest }
}

export const Ico = {
  /** motors / control mode */
  motor: (p: P = {}) => <svg {...base(p)}><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1" /></svg>,
  wifi: (p: P = {}) => <svg {...base(p)}><path d="M2 8.5a15 15 0 0 1 20 0M5.5 12a10 10 0 0 1 13 0M9 15.5a5 5 0 0 1 6 0" /><circle cx="12" cy="19" r="1" fill="currentColor" /></svg>,
  temp: (p: P = {}) => <svg {...base(p)}><path d="M10 4a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0Z" /><path d="M12 10v6" /></svg>,
  brain: (p: P = {}) => <svg {...base(p)}><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0V7a3 3 0 0 0-3-3Z" /><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 0V7a3 3 0 0 1 3-3Z" /></svg>,
  film: (p: P = {}) => <svg {...base(p)}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 5v14M17 5v14M3 10h4M3 14h4M17 10h4M17 14h4" /></svg>,
  eye: (p: P = {}) => <svg {...base(p)}><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>,
  sound: (p: P = {}) => <svg {...base(p)}><path d="M4 10v4h3l4 3V7L7 10H4Z" /><path d="M15 9a4 4 0 0 1 0 6M17.5 6.5a8 8 0 0 1 0 11" /></svg>,
  lock: (p: P = {}) => <svg {...base(p)}><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>,
  alert: (p: P = {}) => <svg {...base(p)}><path d="M12 3 2 21h20L12 3Z" /><path d="M12 10v5M12 18v.5" /></svg>,
  /** head and antennas: a crosshair */
  look: (p: P = {}) => <svg {...base(p)}><circle cx="12" cy="12" r="7" /><path d="M12 2v4M12 18v4M2 12h4M18 12h4" /></svg>,
  /** emotions: a smile */
  smile: (p: P = {}) => <svg {...base(p)}><circle cx="12" cy="12" r="9" /><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0" /><path d="M9 9.5v.5M15 9.5v.5" /></svg>,
  /** say: a speech bubble */
  say: (p: P = {}) => <svg {...base(p)}><path d="M4 5h16v10H9l-5 4V5Z" /></svg>,
  settings: (p: P = {}) => <svg {...base(p)}><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></svg>,
  close: (p: P = {}) => <svg {...base(p)}><path d="M6 6l12 12M18 6 6 18" /></svg>,
  send: (p: P = {}) => <svg {...base(p)}><path d="M12 19V5M6 11l6-6 6 6" /></svg>,
  camera: (p: P = {}) => <svg {...base(p)}><path d="M4 8h3l2-3h6l2 3h3v11H4V8Z" /><circle cx="12" cy="13" r="3.2" /></svg>,
  cube: (p: P = {}) => <svg {...base(p)}><path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z" /><path d="M4 7.5 12 12l8-4.5M12 12v9" /></svg>,
  swap: (p: P = {}) => <svg {...base(p)}><path d="M4 8h13l-3-3M20 16H7l3 3" /></svg>,
  home: (p: P = {}) => <svg {...base(p)}><path d="M3 11 12 4l9 7" /><path d="M6 10v10h12V10" /></svg>,
  sun: (p: P = {}) => <svg {...base(p)}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>,
  moon: (p: P = {}) => <svg {...base(p)}><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" /></svg>,
  tool: (p: P = {}) => <svg {...base(p)}><path d="M14.5 5.5a4 4 0 0 0 4 4l-9 9a2.1 2.1 0 0 1-3-3l9-9Z" /><path d="M15 6l3 3" /></svg>,
  key: (p: P = {}) => <svg {...base(p)}><circle cx="8" cy="14" r="4" /><path d="M11 11l9-9M16 6l3 3M14 8l2 2" /></svg>,
  mic: (p: P = {}) => <svg {...base(p)}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>,
  chat: (p: P = {}) => <svg {...base(p)}><path d="M4 5h16v10H9l-5 4V5Z" /><path d="M8 9h8M8 12h5" /></svg>,
  monitor: (p: P = {}) => <svg {...base(p)}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></svg>,
  terminal: (p: P = {}) => <svg {...base(p)}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9l3 3-3 3M12 15h5" /></svg>,
  dot: (p: P = {}) => <svg {...base(p)}><circle cx="12" cy="12" r="4" fill="currentColor" stroke="none" /></svg>,
  chevronDown: (p: P = {}) => <svg {...base(p)}><path d="M6 10l6 6 6-6" /></svg>,
  /** neon additions */
  headset: (p: P = {}) => <svg {...base(p)}><path d="M3 13a9 9 0 0 1 18 0" /><path d="M3 13v5a2 2 0 0 0 2 2h2v-7H3ZM21 13v5a2 2 0 0 1-2 2h-2v-7h4Z" /></svg>,
  activity: (p: P = {}) => <svg {...base(p)}><path d="M3 12h4l3-8 4 16 3-8h4" /></svg>,
  battery: (p: P = {}) => <svg {...base(p)}><rect x="2" y="7" width="18" height="10" rx="2" /><path d="M22 10v4M6 11v2M10 11v2" /></svg>,
  radar: (p: P = {}) => <svg {...base(p)}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><path d="M12 12 18 6M12 12v.5" /></svg>,
  layers: (p: P = {}) => <svg {...base(p)}><path d="M12 3 3 8l9 5 9-5-9-5Z" /><path d="M3 12l9 5 9-5M3 16l9 5 9-5" /></svg>,
  sliders: (p: P = {}) => <svg {...base(p)}><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></svg>,
  robot: (p: P = {}) => <svg {...base(p)}><rect x="5" y="8" width="14" height="11" rx="2" /><path d="M12 4v4M9 13h.5M14.5 13h.5M9 16h6" /></svg>,
  cpu: (p: P = {}) => <svg {...base(p)}><rect x="6" y="6" width="12" height="12" rx="2" /><rect x="10" y="10" width="4" height="4" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></svg>,
  refresh: (p: P = {}) => <svg {...base(p)}><path d="M20 12a8 8 0 1 1-2.3-5.7" /><path d="M20 4v5h-5" /></svg>,
  check: (p: P = {}) => <svg {...base(p)}><path d="M5 12l5 5L20 7" /></svg>,
  plus: (p: P = {}) => <svg {...base(p)}><path d="M12 5v14M5 12h14" /></svg>,
  copy: (p: P = {}) => <svg {...base(p)}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a1 1 0 0 1 1-1h10" /></svg>,
  external: (p: P = {}) => <svg {...base(p)}><path d="M14 4h6v6M20 4l-9 9" /><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" /></svg>,
  globe: (p: P = {}) => <svg {...base(p)}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></svg>,
}
export type IconName = keyof typeof Ico
