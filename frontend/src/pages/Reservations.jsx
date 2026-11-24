import React, { useState } from "react";
import { useApp } from "../AppState.jsx";
import { useReservations } from "../hooks/useReservations.js";
import { api } from "../lib/api.js";
import Icon from "../components/Icon.jsx";
import { Empty, ErrorNote, PageHead, Spinner } from "../components/Ui.jsx";
import { resumeReservation, typeIcon } from "../components/ToolResults.jsx";
import { fmtDate, STATUS_LABEL, usd } from "../lib/format.js";

const STATES = ["SIMULADA", "PENDIENTE", "CONFIRMADA"];

export default function Reservations() {
  const { trips, toast, goto } = useApp();
  const { items, loading, error, reload } = useReservations(trips);
  const [filter, setFilter] = useState("TODAS");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ tripId: "", type: "HOTEL", name: "", price: "" });
  const [busy, setBusy] = useState("");
  const [confirm, setConfirm] = useState("");

  const shown = filter === "TODAS" ? items : items.filter((r) => r.status === filter);

  const changeStatus = async (r, status) => {
    setBusy(r.reservationId);
    try {
      await api.updateReservation(r.tripId, r.reservationId, status);
      toast(`Reserva ${STATUS_LABEL[status].toLowerCase()}`);
      await reload();
    } catch (e) {
      toast(e.message, "bad");
    } finally {
      setBusy("");
    }
  };

  const remove = async (r) => {
    setBusy(r.reservationId);
    try {
      await api.deleteReservation(r.tripId, r.reservationId);
      toast("Reserva eliminada");
      setConfirm("");
      await reload();
    } catch (e) {
      toast(e.message, "bad");
    } finally {
      setBusy("");
    }
  };

  const create = async (e) => {
    e.preventDefault();
    const tripId = form.tripId || trips[0]?.tripId;
    if (!tripId) return;
    setBusy("new");
    try {
      const details = { name: form.name.trim() };
      if (form.price !== "") details.price_usd = Number(form.price);
      await api.createReservation(tripId, form.type, details);
      toast("Reserva simulada guardada");
      setForm({ tripId: "", type: "HOTEL", name: "", price: "" });
      setShowForm(false);
      await reload();
    } catch (err) {
      toast(err.message, "bad");
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="page">
      <PageHead title="Reservas">
        <button className="btn btn-primary" onClick={() => setShowForm((v) => !v)} disabled={trips.length === 0}><Icon name="plus" size={16} /> Nueva reserva</button>
      </PageHead>
      <p className="muted lead">Las reservas son simuladas: se guardan en tu cuenta, pero no se cobra ni se emite nada. No ingreses datos de tarjetas.</p>

      {showForm && (
        <form className="form-row" onSubmit={create}>
          <label>Viaje
            <select value={form.tripId || trips[0]?.tripId} onChange={(e) => setForm({ ...form, tripId: e.target.value })}>
              {trips.map((t) => <option key={t.tripId} value={t.tripId}>{t.origin} a {t.destination}, {fmtDate(t.startDate)}</option>)}
            </select>
          </label>
          <label>Tipo
            <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
              <option value="VUELO">Vuelo</option><option value="HOTEL">Hotel</option><option value="ACTIVIDAD">Actividad</option>
            </select>
          </label>
          <label>Descripción<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Hotel Casa del Arzobispado" required /></label>
          <label>Precio en USD (opcional)<input type="number" min="0" step="1" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></label>
          <button className="btn btn-primary" disabled={busy === "new"}>{busy === "new" ? "Guardando…" : "Guardar reserva"}</button>
        </form>
      )}

      <div className="chips" role="group" aria-label="Filtrar por estado">
        {["TODAS", ...STATES].map((s) => (
          <button key={s} className={`chip ${filter === s ? "on" : ""}`} aria-pressed={filter === s} onClick={() => setFilter(s)}>
            {s === "TODAS" ? "Todas" : STATUS_LABEL[s]}
            <b>{s === "TODAS" ? items.length : items.filter((r) => r.status === s).length}</b>
          </button>
        ))}
        {loading && <Spinner />}
      </div>

      <ErrorNote onRetry={reload}>{error}</ErrorNote>
      {shown.length === 0 && !loading ? (
        <Empty title={items.length ? "Ninguna reserva con ese estado" : "Aún no tienes reservas"} action={!items.length && <button className="btn btn-primary" onClick={() => goto("assistant")}>Pedirle una al asistente</button>}>
          {items.length ? "Prueba con otro filtro." : "Cuando el asistente o tú reserven un vuelo, hotel o actividad, aparecerá aquí."}
        </Empty>
      ) : (
        <ul className="res-table">
          {shown.map((r) => (
            <li key={r.reservationId}>
              <span className="res-icon"><Icon name={typeIcon(r.type)} size={18} /></span>
              <div className="res-main">
                <strong>{resumeReservation(r)}</strong>
                <span className="muted">{r.trip.origin} a {r.trip.destination}, {fmtDate(r.createdAt, { day: "numeric", month: "short" })}</span>
              </div>
              <span className="res-price">{r.details?.price_usd != null ? usd(r.details.price_usd) : ""}</span>
              <select aria-label="Estado de la reserva" className={`state-select s-${r.status}`} value={r.status} disabled={busy === r.reservationId} onChange={(e) => changeStatus(r, e.target.value)}>
                {STATES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
              </select>
              {confirm === r.reservationId ? (
                <span className="res-confirm" role="group" aria-label="Confirmar eliminación">
                  <button className="btn btn-small btn-danger" disabled={busy === r.reservationId} onClick={() => remove(r)}>Sí, eliminar</button>
                  <button className="btn btn-quiet" onClick={() => setConfirm("")}>No</button>
                </span>
              ) : (
                <button className="btn btn-quiet btn-icon" aria-label={`Eliminar reserva: ${resumeReservation(r)}`} title="Eliminar reserva" onClick={() => setConfirm(r.reservationId)}><Icon name="trash" size={17} /></button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
