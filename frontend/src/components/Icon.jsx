import React from "react";

const P = {
  dashboard: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  chat: <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5z" />,
  plane: <path d="M3 13.5l7-2.2L6.5 4.5l1.8-.6 6.4 5.2 4.6-1.4c1.1-.3 2 .6 1.7 1.7-.2.7-.8 1.2-1.5 1.4l-4.6 1.4-1 7.5-1.7.5-1.8-6.3L3 16z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" /></>,
  cloud: <path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 10.5a3.75 3.75 0 0 1-.5 7.5z" />,
  rain: <><path d="M7 14a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 6.5 3.75 3.75 0 0 1 17.5 14z" /><path d="M8 17l-1 3M12 17l-1 3M16 17l-1 3" /></>,
  suitcase: <><rect x="3" y="7" width="18" height="13" rx="2.5" /><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M3 13h18" /></>,
  ticket: <path d="M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v3a2 2 0 0 0 0 2v3a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-3a2 2 0 0 0 0-2z" />,
  send: <path d="M4 12l16-8-6 16-3-6.5z" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  hotel: <><path d="M4 20V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14M2 20h20" /><path d="M9 9h2M13 9h2M9 13h2M13 13h2M10 20v-3h4v3" /></>,
  activity: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  user: <><circle cx="12" cy="8" r="3.5" /><path d="M5 20a7 7 0 0 1 14 0" /></>,
  trash: <path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13" />,
  pencil: <path d="M4 20h4L19 9l-4-4L4 16zM14 6l4 4" />,
  refresh: <path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" />,
};

export default function Icon({ name, size = 20, className = "", strokeWidth = 1.8 }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {P[name] || null}
    </svg>
  );
}
