import React, { useCallback, useEffect, useState } from "react";
import { useApp } from "../AppState.jsx";
import { api } from "../lib/api.js";
import Icon from "../components/Icon.jsx";
import { Empty, ErrorNote, PageHead, Spinner, Status } from "../components/Ui.jsx";
import { ItineraryView, ReservationLine, TripLine } from "../components/ToolResults.jsx";
import { cop, fmtDate, TRIP_STATUS } from "../lib/format.js";

const actText = (a) => (typeof a === "string" ? a : a?.name || a?.title || a?.description || a?.activity || "");

function ItineraryEditor({ trip, itinerary, onSaved }) {
  const { toast } = useApp();
  const [editing, setEditing] = useState(false);
  const [days, setDays] = useState([]);
  const [saving, setSaving] = useState(false);

  const start = () => {
    setDays((itinerary.days || []).map((d, i) => ({ day: d.day ?? i + 1, title: d.title || "", activities: (d.activities || []).map(actText) })));
    setEditing(true);
  };
  const setAct = (i, j, v) => setDays((ds) => ds.map((d, x) => (x === i ? { ...d, activities: d.activities.map((a, y) => (y === j ? v : a)) } : d)));
  const save = async () => {
    setSaving(true);
    try {
      const clean = days.map((d, i) => ({ day: i + 1, title: d.title, activities: d.activities.map((a) => a.trim()).filter(Boolean) }));
      await api.updateItinerary(trip.tripId, itinerary.itineraryId, clean);
      toast("Itinerario actualizado");
      setEditing(false);
      onSaved();
    } catch (e) {
      toast(e.message, "bad");
    } finally {
      setSaving(false);
    }
  };

  if (!editing)
    return (
      <>
        <ItineraryView days={itinerary.days} />
        <button className="btn btn-quiet" onClick={start}><Icon name="pencil" size={16} /> Editar itinerario</button>
      </>
    );
  return (
    <div className="editor">
      {days.map((d, i) => (
        <fieldset key={i}>
          <legend>Día {i + 1}</legend>
          <input className="title-input" value={d.title} placeholder="Título del día" onChange={(e) => setDays((ds) => ds.map((x, k) => (k === i ? { ...x, title: e.target.value } : x)))} />
          {d.activities.map((a, j) => (
            <div className="act-row" key={j}>
              <input value={a} onChange={(e) => setAct(i, j, e.target.value)} aria-label={`Actividad ${j + 1} del día ${i + 1}`} />
              <button className="btn btn-icon" onClick={() => setDays((ds) => ds.map((x, k) => (k === i ? { ...x, activities: x.activities.filter((_, y) => y !== j) } : x)))} aria-label="Quitar actividad"><Icon name="trash" size={16} /></button>
            </div>
          ))}
          <button className="btn btn-quiet" onClick={() => setDays((ds) => ds.map((x, k) => (k === i ? { ...x, activities: [...x.activities, ""] } : x)))}><Icon name="plus" size={14} /> Agregar actividad</button>
        </fieldset>
      ))}
      <div className="editor-actions">
        <button className="btn btn-quiet" onClick={() => setDays((ds) => [...ds, { day: ds.length + 1, title: "", activities: [""] }])}><Icon name="plus" size={14} /> Agregar día</button>
        <span className="spacer" />
        <button className="btn btn-quiet" onClick={() => setEditing(false)}>Cancelar</button>
        <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? "Guardando…" : "Guardar cambios"}</button>
      </div>
    </div>
  );
}

