import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { Spinner } from "./Ui.jsx";
import { ToolResult } from "./ToolResults.jsx";

// Linea de tiempo del agente: cada paso del plan con su agente, su estado, su duracion y su resultado.
// Los eventos llegan por streaming (plan -> step -> plan_done) y el resultado de cada paso es una tarjeta.

const fmtMs = (ms) => (ms < 950 ? `${Math.max(ms, 1)} ms` : `${(ms / 1000).toFixed(1).replace(".", ",")} s`);

function Elapsed({ since }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 500);
    return () => clearInterval(id);
  }, []);
  return <>{(Math.max(Date.now() - since, 0) / 1000).toFixed(1).replace(".", ",")} s</>;
}

const agentIcon = (agent = "") =>
  /clima/i.test(agent) ? "sun" : /vuelos/i.test(agent) ? "plane" : /cuenta/i.test(agent) ? "suitcase" : "chat";

// "Detalles técnicos": muestra qué pieza de la arquitectura hizo cada paso (útil para explicar el sistema en una demo).
// Por defecto está apagado: la persona usuaria solo ve lenguaje normal.
const TECH_KEY = "tripplanner.techDetails";
const readTech = () => { try { return localStorage.getItem(TECH_KEY) === "1"; } catch { return false; } };
const writeTech = (on) => { try { localStorage.setItem(TECH_KEY, on ? "1" : "0"); } catch { /* sin almacenamiento: solo vale en esta sesión */ } };

const STATUS_TEXT = { pending: "Pendiente", running: "En curso", done: "Listo", error: "Con problema", skipped: "Omitido" };

function Node({ status }) {
  if (status === "running") return <Spinner size={13} />;
  if (status === "done") return <Icon name="check" size={13} strokeWidth={3} />;
  if (status === "error") return <Icon name="x" size={13} strokeWidth={3} />;
  if (status === "skipped") return <span className="tl-dash" />;
  return null;
}

export default function PlanTimeline({ plan, tools = [], onAsk, onAction, busy }) {
  const [open, setOpen] = useState({});
  const [tech, setTech] = useState(readTech);
  const toggleTech = () => setTech((on) => { writeTech(!on); return !on; });
  const steps = plan.steps || [];
  const byStep = Object.fromEntries(tools.filter((t) => t.step).map((t) => [t.step, t]));
  const finished = steps.filter((s) => ["done", "error", "skipped"].includes(s.status)).length;
  const problems = steps.filter((s) => s.status === "error").length;
  const interrupted = plan.done && plan.ok === undefined;
  const hasBody = (s) => {
    const t = byStep[s.step || s.id];
    if (s.id === "save_itinerary") return false;          // su detalle ya dice "Itinerario guardado"; la tarjeta no aporta mas
    return Boolean((t && t.data && t.status !== "error" && s.status !== "pending") || (s.assumptions && s.assumptions.length));
  };
  const isOpen = (s) => open[s.id] ?? hasBody(s);
  const allOpen = steps.filter(hasBody).every((s) => isOpen(s));
  const toggleAll = () => setOpen(Object.fromEntries(steps.map((s) => [s.id, !allOpen])));
  const pct = Math.round((finished / Math.max(steps.length, 1)) * 100);

  let sub;
  if (!plan.done) sub = <>Trabajando en tu viaje · paso {Math.min(finished + 1, steps.length)} de {steps.length} · <Elapsed since={plan.startedAt || Date.now()} /></>;
  else if (interrupted) sub = "Se interrumpió antes de terminar";
  else if (problems) sub = `Terminó en ${fmtMs(plan.ms || 0)} · ${problems} ${problems === 1 ? "paso con problema" : "pasos con problemas"}`;
  else sub = `Terminó en ${fmtMs(plan.ms || 0)} · ${steps.length} pasos`;

  return (
    <section className={`plan ${plan.done ? (problems || interrupted ? "plan-warn" : "plan-ok") : "plan-live"}`} aria-label="Línea de tiempo del agente">
      <header className="plan-head">
        <div className="plan-title">
          <h3>{plan.title}</h3>
          <p className="plan-sub" aria-live="polite">{sub}</p>
        </div>
        <div className="plan-actions">
          {steps.some(hasBody) && (
            <button className="btn btn-quiet" onClick={toggleAll}>{allOpen ? "Contraer todo" : "Expandir todo"}</button>
          )}
          <button className={`btn btn-quiet tl-techbtn ${tech ? "on" : ""}`} aria-pressed={tech} onClick={toggleTech} title="Muestra qué parte del sistema hizo cada paso">
            Detalles técnicos
          </button>
        </div>
      </header>
      <div className="plan-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Progreso del plan"><i style={{ width: `${pct}%` }} /></div>

      <ol className="tl">
        {steps.map((s) => {
          const tool = byStep[s.step || s.id];
          const body = hasBody(s);
          const expanded = body && isOpen(s);
          const time = s.status === "running" && s.startedAt ? <Elapsed since={s.startedAt} /> : s.ms != null && s.status !== "pending" ? fmtMs(s.ms) : null;
          const head = (
            <>
              <span className="tl-label">{s.label}</span>
              <span className="tl-agent"><Icon name={agentIcon(s.agent)} size={13} />{s.agent}</span>
              {time && <span className="tl-time">{time}</span>}
              {body && <span className={`tl-chev ${expanded ? "up" : ""}`} aria-hidden="true" />}
            </>
          );
          return (
            <li key={s.id} className={`tl-step is-${s.status}`}>
              <span className="tl-node" role="img" aria-label={STATUS_TEXT[s.status]}><Node status={s.status} /></span>
              <div className="tl-main">
                {body ? (
                  <button className="tl-head" aria-expanded={expanded} onClick={() => setOpen((o) => ({ ...o, [s.id]: !expanded }))}>{head}</button>
                ) : (
                  <div className="tl-head">{head}</div>
                )}
                <p className="tl-detail">
                  {s.detail || (s.status === "pending" ? "Esperando su turno" : "")}
                  {s.attempt > 1 && s.status !== "pending" && <span className="tl-via"> · intento {s.attempt}</span>}
                </p>
                {tech && s.tech && s.status !== "pending" && <p className="tl-tech"><Icon name="chat" size={12} />{s.tech}</p>}
                {s.status === "error" && s.retry && onAsk && !busy && (
                  <button className="btn btn-small tl-retry" onClick={() => onAsk(s.retry)}><Icon name="refresh" size={14} /> Reintentar</button>
                )}
                {expanded && (
                  <div className="tl-body">
                    {s.assumptions && s.assumptions.length > 0 && (
                      <ul className="tl-assume">{s.assumptions.map((a, i) => <li key={i}>{a}. Dímelo si quieres cambiarlo.</li>)}</ul>
                    )}
                    {tool && tool.status !== "running" && <ToolResult name={tool.name} data={tool.data} onAsk={onAsk} onAction={onAction} compact />}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
