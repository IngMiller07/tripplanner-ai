import React, { useEffect, useState } from "react";
import { useApp, readKnownUsers, forgetKnownUser } from "./AppState.jsx";
import { api } from "./lib/api.js";
import Icon from "./components/Icon.jsx";
import { Toasts } from "./components/Ui.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Assistant from "./pages/Assistant.jsx";
import Flights from "./pages/Flights.jsx";
import Weather from "./pages/Weather.jsx";
import Trips from "./pages/Trips.jsx";
import Reservations from "./pages/Reservations.jsx";
import { slugUser, prettyUser } from "./lib/format.js";

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard" },
  { id: "assistant", label: "Asistente", icon: "chat" },
  { id: "flights", label: "Vuelos", icon: "plane" },
  { id: "weather", label: "Clima", icon: "sun" },
  { id: "trips", label: "Mis viajes", icon: "suitcase" },
  { id: "reservations", label: "Reservas", icon: "ticket" },
];
const PAGES = { dashboard: Dashboard, assistant: Assistant, flights: Flights, weather: Weather, trips: Trips, reservations: Reservations };

function Brand() {
  return (
    <div className="brand">
      <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="9" fill="var(--sea)" /><path d="M6.5 21c4-9.5 9.5-9.5 12.5-4.5s5.2 2.2 6.5-4" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" /><circle cx="25.5" cy="12.5" r="2.6" fill="var(--sol)" /></svg>
      <span>TripPlanner <em>AI</em></span>
    </div>
  );
}

const plural = (n) => `${n} ${n === 1 ? "viaje guardado" : "viajes guardados"}`;

// Entrar = escribir el nombre de tu cuenta. Los viajes viven en DynamoDB bajo ese nombre, así que al volver (desde cualquier
// navegador) aparecen igual. Se avisa si la cuenta ya existe o es nueva. No hay contraseña: es una demostración (ver docs).
function Welcome() {
  const { setUserId } = useApp();
  const [name, setName] = useState("");
  const [known, setKnown] = useState(readKnownUsers);
  const [counts, setCounts] = useState({});
  const [hint, setHint] = useState({ state: "idle" });
  const id = slugUser(name);

  useEffect(() => {                                   // cuántos viajes tiene cada cuenta conocida
    let live = true;
    known.forEach((u) => api.trips(u).then((t) => live && setCounts((c) => ({ ...c, [u]: t.length }))).catch(() => {}));
    return () => { live = false; };
  }, [known]);

  useEffect(() => {                                   // ¿la cuenta que escribes ya existe?
    if (id.length < 2) { setHint({ state: "idle" }); return undefined; }
    let live = true;
    setHint({ state: "checking" });
    const t = setTimeout(() => {
      api.trips(id).then((list) => live && setHint({ state: "ok", n: list.length })).catch(() => live && setHint({ state: "idle" }));
    }, 450);
    return () => { live = false; clearTimeout(t); };
  }, [id]);

  const submit = (e) => {
    e.preventDefault();
    if (id) setUserId(id);
  };
  const exists = hint.state === "ok" && hint.n > 0;
  return (
    <main className="welcome">
      <form onSubmit={submit}>
        <Brand />
        <h1>Tu agente de viajes, de la idea a la reserva</h1>
        <p>Entra con el nombre de tu cuenta. Tus viajes, itinerarios y reservas se guardan en ella y los encuentras al volver. No necesitas contraseña: es una demostración.</p>
        {known.length > 0 && (
          <div className="accounts" role="group" aria-label="Cuentas usadas en este equipo">
            <span className="accounts-title">Cuentas en este equipo</span>
            {known.map((u) => (
              <span key={u} className="account">
                <button type="button" className="account-go" onClick={() => setUserId(u)}>
                  <Icon name="user" size={16} />
                  <b>{prettyUser(u)}</b>
                  {counts[u] != null && <small>{counts[u] === 0 ? "sin viajes" : plural(counts[u])}</small>}
                </button>
                <button type="button" className="account-x" title="Quitar de esta lista (no borra tus viajes)" aria-label={`Quitar ${prettyUser(u)} de esta lista`}
                  onClick={() => { forgetKnownUser(u); setKnown(readKnownUsers()); }}>×</button>
              </span>
            ))}
          </div>
        )}
        <label>{known.length ? "O escribe otro nombre" : "Tu nombre"}<input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Miller" required /></label>
        <p className={`hint ${exists ? "hint-ok" : ""}`} aria-live="polite">
          {hint.state === "checking" && "Buscando tu cuenta…"}
          {hint.state === "ok" && (exists ? `Encontré ${plural(hint.n)} de «${prettyUser(id)}».` : `«${prettyUser(id)}» es una cuenta nueva: se crea al guardar tu primer viaje.`)}
        </p>
        <button className="btn btn-primary" disabled={!id}>{exists ? "Entrar a mi cuenta" : "Empezar"}</button>
      </form>
    </main>
  );
}

export default function App() {
  const { userId, setUserId, nav, goto } = useApp();
  if (!userId) return <Welcome />;
  const Page = PAGES[nav.page] || Dashboard;
  return (
    <div className="app">
      <aside className="side">
        <Brand />
        <nav aria-label="Principal">
          {NAV.map((n) => (
            <button key={n.id} className={`nav-btn ${nav.page === n.id ? "on" : ""}`} aria-current={nav.page === n.id ? "page" : undefined} onClick={() => goto(n.id)}>
              <Icon name={n.icon} size={20} />
              <span>{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="side-user">
          <Icon name="user" size={18} />
          <span title={`Cuenta: ${userId}`}>{prettyUser(userId)}</span>
          <button className="link" onClick={() => setUserId("")}>Cambiar</button>
        </div>
      </aside>
      <main className={`main ${nav.page === "assistant" ? "main-chat" : ""}`}>
        <Page key={nav.page + (nav.params?.tripId || "")} />
      </main>
      <Toasts />
    </div>
  );
}
