import React from "react";
import Icon from "./Icon.jsx";
import { useApp } from "../AppState.jsx";
import { STATUS_LABEL } from "../lib/format.js";

export function Status({ value }) {
  const tone = { SIMULADA: "sol", PENDIENTE: "sea", CONFIRMADA: "ok", PLANEANDO: "sea", RESERVADO: "sol", COMPLETADO: "ok", CANCELADO: "bad" }[value] || "sea";
  return <span className={`pill pill-${tone}`}>{STATUS_LABEL[value] || (value ? value.charAt(0) + value.slice(1).toLowerCase() : "-")}</span>;
}

export const Spinner = ({ size = 16 }) => <span className="spinner" style={{ width: size, height: size }} role="status" aria-label="Cargando" />;

export function Empty({ title, children, action }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function ErrorNote({ children, onRetry }) {
  if (!children) return null;
  return (
    <div className="error-note" role="alert">
      <span>{children}</span>
      {onRetry && <button className="btn btn-quiet" onClick={onRetry}>Reintentar</button>}
    </div>
  );
}

export function PageHead({ title, children }) {
  return (
    <header className="page-head">
      <h1>{title}</h1>
      <div className="page-head-actions">{children}</div>
    </header>
  );
}

export function Toasts() {
  const { toasts } = useApp();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`}>
          <Icon name={t.tone === "bad" ? "x" : "check"} size={16} />
          {t.text}
        </div>
      ))}
    </div>
  );
}
