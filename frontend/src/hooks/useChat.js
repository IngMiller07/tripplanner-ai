import { useCallback, useEffect, useRef, useState } from "react";
import { streamChat } from "../lib/stream.js";
import { uid } from "../lib/format.js";

const MUTATING = new Set(["create_trip", "update_trip", "create_itinerary", "update_itinerary", "create_reservation", "update_reservation", "delete_reservation"]);

// Una linea de tiempo que quedo a medias (se cerro la pagina, se detuvo el stream) no puede quedar "trabajando".
export const closePlan = (p) =>
  p.type !== "plan" ? p : {
    ...p,
    done: true,
    steps: p.steps.map((s) => (s.status === "running" ? { ...s, status: "error", detail: "Interrumpido" } : s.status === "pending" ? { ...s, status: "skipped", detail: s.detail || "No se ejecutó" } : s)),
  };

const load = (key) => {
  try {
    const s = JSON.parse(localStorage.getItem(key) || "null");
    if (s && Array.isArray(s.messages)) {
      // un stream interrumpido al cerrar la pagina no debe quedar "cargando"
      s.messages = s.messages.map((m) => ({ ...m, streaming: false, parts: (m.parts || []).map((p) => closePlan(p.status === "running" ? { ...p, status: "error" } : p)) }));
      return s;
    }
  } catch {
    /* ignora */
  }
  return { sessionId: uid(), messages: [] };
};
const save = (key, v) => {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* ignora */
  }
};

export const textOf = (m) =>
  (m.parts || []).filter((p) => p.type === "text").map((p) => p.text).join("")
    .replace(/<thinking>[\s\S]*?<\/thinking>\s*/g, "").trim();

// Contexto compacto (viaje activo, vuelos mostrados...) que viaja con cada mensaje al Orchestrator.
export function buildContext(messages) {
  const ctx = {};
  for (const m of messages) {
    for (const p of m.parts || []) {
      if (p.type !== "tool" || !p.data || typeof p.data !== "object" || p.data.error) continue;
      if (p.name === "create_trip" && p.data.tripId) {
        const { tripId, origin, destination, startDate, endDate, travelers, budget } = p.data;
        ctx.activeTrip = { tripId, origin, destination, startDate, endDate, travelers, budget };
      }
      if (p.name === "update_trip" && p.data.tripId) {
        // un cambio (dias, presupuesto, viajeros, fechas) deja al viaje activo con sus datos nuevos
        const { tripId, origin, destination, startDate, endDate, travelers, budget } = p.data;
        ctx.activeTrip = { ...(ctx.activeTrip && ctx.activeTrip.tripId === tripId ? ctx.activeTrip : {}), tripId, origin, destination, startDate, endDate, travelers, budget };
      }
      if (p.name === "ask_flights" && Array.isArray(p.data.flights)) {
        ctx.lastFlights = { route: `${p.data.origin}-${p.data.destination}`, date: p.data.date, options: p.data.flights.slice(0, 3) };
      }
      if ((p.name === "create_itinerary" || p.name === "update_itinerary") && p.data.itineraryId) ctx.activeItineraryId = p.data.itineraryId;
      const brief = (r) => ({ reservationId: r.reservationId, tripId: r.tripId, type: r.type, status: r.status, label: r.details?.name || [r.details?.airline, r.details?.flight].filter(Boolean).join(" ") || undefined });
      if (p.name === "create_reservation" && p.data.reservationId) (ctx.reservations ||= []).push(brief(p.data));
      if (p.name === "get_reservations" && Array.isArray(p.data.reservations)) ctx.reservations = p.data.reservations.map(brief);
      if (p.name === "delete_reservation" && p.data.deleted) ctx.reservations = (ctx.reservations || []).filter((r) => r.reservationId !== p.data.reservationId);
    }
  }
  return ctx;
}

