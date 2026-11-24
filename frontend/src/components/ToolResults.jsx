import React from "react";
import Icon from "./Icon.jsx";
import { Status } from "./Ui.jsx";
import { cop, usd, fmtDate, parseDate } from "../lib/format.js";

export const TOOL_META = {
  ask_weather: { run: "Consultando el clima", done: "Clima del destino" },
  ask_flights: { run: "Buscando vuelos reales", done: "Vuelos encontrados" },
  calculate_budget: { run: "Calculando el presupuesto", done: "Presupuesto" },
  create_trip: { run: "Guardando el viaje", done: "Viaje guardado" },
  get_trips: { run: "Revisando tus viajes", done: "Tus viajes" },
  update_trip: { run: "Actualizando el viaje", done: "Viaje actualizado" },
  create_itinerary: { run: "Guardando el itinerario", done: "Itinerario guardado" },
  update_itinerary: { run: "Actualizando el itinerario", done: "Itinerario actualizado" },
  create_reservation: { run: "Registrando la reserva", done: "Reserva registrada (simulada)" },
  get_reservations: { run: "Revisando las reservas", done: "Reservas del viaje" },
  update_reservation: { run: "Actualizando la reserva", done: "Reserva actualizada" },
  delete_reservation: { run: "Eliminando la reserva", done: "Reserva eliminada" },
  plan_itinerary: { run: "Diseñando el itinerario", done: "Itinerario" },
};

/* ---------- Clima: franja de rango de temperatura ---------- */
const weatherIcon = (c = "") => (/lluvia|llovizna|chubasco|tormenta/i.test(c) ? "rain" : /nublado|niebla/i.test(c) ? "cloud" : "sun");

