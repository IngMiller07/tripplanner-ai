// Servidor de PRUEBA local (no se despliega): imita el Canal 1 (REST) y el Canal 2 (SSE).
// Uso:  npm run mock   y en public/config.js poner API_URL/STREAM_URL = http://localhost:8787
import http from "node:http";
import crypto from "node:crypto";

const db = { trips: [], itins: [], res: [] };
const PORT = process.env.PORT || 8787;
const send = (res, code, body) => {
  res.writeHead(code, { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS", "Access-Control-Allow-Headers": "content-type" });
  res.end(JSON.stringify(body));
};
const body = (req) => new Promise((r) => { let d = ""; req.on("data", (c) => (d += c)); req.on("end", () => r(d ? JSON.parse(d) : {})); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString();

async function stream(req, res) {
  const b = await body(req);
  res.writeHead(200, { "Content-Type": "text/event-stream", "Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache" });
  const ev = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
  const say = async (t) => { for (const w of t.split(/(?<= )/)) { ev({ type: "text", data: w }); await sleep(12); } };
  const p = (b.prompt || "").toLowerCase();
  ev({ type: "start" });
  const delReq = b.action?.type === "delete_reservation" || /elimin|borr|cancel/.test(p) && p.includes("reserv");
  if (delReq) {
    // Mismos eventos que el Orchestrator real (planner.run_delete): buscar -> (preguntar | eliminar)
    const label = (r) => `Vuelo ${r.details?.airline || ""} ${r.details?.flight || ""}${r.details?.depart ? ` (${r.details.depart})` : ""}`.replace("  ", " ");
    const rows = [...db.res].sort((a, c) => String(a.createdAt).localeCompare(String(c.createdAt)));
    const P = "plan-del";
    ev({ type: "plan", id: P, title: "Eliminar reservas", steps: [
      { id: "find", label: "Buscar tus reservas", agent: "Tu cuenta", tech: "Tool get_reservations · Lambda → DynamoDB" },
      { id: "delete", label: "Eliminar la reserva", agent: "Tu cuenta", tech: "Tool delete_reservation · Lambda → DynamoDB" }] });
    ev({ type: "step", plan: P, id: "find", status: "running", detail: "Consultando tus reservas…" });
    ev({ type: "tool_start", name: "get_reservations", id: "g1", step: "find" }); await sleep(400);
    ev({ type: "tool_result", name: "get_reservations", id: "g1", status: "success", data: { reservations: rows }, step: "find" });
    ev({ type: "step", plan: P, id: "find", status: "done", detail: `${rows.length} reservas`, ms: 410 });
    let pick = null; let all = false;
    if (b.action?.all || /todas/.test(p)) all = true;
    else if (b.action?.reservationId) pick = rows.find((r) => r.reservationId === b.action.reservationId);
    else if (/ultim|hice|acabo/.test(p)) pick = rows.at(-1);
    else if (rows.length === 1) pick = rows[0];
    if (!rows.length) {
      ev({ type: "step", plan: P, id: "delete", status: "skipped", detail: "No hay reservas que eliminar" }); ev({ type: "plan_done", plan: P, ok: true, ms: 450 });
      await say("No tienes reservas guardadas para eliminar.");
    } else if (!pick && !all) {
      ev({ type: "step", plan: P, id: "delete", status: "skipped", detail: "Necesito que elijas cuál" }); ev({ type: "plan_done", plan: P, ok: true, ms: 450 });
      await say(`Tienes ${rows.length} reservas y no sé cuál quieres eliminar:\n\n${rows.map((r, i) => `${i + 1}. ${label(r)} · Simulada`).join("\n")}\n\n¿Cuál elimino? Toca un botón o escribe el número, «la última» o «todas».`);
      ev({ type: "suggestions", items: [...rows.slice(0, 4).map((r) => ({ label: `Eliminar ${label(r)}`, icon: "trash", action: { type: "delete_reservation", tripId: r.tripId, reservationId: r.reservationId } })),
        { label: "Eliminar todas", icon: "trash", action: { type: "delete_reservation", tripId: rows[0].tripId, all: true } }] });
    } else {
      const targets = all ? rows : [pick];
      ev({ type: "step", plan: P, id: "delete", status: "running", detail: `Eliminando ${targets.length} reserva${targets.length > 1 ? "s" : ""}…` });
      for (const r of targets) {
        ev({ type: "tool_start", name: "delete_reservation", id: "d" + r.reservationId, step: "delete" }); await sleep(350);
        db.res.splice(db.res.findIndex((x) => x.reservationId === r.reservationId), 1);
        ev({ type: "tool_result", name: "delete_reservation", id: "d" + r.reservationId, status: "success", data: { deleted: true, tripId: r.tripId, reservationId: r.reservationId }, step: "delete" });
      }
      ev({ type: "step", plan: P, id: "delete", status: "done", detail: targets.length > 1 ? `${targets.length} reservas eliminadas` : "Reserva eliminada", ms: 380 * targets.length });
      ev({ type: "plan_done", plan: P, ok: true, ms: 1200 });
      const left = db.res.length;
      await say(`Listo, eliminé ${all ? `las ${targets.length} reservas` : `la reserva **${label(pick)}**`}.${left ? ` Te quedan ${left} reserva${left > 1 ? "s" : ""}.` : " Ya no tienes reservas guardadas."}`);
    }
  } else if (b.action?.type === "reserve_flight") {
    // Mismos eventos que el Orchestrator real (planner.run_reserve): linea de tiempo corta + tarjeta + texto
    const f = b.action.flight || {}; const tid = b.context?.activeTrip?.tripId || db.trips.at(-1)?.tripId;
    ev({ type: "plan", id: "plan-res", title: "Reservar vuelo", steps: [
      { id: "pick", label: "Confirmar el vuelo elegido", agent: "Asistente", tech: "Orchestrator · datos del Flight Agent" },
      { id: "reserve", label: "Registrar la reserva simulada", agent: "Tu cuenta", tech: "Tool create_reservation · Lambda → DynamoDB" }] });
    ev({ type: "step", plan: "plan-res", id: "pick", status: "running" }); await sleep(250);
    ev({ type: "step", plan: "plan-res", id: "pick", status: "done", detail: `${f.airline} ${f.flight} · sale ${f.depart} · US$${f.price_usd}`, ms: 5 });
    ev({ type: "step", plan: "plan-res", id: "reserve", status: "running", detail: "Guardando…" });
    ev({ type: "tool_start", name: "create_reservation", id: "r1", step: "reserve" }); await sleep(600);
    const r = { tripId: tid, reservationId: crypto.randomUUID(), type: "VUELO", details: { ...f, travelers: 3 }, status: "SIMULADA", createdAt: now(), updatedAt: now() };
    db.res.push(r);
    ev({ type: "tool_result", name: "create_reservation", id: "r1", status: "success", data: r, step: "reserve" });
    ev({ type: "step", plan: "plan-res", id: "reserve", status: "done", detail: "Reserva guardada con estado Simulada", ms: 612 });
    ev({ type: "plan_done", plan: "plan-res", ok: true, ms: 870 });
    await say(`Listo, reservé de forma **simulada** el vuelo ${f.airline} ${f.flight}. No se cobró nada: la ves en **Reservas** con estado *Simulada*.`);
    ev({ type: "suggestions", items: [{ label: "Ver mis reservas", icon: "ticket", goto: "reservations" }] });
  } else if (/\bbrasil\b/.test(p) && !p.includes("janeiro") && !p.includes("paulo")) {
    // Igual que el Orchestrator real: un pais no es un destino; se pide una ciudad y NO se abre linea de tiempo
    await say("Para armarlo me falta: la ciudad de destino en Brasil (por ejemplo, Río de Janeiro o São Paulo). Cuéntame a cuál quieres ir y lo armo.");
  } else if ((p.includes("cartagena") || p.includes("santa marta")) && p.includes("vuelos") && !p.includes("ask_flights")) {
    // Mismos eventos que el Orchestrator real (planner.run_plan). Con "santa marta" el clima falla para ver el estado de error.
    const city = p.includes("santa marta") ? "Santa Marta" : "Cartagena"; const weatherFails = city === "Santa Marta";
    const P = "plan-1";
    ev({ type: "plan", id: P, title: "Armando tu viaje", steps: [
      { id: "understand", label: "Entender tu solicitud", agent: "Asistente", tech: "Orchestrator · reglas + Nova Pro" },
      { id: "weather", label: "Consultar el clima", agent: "Agente de clima", tech: "Weather Agent · A2A · MCP Open-Meteo" },
      { id: "flights", label: "Buscar vuelos", agent: "Agente de vuelos", tech: "Flight Agent · A2A · MCP Kiwi" },
      { id: "budget", label: "Calcular el presupuesto", agent: "Asistente", tech: "Orchestrator · cálculo determinista" },
      { id: "itinerary", label: "Diseñar el itinerario", agent: "Asistente", tech: "Orchestrator · Nova Pro + catálogo de lugares" },
      { id: "save_trip", label: "Guardar el viaje", agent: "Tu cuenta", tech: "Tool create_trip · Lambda → DynamoDB" },
      { id: "save_itinerary", label: "Guardar el itinerario", agent: "Tu cuenta", tech: "Tool create_itinerary · Lambda → DynamoDB" }] });
    ev({ type: "step", plan: P, id: "understand", status: "running", detail: "Leyendo tu mensaje…" }); await sleep(500);
    ev({ type: "step", plan: P, id: "understand", status: "done", title: `Bogotá → ${city}`, ms: 4, tech: "Orchestrator · reglas",
      detail: `Bogotá → ${city} · 4 días · 3 viajeros · $5.000.000 · 04/12/2026 al 07/12/2026`, assumptions: ["Asumí la salida el 04/12/2026"] });
    ev({ type: "step", plan: P, id: "weather", status: "running", label: `Clima en ${city}`, detail: "Consultando el pronóstico…" });
    ev({ type: "tool_start", name: "ask_weather", id: "w1", step: "weather" });
    ev({ type: "step", plan: P, id: "flights", status: "running", label: `Vuelos Bogotá → ${city}`, detail: "Buscando vuelos reales…" });
    ev({ type: "tool_start", name: "ask_flights", id: "f1", step: "flights" }); await sleep(1800);
    if (weatherFails) {
      ev({ type: "tool_result", name: "ask_weather", id: "w1", status: "error", data: { error: "El servicio de clima está saturado en este momento." }, step: "weather" });
      ev({ type: "step", plan: P, id: "weather", status: "error", detail: "El servicio de clima está saturado en este momento.", ms: 2431, attempt: 2, retry: `Consulta de nuevo el clima de ${city} del 04/12/2026 al 07/12/2026.` });
    } else {
      ev({ type: "tool_result", name: "ask_weather", id: "w1", status: "success", step: "weather", data: { city, source: "pronostico", days: [
        { date: "2026-12-04", temp_min_c: 26, temp_max_c: 32, rain_probability_pct: 15, condition: "Mayormente despejado" },
        { date: "2026-12-05", temp_min_c: 25, temp_max_c: 31, rain_probability_pct: 65, condition: "Lluvia por ratos" },
        { date: "2026-12-06", temp_min_c: 26, temp_max_c: 33, rain_probability_pct: 10, condition: "Despejado" },
        { date: "2026-12-07", temp_min_c: 26, temp_max_c: 32, rain_probability_pct: 30, condition: "Parcialmente nublado" }] } });
      ev({ type: "step", plan: P, id: "weather", status: "done", detail: "25°–33°C · despejado · lluvia hasta 65%", ms: 1873, attempt: 1 });
    }
    await sleep(900);
    ev({ type: "tool_result", name: "ask_flights", id: "f1", status: "success", step: "flights", data: { origin: "Bogotá", destination: city, date: "04/12/2026", currency: "USD", flights: [
      { airline: "LATAM", flight: "LA4126", depart: "12:00", arrive: "13:30", price_usd: 77, stops: 0 },
      { airline: "Avianca", flight: "AV9362", depart: "06:15", arrive: "07:50", price_usd: 97, stops: 0 },
      { airline: "Viva Air", flight: "VV1520", depart: "14:05", arrive: "17:30", price_usd: 120, stops: 1 }] } });
    ev({ type: "step", plan: P, id: "flights", status: "done", detail: "3 opciones · la más barata US$77 (LATAM)", ms: 2790, attempt: 1 });
    ev({ type: "step", plan: P, id: "budget", status: "running", detail: "Sumando vuelos, hospedaje y comidas…" });
    ev({ type: "tool_start", name: "calculate_budget", id: "b1", step: "budget" }); await sleep(300);
    ev({ type: "tool_result", name: "calculate_budget", id: "b1", status: "success", step: "budget", data: { budgetCOP: 5000000, fxCOPperUSD: 4000, travelers: 3, days: 4, nights: 3, breakdown: [
      { item: "Vuelos", costCOP: 1848000 }, { item: "Hospedaje (estimado)", costCOP: 1350000 }, { item: "Alimentacion y actividades (estimado)", costCOP: 1440000 }], totalEstimatedCOP: 4638000, remainingCOP: 362000, withinBudget: true, perPersonCOP: 1546000 } });
    ev({ type: "step", plan: P, id: "budget", status: "done", detail: "Total estimado $4.638.000 · sobran $362.000", ms: 3 });
    ev({ type: "step", plan: P, id: "itinerary", status: "running", detail: "Repartiendo lugares reales por día…" });
    ev({ type: "tool_start", name: "plan_itinerary", id: "i0", step: "itinerary" }); await sleep(1200);
    const days = [
      { day: 1, title: "Llegada y atardecer", activities: ["Vuelo Bogotá → Cartagena (LATAM LA4126, sale 12:00)", "Check-in en el hotel y descanso", "Tarde: Ciudad Amurallada y Centro Histórico", "Noche: Getsemaní y Plaza de la Trinidad"] },
      { day: 2, title: "Fortalezas e historia", activities: ["Mañana: Castillo San Felipe de Barajas", "Almuerzo de comida típica de la región", "Tarde: Museo Naval del Caribe", "Noche: Plaza de Santo Domingo"] },
      { day: 3, title: "Islas del Rosario", activities: ["Mañana: Islas del Rosario", "Almuerzo de comida típica de la región", "Tarde: Playa Blanca en Barú", "Noche: Bocagrande"] },
      { day: 4, title: "Despedida y regreso", activities: ["Mañana: Torre del Reloj", "Almuerzo y compras de recuerdos", "Check-out y regreso a Bogotá"] }];
    ev({ type: "tool_result", name: "plan_itinerary", id: "i0", status: "success", step: "itinerary", data: { days, source: "lugares reales del destino" } });
    ev({ type: "step", plan: P, id: "itinerary", status: "done", detail: "4 días · 15 actividades · lugares reales del destino", ms: 1204 });
    const trip = { tripId: crypto.randomUUID(), userId: b.userId, origin: "Bogotá", destination: city, startDate: "04/12/2026", endDate: "07/12/2026", travelers: 3, budget: 5000000, status: "PLANEANDO", createdAt: now(), updatedAt: now() };
    db.trips.push(trip);
    ev({ type: "step", plan: P, id: "save_trip", status: "running", detail: "Guardando…" });
    ev({ type: "tool_start", name: "create_trip", id: "t1", step: "save_trip" }); await sleep(400);
    ev({ type: "tool_result", name: "create_trip", id: "t1", status: "success", step: "save_trip", data: trip });
    ev({ type: "step", plan: P, id: "save_trip", status: "done", detail: "Viaje guardado en tu cuenta", ms: 402 });
    const it = { tripId: trip.tripId, itineraryId: crypto.randomUUID(), days, createdAt: now(), updatedAt: now() };
    db.itins.push(it);
    ev({ type: "step", plan: P, id: "save_itinerary", status: "running", detail: "Guardando…" });
    ev({ type: "tool_start", name: "create_itinerary", id: "i1", step: "save_itinerary" }); await sleep(350);
    ev({ type: "tool_result", name: "create_itinerary", id: "i1", status: "success", step: "save_itinerary", data: it });
    ev({ type: "step", plan: P, id: "save_itinerary", status: "done", detail: "Itinerario guardado", ms: 351 });
    ev({ type: "plan_done", plan: P, ok: !weatherFails, ms: 9400 });
    await say(weatherFails
      ? `Armé tu viaje a ${city}, pero **el clima no estuvo disponible** (puedes reintentarlo con el botón). El vuelo más barato es LATAM LA4126 por US$77 y el viaje cabe en tu presupuesto: sobran $362.000. Asumí la salida el 04/12/2026.\n\n¿Reservo el vuelo más barato? Es una reserva simulada.`
      : `Tu plan a ${city} está listo. Clima cálido con lluvia el sábado (65%), así que puse museos ese día. El vuelo más barato es **LATAM LA4126 por US$77** y el viaje cabe en tu presupuesto: sobran **$362.000**. Asumí la salida el 04/12/2026.\n\n¿Reservo el vuelo más barato? Es una reserva simulada.`);
    ev({ type: "suggestions", items: [
      { label: "Reservar el vuelo más barato", icon: "ticket", action: { type: "reserve_flight", tripId: trip.tripId, flight: { airline: "LATAM", flight: "LA4126", depart: "12:00", arrive: "13:30", price_usd: 77, stops: 0 } } },
      { label: "Ver mis viajes", icon: "suitcase", goto: "trips" }] });
  } else if (p.includes("reserva el vuelo")) {
    const tid = (b.context?.activeTrip?.tripId) || db.trips.at(-1)?.tripId;
    const r = { tripId: tid, reservationId: crypto.randomUUID(), type: "VUELO", details: { airline: "Avianca", flight: "AV9362", depart: "06:15", price_usd: 58 }, status: "SIMULADA", createdAt: now(), updatedAt: now() };
    db.res.push(r);
    ev({ type: "tool_start", name: "create_reservation", id: "r1" }); await sleep(400);
    ev({ type: "tool_result", name: "create_reservation", id: "r1", status: "success", data: r });
    await say("Listo, registré la reserva **simulada** del vuelo Avianca AV9362 para tu viaje a Cartagena.");
  } else if (p.includes("ask_flights")) {
    ev({ type: "tool_start", name: "ask_flights", id: "f9" }); await sleep(600);
    ev({ type: "tool_result", name: "ask_flights", id: "f9", status: "success", data: { origin: "BOG", destination: "CTG", date: "04/12/2026", flights: [{ airline: "Avianca", flight: "AV9362", depart: "06:15", arrive: "07:50", price_usd: 58, stops: 0 }, { airline: "LATAM", flight: "LA4040", depart: "09:40", arrive: "11:15", price_usd: 64, stops: 0 }] } });
    await say("Estos son los vuelos.");
  } else if (p.includes("ask_weather")) {
    ev({ type: "tool_start", name: "ask_weather", id: "w9" }); await sleep(500);
    ev({ type: "tool_result", name: "ask_weather", id: "w9", status: "success", data: { city: "Cartagena", source: "historico_ano_anterior", days: [{ date: "2027-03-10", temp_min_c: 26, temp_max_c: 33, rain_mm: 0, condition: "Despejado" }, { date: "2027-03-11", temp_min_c: 25, temp_max_c: 32, rain_mm: 4.2, condition: "Lluvia ligera" }] } });
    await say("Clima listo.");
  } else {
    await say("Cuéntame a dónde quieres ir, con cuántas personas y qué presupuesto tienes.");
  }
  ev({ type: "done" });
  res.end();
}

http.createServer(async (req, res) => {
  const u = new URL(req.url, `http://${req.headers.host}`);
  if (req.method === "OPTIONS") return send(res, 204, {});
  if (u.pathname === "/stream" && req.method === "POST") return stream(req, res);
  const seg = u.pathname.split("/").filter(Boolean);
  const q = u.searchParams;
  if (seg[0] === "trips") {
    if (req.method === "GET") return send(res, 200, { trips: db.trips.filter((t) => t.userId === q.get("userId")) });
    if (req.method === "POST") { const b = await body(req); const t = { tripId: crypto.randomUUID(), status: "PLANEANDO", createdAt: now(), updatedAt: now(), ...b }; db.trips.push(t); return send(res, 201, t); }
    if (req.method === "PUT") { const b = await body(req); const t = db.trips.find((x) => x.tripId === seg[1]); Object.assign(t, b, { updatedAt: now() }); return send(res, 200, t); }
  }
  if (seg[0] === "itineraries") {
    if (req.method === "GET") return send(res, 200, { itineraries: db.itins.filter((i) => i.tripId === q.get("tripId")) });
    if (req.method === "PUT") { const b = await body(req); const i = db.itins.find((x) => x.itineraryId === seg[2]); i.days = b.days; i.updatedAt = now(); return send(res, 200, i); }
  }
  if (seg[0] === "reservations") {
    if (req.method === "GET") return send(res, 200, { reservations: db.res.filter((r) => r.tripId === q.get("tripId")) });
    if (req.method === "POST") { const b = await body(req); const r = { reservationId: crypto.randomUUID(), status: "SIMULADA", createdAt: now(), updatedAt: now(), ...b }; db.res.push(r); return send(res, 201, r); }
    if (req.method === "PUT") { const b = await body(req); const r = db.res.find((x) => x.reservationId === seg[2]); r.status = b.status; return send(res, 200, r); }
    if (req.method === "DELETE") { const i = db.res.findIndex((x) => x.reservationId === seg[2]); if (i < 0) return send(res, 404, { error: "La reserva no existe o ya fue eliminada" }); db.res.splice(i, 1); return send(res, 200, { deleted: true, tripId: seg[1], reservationId: seg[2] }); }
  }
  send(res, 404, { error: "no existe" });
}).listen(PORT, () => console.log(`mock en http://localhost:${PORT}`));