function TripDetail({ trip }) {
  const { refreshTrips, toast, goto } = useApp();
  const [tab, setTab] = useState("itinerary");
  const [itins, setItins] = useState(null);
  const [res, setRes] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [i, r] = await Promise.all([api.itineraries(trip.tripId), api.reservations(trip.tripId)]);
      i.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
      setItins(i);
      setRes(r);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, [trip.tripId]);

  useEffect(() => {
    setItins(null);
    setRes(null);
    load();
  }, [load]);

  const changeStatus = async (status) => {
    try {
      await api.updateTrip(trip.tripId, { status });
      await refreshTrips();
      toast("Estado del viaje actualizado");
    } catch (e) {
      toast(e.message, "bad");
    }
  };

  const itinerary = itins && itins[0];
  return (
    <section className="trip-detail">
      <div className="trip-detail-head">
        <div className="trip-route big">
          <h2>{trip.origin}</h2>
          <span className="trip-route-line" aria-hidden="true"><Icon name="plane" size={16} /></span>
          <h2>{trip.destination}</h2>
        </div>
        <div className="trip-meta">
          <span>{fmtDate(trip.startDate, { day: "numeric", month: "long" })} al {fmtDate(trip.endDate, { day: "numeric", month: "long", year: "numeric" })}</span>
          <span>{trip.travelers} {trip.travelers === 1 ? "viajero" : "viajeros"}</span>
          {trip.budget != null && <span>{cop(trip.budget)}</span>}
        </div>
        <label className="inline-select">Estado
          <select value={trip.status} onChange={(e) => changeStatus(e.target.value)}>
            {TRIP_STATUS.map((s) => <option key={s} value={s}>{s.charAt(0) + s.slice(1).toLowerCase()}</option>)}
          </select>
        </label>
      </div>

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "itinerary"} onClick={() => setTab("itinerary")}>Itinerario</button>
        <button role="tab" aria-selected={tab === "reservations"} onClick={() => setTab("reservations")}>Reservas{res ? ` (${res.length})` : ""}</button>
      </div>

      <ErrorNote onRetry={load}>{error}</ErrorNote>
      {!error && itins === null && <p className="progress"><Spinner /> Cargando tus viajes…</p>}

      {tab === "itinerary" && itins && (
        itinerary ? (
          <ItineraryEditor key={itinerary.itineraryId + (itinerary.updatedAt || "")} trip={trip} itinerary={itinerary} onSaved={load} />
        ) : (
          <Empty title="Este viaje no tiene itinerario" action={<button className="btn btn-primary" onClick={() => goto("assistant", { prompt: `Arma un itinerario para mi viaje de ${trip.origin} a ${trip.destination} (tripId ${trip.tripId}), del ${trip.startDate} al ${trip.endDate}, y guárdalo.` })}>Pedirlo al asistente</button>}>
            El asistente puede proponer actividades según el clima y guardarlas aquí.
          </Empty>
        )
      )}
      {tab === "reservations" && res && (
        res.length ? <div className="stack">{res.map((r) => <ReservationLine key={r.reservationId} r={r} />)}<button className="btn btn-quiet" onClick={() => goto("reservations")}>Gestionar reservas</button></div>
        : <Empty title="Sin reservas todavía">Pídele al asistente que reserve un vuelo o agrega una desde Reservas.</Empty>
      )}
    </section>
  );
}

export default function Trips() {
  const { trips, tripsLoading, tripsError, refreshTrips, nav, goto } = useApp();
  const [sel, setSel] = useState(nav.params?.tripId || "");
  useEffect(() => {
    if (nav.params?.tripId) setSel(nav.params.tripId);
  }, [nav.params?.tripId]);
  const current = trips.find((t) => t.tripId === sel) || trips[0];

  return (
    <div className="page">
      <PageHead title="Mis viajes">
        <button className="btn btn-quiet" onClick={refreshTrips}><Icon name="refresh" size={16} /> Actualizar</button>
        <button className="btn btn-primary" onClick={() => goto("assistant")}><Icon name="plus" size={16} /> Nuevo viaje</button>
      </PageHead>
      <ErrorNote onRetry={refreshTrips}>{tripsError}</ErrorNote>
      {tripsLoading && trips.length === 0 && <p className="progress"><Spinner /> Cargando tus viajes…</p>}
      {!tripsLoading && trips.length === 0 ? (
        <Empty title="Todavía no has guardado viajes" action={<button className="btn btn-primary" onClick={() => goto("assistant")}>Planear con el asistente</button>}>
          Lo que planees queda guardado en tu cuenta: puedes cerrar la app y volver.
        </Empty>
      ) : (
        <div className="split">
          <nav className="trip-list" aria-label="Lista de viajes">
            {trips.map((t) => (
              <button key={t.tripId} className={`trip-pick ${current?.tripId === t.tripId ? "on" : ""}`} onClick={() => setSel(t.tripId)}>
                <TripLine trip={t} compact />
              </button>
            ))}
          </nav>
          {current && <TripDetail key={current.tripId} trip={current} />}
        </div>
      )}
    </div>
  );
}
