import React, { useEffect, useRef, useState } from "react";
import { useApp } from "../AppState.jsx";
import { useChat } from "../hooks/useChat.js";
import Icon from "../components/Icon.jsx";
import Markdown from "../components/Markdown.jsx";
import { Spinner } from "../components/Ui.jsx";
import { TOOL_META, ToolResult } from "../components/ToolResults.jsx";
import PlanTimeline from "../components/PlanTimeline.jsx";
import { isConfigured } from "../lib/config.js";

export const DEMO_PROMPT =
  "Quiero organizar unas vacaciones de cuatro días en Cartagena. Salgo desde Bogotá, viajo con dos personas más y tengo un presupuesto de 5 millones de pesos. Quiero conocer el clima, encontrar vuelos y organizar algunas actividades turísticas.";

function Step({ part, onAsk, onAction }) {
  if (part.type === "text") return <div className="step step-text"><Markdown text={part.text} /></div>;
  if (part.type === "error") return <div className="step step-text"><p className="inline-error">{part.text}</p></div>;
  const meta = TOOL_META[part.name] || { run: part.name, done: part.name };
  const state = part.status;
  return (
    <div className={`step step-tool is-${state}`}>
      <span className="node" aria-hidden="true">
        {state === "running" ? <Spinner size={12} /> : state === "error" ? <Icon name="x" size={12} strokeWidth={3} /> : <Icon name="check" size={12} strokeWidth={3} />}
      </span>
      <div className="step-title">{state === "running" ? `${meta.run}…` : state === "error" ? `${meta.done}: no se pudo completar` : meta.done}</div>
      {state !== "running" && <ToolResult name={part.name} data={part.data} onAsk={onAsk} onAction={onAction} />}
    </div>
  );
}

function Suggestions({ items, onAsk, onAction, goto, hideReserve }) {
  const shown = items.filter((it) => !(hideReserve && it.action?.type === "reserve_flight"));
  if (!shown.length) return null;
  const run = (it) => {
    if (it.action) onAction(it.action, `${it.label}${it.action.flight ? ` (${it.action.flight.airline || ""} ${it.action.flight.flight || ""})` : ""}`.replace("  ", " "));
    else if (it.goto) goto(it.goto);
    else if (it.prompt) onAsk(it.prompt);
  };
  return (
    <div className="quick" role="group" aria-label="Siguientes pasos">
      {shown.map((it, i) => (
        <button key={i} className={`quick-btn ${i === 0 ? "primary" : ""}`} onClick={() => run(it)}>
          {it.icon && <Icon name={it.icon} size={16} />}{it.label}
        </button>
      ))}
    </div>
  );
}

function AssistantMessage({ m, onAsk, onAction, goto, isLast, busy, hideReserve }) {
  const tools = m.parts.filter((p) => p.type === "tool");
  const hasLooseTools = tools.some((p) => !p.step);       // herramientas del agente libre (fuera de una linea de tiempo)
  const linked = tools.filter((p) => p.step);
  return (
    <div className="msg msg-asst">
      <div className={`rail ${hasLooseTools ? "has-tools" : ""}`}>
        {m.parts.length === 0 && m.streaming && (
          <div className="step step-text thinking"><Spinner size={14} /> Pensando…</div>
        )}
        {m.parts.map((p, i) => {
          if (p.type === "plan") return <PlanTimeline key={p.id || i} plan={p} tools={linked} onAsk={onAsk} onAction={onAction} busy={busy} />;
          if (p.type === "tool" && p.step) return null;
          if (p.type === "suggestions") return isLast && !busy && !m.streaming ? <Suggestions key={`s${i}`} items={p.items} onAsk={onAsk} onAction={onAction} goto={goto} hideReserve={hideReserve} /> : null;
          return <Step key={p.id || i} part={p} onAsk={onAsk} onAction={onAction} />;
        })}
        {m.streaming && m.parts.length > 0 && m.parts[m.parts.length - 1].type === "text" && <span className="cursor" aria-hidden="true" />}
      </div>
    </div>
  );
}

export default function Assistant() {
  const { userId, refreshTrips, nav, goto } = useApp();
  const chat = useChat(userId, { onChanged: refreshTrips });
  const [input, setInput] = useState(nav.params?.prompt || "");
  const scroller = useRef(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [chat.messages]);

  const onScroll = () => {
    const el = scroller.current;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  };

  const submit = (text) => {
    const t = (text ?? input).trim();
    if (!t || chat.busy) return;
    stick.current = true;
    setInput("");
    chat.send(t);
  };

  const act = (action, label) => {
    if (chat.busy) return;
    stick.current = true;
    chat.send(label, { action });
  };
  const lastAsstMsg = [...chat.messages].reverse().find((m) => m.role === "assistant");
  // "Reservar" se oculta solo si el ULTIMO mensaje ya reservo algo; evitar duplicados lo garantiza el servidor.
  const reserved = !!lastAsstMsg && lastAsstMsg.parts.some((p) => p.type === "tool" && p.name === "create_reservation" && p.status === "done" && !p.data?.error);
  const lastAsst = lastAsstMsg?.id;

  const empty = chat.messages.length === 0;
  return (
    <div className="chat">
      <header className="chat-head">
        <h1>Asistente de viajes</h1>
        {!empty && (
          <button className="btn btn-quiet" onClick={chat.reset} disabled={chat.busy}>
            <Icon name="plus" size={16} /> Nueva conversación
          </button>
        )}
      </header>

      {!isConfigured() && (
        <div className="error-note" role="alert">Falta configurar las URLs del backend en <code>config.js</code>. Mira el README para completar API_URL y STREAM_URL.</div>
      )}

      <div className="chat-scroll" ref={scroller} onScroll={onScroll}>
        {empty ? (
          <div className="chat-empty">
            <h2>¿A dónde quieres ir?</h2>
            <p>Cuéntame origen, destino, fechas, cuántas personas viajan y tu presupuesto. Yo busco vuelos reales y el clima, armo el itinerario y lo guardo en tu cuenta.</p>
            <div className="suggestions">
              <button className="suggestion" onClick={() => setInput(DEMO_PROMPT)}>
                <strong>Vacaciones de 4 días en Cartagena</strong>
                <span>Desde Bogotá, 3 personas, 5 millones de pesos</span>
              </button>
              <button className="suggestion" onClick={() => setInput("¿Cuáles son mis viajes guardados?")}>
                <strong>Ver mis viajes</strong>
                <span>Recupera lo que ya planeaste</span>
              </button>
            </div>
          </div>
        ) : (
          <div className="chat-thread">
            {chat.messages.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="msg msg-user"><p>{m.parts[0]?.text}</p></div>
              ) : (
                <AssistantMessage key={m.id} m={m} onAsk={submit} onAction={act} goto={goto} isLast={m.id === lastAsst} busy={chat.busy} hideReserve={reserved} />
              )
            )}
          </div>
        )}
      </div>

      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
          placeholder="Escribe tu viaje ideal…"
          rows={2}
          aria-label="Mensaje para el asistente"
        />
        {chat.busy ? (
          <button type="button" className="btn btn-stop" onClick={chat.stop} aria-label="Detener respuesta"><Icon name="stop" size={18} /></button>
        ) : (
          <button type="submit" className="btn btn-primary btn-send" disabled={!input.trim()} aria-label="Enviar mensaje"><Icon name="send" size={18} /></button>
        )}
      </form>
    </div>
  );
}