export function WeatherStrip({ data }) {
  const days = data.days || [];
  if (!days.length) return null;
  const lo = Math.min(...days.map((d) => d.temp_min_c ?? d.temp_max_c ?? 0));
  const hi = Math.max(...days.map((d) => d.temp_max_c ?? d.temp_min_c ?? 0));
  const span = Math.max(hi - lo, 1);
  const hist = data.source === "historico_ano_anterior";
  return (
    <section className="wx" aria-label={`Clima en ${data.city}`}>
      <div className="wx-head">
        <h4>{data.city}</h4>
        <span className="muted">{hist ? "Referencia: mismas fechas del año pasado" : "Pronóstico"}</span>
      </div>
      {data.summary && <p className="wx-summary">{data.summary}</p>}
      <ul>
        {days.map((d) => {
          const a = ((d.temp_min_c ?? d.temp_max_c) - lo) / span;
          const b = ((d.temp_max_c ?? d.temp_min_c) - lo) / span;
          return (
            <li key={d.date}>
              <span className="wx-day">{fmtDate(d.date, { weekday: "short", day: "numeric", month: "short" })}</span>
              <span className="wx-cond"><Icon name={weatherIcon(d.condition)} size={18} />{d.condition}</span>
              <span className="wx-bar" aria-hidden="true"><i style={{ left: `${a * 100}%`, width: `${Math.max((b - a) * 100, 6)}%` }} /></span>
              <span className="wx-temp">{d.temp_min_c != null ? `${Math.round(d.temp_min_c)}°` : ""}<b>{d.temp_max_c != null ? ` ${Math.round(d.temp_max_c)}°` : ""}</b></span>
              <span className="wx-rain">{d.rain_probability_pct != null ? `${d.rain_probability_pct}% lluvia` : d.rain_mm != null ? `${d.rain_mm} mm` : ""}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ---------- Vuelos: tiquetes ---------- */
export function FlightTicket({ f, route, action }) {
  const stops = Number(f.stops) || 0;
  return (
    <article className="ticket">
      <div className="ticket-main">
        <div className="ticket-airline">
          <strong>{f.airline || "Aerolínea"}</strong>
          <span className="muted">Vuelo {f.flight || "-"}</span>
        </div>
        <div className="ticket-route">
          <span className="ticket-time">{f.depart || "--:--"}</span>
          <span className="ticket-line" aria-hidden="true"><Icon name="plane" size={16} /></span>
          <span className="ticket-time">{f.arrive || "--:--"}</span>
        </div>
        <span className="muted ticket-stops">{route} {stops === 0 ? "directo" : `${stops} escala${stops > 1 ? "s" : ""}`}</span>
      </div>
      <div className="ticket-stub">
        <span className="ticket-price">{usd(f.price_usd)}</span>
        <span className="muted">por persona</span>
        {action}
      </div>
    </article>
  );
}

export function FlightList({ data, onAsk, onAction }) {
  const flights = data.flights || [];
  if (!flights.length) return <p className="muted">No encontré vuelos para esa ruta y fecha.</p>;
  const route = [data.origin, data.destination].filter(Boolean).join(" a ");
  return (
    <section className="flights" aria-label="Vuelos">
      <div className="wx-head">
        <h4>{route}</h4>
        <span className="muted">{data.date}</span>
      </div>
      {flights.map((f, i) => (
        <FlightTicket
          key={i}
          f={f}
          route=""
          action={(onAction || onAsk) && <button className="btn btn-small" onClick={() => (onAction
            ? onAction({ type: "reserve_flight", flight: f }, `Reservar el vuelo ${f.airline} ${f.flight} (${usd(f.price_usd)})`)
            : onAsk(`Reserva el vuelo ${f.airline} ${f.flight} de las ${f.depart} (USD ${f.price_usd}) para este viaje.`))}>Reservar</button>}
        />
      ))}
    </section>
  );
}

/* ---------- Presupuesto ---------- */
export function BudgetBar({ data }) {
  const items = data.breakdown || [];
  const total = data.totalEstimatedCOP || 0;
  const budget = data.budgetCOP || 0;
  const scale = Math.max(budget, total, 1);
  return (
    <section className="budget" aria-label="Presupuesto">
      <div className="budget-head">
        <div>
          <span className="muted">Estimado para {data.travelers} {data.travelers === 1 ? "persona" : "personas"}, {data.days} días</span>
          <strong className="budget-total">{cop(total)}</strong>
        </div>
        <span className={`pill ${data.withinBudget ? "pill-ok" : "pill-bad"}`}>{data.withinBudget ? `Sobran ${cop(data.remainingCOP)}` : `Faltan ${cop(-data.remainingCOP)}`}</span>
      </div>
      {data.note && <p className="muted budget-note">{data.note}</p>}
      <div className="budget-bar" role="img" aria-label="Distribución del gasto estimado">
        {items.map((it, i) => (
          <i key={i} className={`seg seg-${i}`} style={{ width: `${(it.costCOP / scale) * 100}%` }} title={`${it.item}: ${cop(it.costCOP)}`} />
        ))}
        <b className="budget-mark" style={{ left: `${(budget / scale) * 100}%` }} title={`Presupuesto ${cop(budget)}`} />
      </div>
      <ul className="budget-legend">
        {items.map((it, i) => (
          <li key={i}><i className={`dot seg-${i}`} />{it.item}<b>{cop(it.costCOP)}</b></li>
        ))}
        <li><i className="dot dot-mark" />Tu presupuesto<b>{cop(budget)}</b></li>
      </ul>
    </section>
  );
}

/* ---------- Viaje ---------- */
export function TripLine({ trip, compact, onOpen }) {
  const body = (
    <>
      <div className="trip-route">
        <strong>{trip.origin}</strong>
        <span className="trip-route-line" aria-hidden="true"><Icon name="plane" size={14} /></span>
        <strong>{trip.destination}</strong>
      </div>
      <div className="trip-meta">
        <span>{fmtDate(trip.startDate)} al {fmtDate(trip.endDate)}</span>
        <span>{trip.travelers} {trip.travelers === 1 ? "viajero" : "viajeros"}</span>
        {trip.budget != null && <span>{cop(trip.budget)}</span>}
        <Status value={trip.status} />
      </div>
    </>
  );
  return onOpen ? (
    <button className={`trip-line ${compact ? "compact" : ""}`} onClick={() => onOpen(trip)}>{body}</button>
  ) : (
    <div className={`trip-line ${compact ? "compact" : ""}`}>{body}</div>
  );
}

/* ---------- Itinerario: linea de tiempo ---------- */
const actText = (a) => (typeof a === "string" ? a : a?.name || a?.title || a?.description || a?.activity || JSON.stringify(a));

export function ItineraryView({ days }) {
  if (!Array.isArray(days) || !days.length) return <p className="muted">Este itinerario todavía no tiene días.</p>;
  return (
    <ol className="timeline">
      {days.map((d, i) => (
        <li key={i}>
          <span className="timeline-node">{d.day ?? i + 1}</span>
          <div>
            <h4>{d.title || `Día ${d.day ?? i + 1}`}</h4>
            <ul>
              {(d.activities || []).map((a, j) => (
                <li key={j}>{actText(a)}</li>
              ))}
            </ul>
          </div>
        </li>
      ))}
    </ol>
  );
}

/* ---------- Reserva ---------- */
export const resumeReservation = (r) => {
  const d = r.details || {};
  if (r.type === "VUELO" && (d.airline || d.flight)) return `${d.airline || "Vuelo"} ${d.flight || ""} ${d.depart ? `a las ${d.depart}` : ""}`.trim();
  return d.name || d.hotel || d.activity || d.description || Object.values(d).filter((v) => typeof v === "string").join(", ") || "Sin detalle";
};
export const typeIcon = (t) => ({ VUELO: "plane", HOTEL: "hotel", ACTIVIDAD: "activity" }[t] || "ticket");

export function ReservationLine({ r }) {
  return (
    <div className="res-line">
      <Icon name={typeIcon(r.type)} size={18} />
      <div className="res-line-main">
        <strong>{resumeReservation(r)}</strong>
        <span className="muted">{r.type === "VUELO" && r.details?.price_usd != null ? `${usd(r.details.price_usd)} por persona` : r.type}</span>
      </div>
      <Status value={r.status} />
    </div>
  );
}

const RawNote = ({ data }) => (data.raw ? <p className="muted">{String(data.raw).slice(0, 400)}</p> : null);

export function ToolResult({ name, data, onAsk, onAction, compact }) {
  if (data == null) return null;
  if (typeof data === "string") return <p className="muted">{data}</p>;
  if (data.error) return <p className="inline-error">{String(data.error)}</p>;
  switch (name) {
    case "ask_weather":
      return data.days ? <WeatherStrip data={data} /> : <RawNote data={data} />;
    case "ask_flights":
      return data.flights ? <FlightList data={data} onAsk={onAsk} onAction={onAction} /> : <RawNote data={data} />;
    case "calculate_budget":
      return <BudgetBar data={data} />;
    case "create_trip":
    case "update_trip":
      return <TripLine trip={data} />;
    case "get_trips":
      return <div className="stack">{(data.trips || []).map((t) => <TripLine key={t.tripId} trip={t} compact />)}</div>;
    case "plan_itinerary":
      return <ItineraryView days={data.days} />;
    case "create_itinerary":
    case "update_itinerary":
      return compact ? <p className="muted">Itinerario guardado en tu cuenta (puedes verlo en Mis viajes).</p> : <ItineraryView days={data.days} />;
    case "delete_reservation":
      return <p className="muted">{data.deleted ? "La reserva se eliminó de tu cuenta." : "No se pudo eliminar la reserva."}</p>;
    case "create_reservation":
    case "update_reservation":
      return <ReservationLine r={data} />;
    case "get_reservations":
      return <div className="stack">{(data.reservations || []).map((r) => <ReservationLine key={r.reservationId} r={r} />)}</div>;
    default:
      return null;
  }
}

export { parseDate };
