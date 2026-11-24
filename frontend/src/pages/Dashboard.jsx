import React from "react";
import { useApp } from "../AppState.jsx";
import { useReservations } from "../hooks/useReservations.js";
import Icon from "../components/Icon.jsx";
import { Empty, ErrorNote, Spinner, Status } from "../components/Ui.jsx";
import { TripLine, ReservationLine } from "../components/ToolResults.jsx";
import { cop, daysUntil, fmtDate, prettyUser, STATUS_LABEL } from "../lib/format.js";

function pickFeatured(trips) {
  const upcoming = trips
    .map((t) => ({ t, d: daysUntil(t.startDate) }))
    .filter((x) => x.d != null && x.d >= 0)
    .sort((a, b) => a.d - b.d);
  return upcoming.length ? { trip: upcoming[0].t, days: upcoming[0].d, upcoming: true } : trips.length ? { trip: trips[0], days: null, upcoming: false } : null;
}

export default function Dashboard() {
  const { trips, tripsLoading, tripsError, refreshTrips, goto, userId } = useApp();
  const res = useReservations(trips);
  const featured = pickFeatured(trips);
  const counts = { SIMULADA: 0, PENDIENTE: 0, CONFIRMADA: 0 };
  res.items.forEach((r) => { if (counts[r.status] != null) counts[r.status] += 1; });
  const committed = trips.reduce((s, t) => s + (Number(t.budget) || 0), 0);

  return (
    <div className="page">
      {featured ? (
        <section className="hero">
          <p className="hero-kicker">{featured.upcoming ? "Tu próximo viaje" : "Tu último viaje"}</p>
          <div className="hero-route">
            <h1>{featured.trip.origin}</h1>
            <span className="hero-line" aria-hidden="true"><Icon name="plane" size={22} /></span>
            <h1>{featured.trip.destination}</h1>
          </div>
          <div className="hero-meta">
            <span>{fmtDate(featured.trip.startDate, { day: "numeric", month: "long" })} al {fmtDate(featured.trip.endDate, { day: "numeric", month: "long" })}</span>
            {featured.days != null && <span>{featured.days === 0 ? "sales hoy" : featured.days === 1 ? "sales mañana" : `faltan ${featured.days} días`}</span>}
            <span>{featured.trip.travelers} {featured.trip.travelers === 1 ? "viajero" : "viajeros"}</span>
            <Status value={featured.trip.status} />
          </div>
          <div className="hero-actions">
            <button className="btn btn-primary" onClick={() => goto("trips", { tripId: featured.trip.tripId })}>Ver itinerario</button>
            <button className="btn btn-ghost-light" onClick={() => goto("assistant")}>Planear otro viaje</button>
          </div>
        </section>
      ) : (
        <section className="hero hero-empty">
          <h1>Hola, {prettyUser(userId)}. Planeemos tu primer viaje.</h1>
          <p>El asistente busca vuelos reales, revisa el clima, arma el itinerario y lo deja guardado aquí.</p>
          <div className="hero-actions">
            <button className="btn btn-primary" onClick={() => goto("assistant")}>Hablar con el asistente</button>
          </div>
        </section>
      )}

      <ErrorNote onRetry={refreshTrips}>{tripsError}</ErrorNote>

      <div className="dash-grid">
        <section>
          <div className="section-head">
            <h2>Mis viajes</h2>
            {tripsLoading && <Spinner />}
          </div>
          {trips.length === 0 && !tripsLoading ? (
            <Empty title="Aún no tienes viajes" action={<button className="btn btn-primary" onClick={() => goto("assistant")}>Crear el primero</button>}>
              Cuando el asistente guarde un viaje, aparecerá aquí aunque cierres la app.
            </Empty>
          ) : (
            <div className="stack">
              {trips.slice(0, 5).map((t) => <TripLine key={t.tripId} trip={t} compact onOpen={() => goto("trips", { tripId: t.tripId })} />)}
              {trips.length > 5 && <button className="btn btn-quiet" onClick={() => goto("trips")}>Ver los {trips.length} viajes</button>}
            </div>
          )}
        </section>

        <section>
          <div className="section-head">
            <h2>Reservas</h2>
            {res.loading && <Spinner />}
          </div>
          <div className="counters">
            {Object.keys(counts).map((k) => (
              <div key={k} className="counter">
                <b>{counts[k]}</b>
                <span>{STATUS_LABEL[k]}{counts[k] === 1 ? "" : "s"}</span>
              </div>
            ))}
          </div>
          <div className="stack">
            {res.items.slice(0, 3).map((r) => <ReservationLine key={r.reservationId} r={r} />)}
            {res.items.length === 0 && !res.loading && <p className="muted">Todavía no hay reservas. Son simuladas, pero quedan guardadas.</p>}
          </div>
          {res.items.length > 0 && <button className="btn btn-quiet" onClick={() => goto("reservations")}>Gestionar reservas</button>}
          {trips.length > 0 && <p className="muted budget-note">Presupuesto total de tus viajes: <b>{cop(committed)}</b></p>}
        </section>
      </div>
    </div>
  );
}