export function useChat(userId, { onChanged, storageKey } = {}) {
  const key = storageKey || `tp.chat.${userId}`;
  const [state, setState] = useState(() => (storageKey === null ? { sessionId: uid(), messages: [] } : load(key)));
  const [busy, setBusy] = useState(false);
  const stateRef = useRef(state);
  stateRef.current = state;
  const abortRef = useRef(null);
  const persist = storageKey !== null;

  useEffect(() => {
    if (persist) save(key, state);
  }, [key, state, persist]);

  // Cambio de usuario => carga su propia conversacion.
  useEffect(() => {
    setState(persist ? load(key) : { sessionId: uid(), messages: [] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const send = useCallback(
    async (text, { withHistory = true, action } = {}) => {
      const prompt = (text || "").trim();
      if (!prompt || busy) return;
      const prev = stateRef.current.messages;
      const history = withHistory ? prev.map((m) => ({ role: m.role, content: textOf(m) })).filter((h) => h.content) : [];
      const context = withHistory ? buildContext(prev) : {};
      const asstId = uid();
      setState((s) => ({
        ...s,
        messages: [...s.messages, { id: uid(), role: "user", parts: [{ type: "text", text: prompt }] }, { id: asstId, role: "assistant", parts: [], streaming: true }],
      }));
      setBusy(true);
      const ctl = new AbortController();
      abortRef.current = ctl;
      const patch = (fn) => setState((s) => ({ ...s, messages: s.messages.map((m) => (m.id === asstId ? fn(m) : m)) }));
      let changed = false;

      const onEvent = (ev) => {
        if (ev.type === "text" && typeof ev.data === "string") {
          patch((m) => {
            const parts = [...m.parts];
            const last = parts[parts.length - 1];
            if (last && last.type === "text") parts[parts.length - 1] = { ...last, text: last.text + ev.data };
            else parts.push({ type: "text", text: ev.data });
            return { ...m, parts };
          });
        } else if (ev.type === "plan") {
          // linea de tiempo del agente: arranca con todos los pasos pendientes
          patch((m) => ({ ...m, parts: [...m.parts, { type: "plan", id: ev.id, title: ev.title, startedAt: Date.now(), done: false, steps: (ev.steps || []).map((st) => ({ ...st, status: "pending" })) }] }));
        } else if (ev.type === "step") {
          patch((m) => ({
            ...m,
            parts: m.parts.map((p) =>
              p.type === "plan" && p.id === ev.plan
                ? {
                    ...p,
                    title: ev.title || p.title,
                    steps: p.steps.map((st) =>
                      st.id !== ev.id ? st : {
                        ...st,
                        status: ev.status,
                        label: ev.label || st.label,
                        detail: ev.detail ?? st.detail,
                        tech: ev.tech || st.tech,
                        ms: ev.ms ?? st.ms,
                        attempt: ev.attempt ?? st.attempt,
                        retry: ev.retry ?? st.retry,
                        assumptions: ev.assumptions ?? st.assumptions,
                        startedAt: ev.status === "running" ? Date.now() : st.startedAt,
                      }
                    ),
                  }
                : p
            ),
          }));
        } else if (ev.type === "plan_done") {
          patch((m) => ({ ...m, parts: m.parts.map((p) => (p.type === "plan" && p.id === ev.plan ? { ...p, done: true, ok: ev.ok, ms: ev.ms } : p)) }));
        } else if (ev.type === "suggestions" && Array.isArray(ev.items)) {
          patch((m) => ({ ...m, parts: [...m.parts, { type: "suggestions", items: ev.items }] }));
        } else if (ev.type === "tool_start") {
          patch((m) => (m.parts.some((p) => p.id === ev.id) ? m : { ...m, parts: [...m.parts, { type: "tool", id: ev.id, name: ev.name, status: "running", step: ev.step }] }));
        } else if (ev.type === "tool_result") {
          if (MUTATING.has(ev.name)) changed = true;
          const status = ev.status === "error" ? "error" : "done";
          patch((m) => {
            const exists = m.parts.some((p) => p.id === ev.id);
            const parts = exists
              ? m.parts.map((p) => (p.id === ev.id ? { ...p, name: ev.name || p.name, status, data: ev.data, step: ev.step || p.step } : p))
              : [...m.parts, { type: "tool", id: ev.id, name: ev.name, status, data: ev.data, step: ev.step }];
            return { ...m, parts };
          });
        } else if (ev.type === "error") {
          patch((m) => ({ ...m, parts: [...m.parts, { type: "error", text: ev.message || "Error desconocido" }] }));
        }
      };

      try {
        await streamChat({ prompt, userId, sessionId: stateRef.current.sessionId, history, context, ...(action ? { action } : {}) }, { signal: ctl.signal, onEvent });
      } catch (e) {
        if (e.name !== "AbortError") patch((m) => ({ ...m, parts: [...m.parts, { type: "error", text: e.message }] }));
      } finally {
        patch((m) => ({ ...m, streaming: false, parts: m.parts.map((p) => closePlan(p.status === "running" ? { ...p, status: "error" } : p)) }));
        setBusy(false);
        if (changed) onChanged?.();
      }
    },
    [busy, userId, onChanged]
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => setState({ sessionId: uid(), messages: [] }), []);
  return { messages: state.messages, busy, send, stop, reset };
}
