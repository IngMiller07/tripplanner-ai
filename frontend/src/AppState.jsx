import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./lib/api.js";

const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

const safeGet = (k) => {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
};
const safeSet = (k, v) => {
  try {
    localStorage.setItem(k, v);
  } catch {
    /* sin almacenamiento: la app sigue funcionando */
  }
};

// Cuentas que ya se usaron en este navegador (solo nombres, nunca datos): permiten volver a entrar con un clic.
const USERS_KEY = "tp.users";
export const readKnownUsers = () => {
  try {
    const v = JSON.parse(safeGet(USERS_KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string" && x).slice(0, 6) : [];
  } catch {
    return [];
  }
};
export const forgetKnownUser = (id) => safeSet(USERS_KEY, JSON.stringify(readKnownUsers().filter((u) => u !== id)));

export function AppProvider({ children }) {
  const [userId, setUserIdState] = useState(() => safeGet("tp.userId") || "");
  const [trips, setTrips] = useState([]);
  const [tripsLoading, setTripsLoading] = useState(false);
  const [tripsError, setTripsError] = useState("");
  const [toasts, setToasts] = useState([]);
  const [nav, setNav] = useState({ page: "dashboard", params: {} });

  const setUserId = useCallback((id) => {
    safeSet("tp.userId", id);
    if (id) safeSet(USERS_KEY, JSON.stringify([id, ...readKnownUsers().filter((u) => u !== id)].slice(0, 6)));
    setUserIdState(id);
    setTrips([]);
  }, []);

  const refreshTrips = useCallback(async () => {
    if (!userId) return [];
    setTripsLoading(true);
    try {
      const list = await api.trips(userId);
      list.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
      setTrips(list);
      setTripsError("");
      return list;
    } catch (e) {
      setTripsError(e.message);
      return [];
    } finally {
      setTripsLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    refreshTrips();
  }, [refreshTrips]);

  const toast = useCallback((text, tone = "ok") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const goto = useCallback((page, params = {}) => setNav({ page, params }), []);

  const value = useMemo(
    () => ({ userId, setUserId, trips, tripsLoading, tripsError, refreshTrips, toast, toasts, nav, goto }),
    [userId, setUserId, trips, tripsLoading, tripsError, refreshTrips, toast, toasts, nav, goto]
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
