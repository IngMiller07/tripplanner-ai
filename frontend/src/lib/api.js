// Canal 1 (operaciones de negocio): Frontend -> API Gateway -> Lambda -> DynamoDB
import { config } from "./config.js";

const enc = encodeURIComponent;

async function request(method, path, body) {
  if (!config.apiUrl) throw new Error("Falta API_URL en config.js");
  let res;
  try {
    res = await fetch(config.apiUrl + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("No se pudo conectar con el servidor. Revisa tu conexion.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.message || `Error ${res.status}`);
  return data;
}

export const api = {
  trips: (userId) => request("GET", `/trips?userId=${enc(userId)}`).then((d) => d.trips || []),
  createTrip: (trip) => request("POST", "/trips", trip),
  updateTrip: (tripId, patch) => request("PUT", `/trips/${enc(tripId)}`, patch),

  itineraries: (tripId) => request("GET", `/itineraries?tripId=${enc(tripId)}`).then((d) => d.itineraries || []),
  createItinerary: (tripId, days) => request("POST", "/itineraries", { tripId, days }),
  updateItinerary: (tripId, itineraryId, days) =>
    request("PUT", `/itineraries/${enc(tripId)}/${enc(itineraryId)}`, { days }),

  reservations: (tripId) => request("GET", `/reservations?tripId=${enc(tripId)}`).then((d) => d.reservations || []),
  createReservation: (tripId, type, details) => request("POST", "/reservations", { tripId, type, details }),
  updateReservation: (tripId, reservationId, status) =>
    request("PUT", `/reservations/${enc(tripId)}/${enc(reservationId)}`, { status }),
  deleteReservation: (tripId, reservationId) => request("DELETE", `/reservations/${enc(tripId)}/${enc(reservationId)}`),
};
