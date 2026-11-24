import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api.js";

// Reservas de TODOS los viajes del usuario (una consulta por viaje al Canal 1).
export function useReservations(trips) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const key = trips.map((t) => t.tripId).join(",");

  const reload = useCallback(async () => {
    if (!trips.length) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      const lists = await Promise.all(
        trips.map((t) => api.reservations(t.tripId).then((rs) => rs.map((r) => ({ ...r, trip: t }))))
      );
      const all = lists.flat().sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
      setItems(all);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { items, loading, error, reload };
}
