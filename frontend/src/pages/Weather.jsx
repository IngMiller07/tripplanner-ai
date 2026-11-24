import React, { useState } from "react";
import { useApp } from "../AppState.jsx";
import { useChat } from "../hooks/useChat.js";
import { PageHead, Spinner, ErrorNote } from "../components/Ui.jsx";
import { WeatherStrip } from "../components/ToolResults.jsx";
import { addDaysISO, CITIES, toDMY } from "../lib/format.js";

export default function Weather() {
  const { userId } = useApp();
  const chat = useChat(userId, { storageKey: null });
  const [form, setForm] = useState({ city: "Cartagena", from: addDaysISO(10), to: addDaysISO(13) });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const last = [...chat.messages].reverse().find((m) => m.role === "assistant");
  const part = last?.parts.find((p) => p.type === "tool" && p.name === "ask_weather");
  const data = part?.data;
  const failure = last?.parts.find((p) => p.type === "error")?.text || (data && data.error);

  const search = (e) => {
    e.preventDefault();
    chat.send(`Dame el clima de ${form.city} del ${toDMY(form.from)} al ${toDMY(form.to)}. Usa solo ask_weather y no guardes nada.`, { withHistory: false });
  };

  return (
    <div className="page">
      <PageHead title="Clima del destino" />
      <form className="form-row" onSubmit={search}>
        <label>Ciudad<input list="wcities" value={form.city} onChange={set("city")} required /></label>
        <label>Desde<input type="date" value={form.from} onChange={set("from")} required /></label>
        <label>Hasta<input type="date" value={form.to} min={form.from} onChange={set("to")} required /></label>
        <button className="btn btn-primary" disabled={chat.busy}>{chat.busy ? "Consultando…" : "Ver clima"}</button>
        <datalist id="wcities">{CITIES.map((c) => <option key={c} value={c} />)}</datalist>
      </form>
      {chat.busy && <p className="progress"><Spinner /> Consultando el pronóstico…</p>}
      <ErrorNote>{failure}</ErrorNote>
      {data && data.days && <WeatherStrip data={data} />}
      {!chat.busy && !data && !failure && <p className="muted">El pronóstico llega hasta unos 16 días; para fechas más lejanas muestro cómo estuvo el clima esos mismos días el año pasado.</p>}
    </div>
  );
}
