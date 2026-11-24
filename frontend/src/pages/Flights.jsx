import React, { useState } from "react";
import { useApp } from "../AppState.jsx";
import { useChat } from "../hooks/useChat.js";
import { api } from "../lib/api.js";
import { PageHead, Spinner, ErrorNote } from "../components/Ui.jsx";
import { FlightTicket } from "../components/ToolResults.jsx";
import { addDaysISO, CITIES, toDMY, fmtDate } from "../lib/format.js";

export default function Flights() {
  const { userId, trips, refreshTrips, toast, goto } = useApp();
  const chat = useChat(userId, { storageKey: null });
  const [form, setForm] = useState({ origin: "Bogota", destination: "Cartagena", date: addDaysISO(10) });
  const [tripId, setTripId] = useState("");
  const [reserving, setReserving] = useState("");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const last = [...chat.messages].reverse().find((m) => m.role === "assistant");
  const part = last?.parts.find((p) => p.type === "tool" && p.name === "ask_flights");
  const data = part?.data;
  const flights = data && Array.isArray(data.flights) ? data.flights : null;
  const failure = last?.parts.find((p) => p.type === "error")?.text || (data && data.error);
  const note = !flights && !failure && last && !last.streaming ? last.parts.filter((p) => p.type === "text").map((p) => p.text).join("") : "";

  const search = (e) => {
    e.preventDefault();
    chat.send(`Busca vuelos reales de ${form.origin} a ${form.destination} para el ${toDMY(form.date)}. Solo consulta con ask_flights y no guardes nada.`, { withHistory: false });
  };

  const reserve = async (f) => {
    setReserving(`${f.flight}-${f.depart}`);
    try {
      let id = tripId || trips[0]?.tripId;
      if (!id) {
        const t = await api.createTrip({ userId, origin: form.origin, destination: form.destination, startDate: toDMY(form.date), endDate: toDMY(form.date), travelers: 1 });
        id = t.tripId;
      }
      await api.createReservation(id, "VUELO", { ...f, origin: form.origin, destination: form.destination, date: toDMY(form.date) });
      await refreshTrips();
      toast("Reserva simulada guardada");
    } catch (e) {
      toast(e.message, "bad");
    } finally {
      setReserving("");
    }
  };

  return (
    <div className="page">
      <PageHead title="Buscar vuelos" />
      <form className="form-row" onSubmit={search}>
        <label>Origen<input list="cities" value={form.origin} onChange={set("origin")} required /></label>
        <label>Destino<input list="cities" value={form.destination} onChange={set("destination")} required /></label>
        <label>Fecha de salida<input type="date" value={form.date} min={addDaysISO(0)} onChange={set("date")} required /></label>
        <button className="btn btn-primary" disabled={chat.busy}>{chat.busy ? "Buscando…" : "Buscar vuelos"}</button>
        <datalist id="cities">{CITIES.map((c) => <option key={c} value={c} />)}</datalist>
      </form>

      {chat.busy && <p className="progress"><Spinner /> Consultando el Flights Agent con datos reales…</p>}
      <ErrorNote>{failure}</ErrorNote>
      {note && <p className="muted">{note}</p>}

      {flights && flights.length > 0 && (
        <section className="flights">
          <div className="reserve-bar">
            <h2>{data.origin || form.origin} a {data.destination || form.destination}, {fmtDate(form.date, { day: "numeric", month: "long" })}</h2>
            {trips.length > 0 ? (
              <label className="inline-select">Reservar en
                <select value={tripId || trips[0].tripId} onChange={(e) => setTripId(e.target.value)}>
                  {trips.map((t) => <option key={t.tripId} value={t.tripId}>{t.origin} a {t.destination}, {fmtDate(t.startDate)}</option>)}
                </select>
              </label>
            ) : (
              <span className="muted">Se creará un viaje nuevo con esta ruta al reservar.</span>
            )}
          </div>
          {flights.map((f, i) => (
            <FlightTicket key={i} f={f} route=""
              action={<button className="btn btn-small" disabled={reserving === `${f.flight}-${f.depart}`} onClick={() => reserve(f)}>{reserving === `${f.flight}-${f.depart}` ? "Reservando…" : "Reservar"}</button>} />
          ))}
          <p className="muted">Las reservas son simuladas: se guardan en tu cuenta pero no emiten tiquetes reales. <button className="link" onClick={() => goto("reservations")}>Ver mis reservas</button></p>
        </section>
      )}
    </div>
  );
}
