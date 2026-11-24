const nfCop = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
const nfUsd = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export const cop = (n) => nfCop.format(Number(n) || 0);
export const usd = (n) => (n == null || n === "" || isNaN(Number(n)) ? "-" : `US$ ${nfUsd.format(Number(n))}`);

// Acepta dd/mm/yyyy o yyyy-mm-dd y devuelve una fecha LOCAL (sin corrimiento de zona horaria).
export function parseDate(s) {
  if (!s || typeof s !== "string") return null;
  let m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(s.trim());
  if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
  m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s.trim());
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  const d = new Date(s);
  return isNaN(d) ? null : d;
}
export function fmtDate(s, opts = { day: "numeric", month: "short" }) {
  const d = parseDate(s);
  return d ? d.toLocaleDateString("es-CO", opts) : s || "";
}
export const toDMY = (iso) => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
};
export const toISO = (dmy) => {
  const d = parseDate(dmy);
  if (!d) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};
export const daysUntil = (s) => {
  const d = parseDate(s);
  if (!d) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((d - today) / 86400000);
};
export const addDaysISO = (n) => {
  const d = new Date();
  d.setDate(d.getDate() + n);
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};
export const slugUser = (name) =>
  name.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);

// "miller-andres" -> "Miller Andres" (solo para mostrar; el identificador real sigue siendo el slug)
export const prettyUser = (id) => String(id || "").split("-").filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

export const uid = () =>
  (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}-xxxxxxxxxxxx`);

export const STATUS_LABEL = { SIMULADA: "Simulada", PENDIENTE: "Pendiente", CONFIRMADA: "Confirmada" };
export const TRIP_STATUS = ["PLANEANDO", "RESERVADO", "COMPLETADO", "CANCELADO"];
export const CITIES = ["Bogota", "Medellin", "Cali", "Cartagena", "Barranquilla", "Santa Marta", "San Andres", "Pereira", "Bucaramanga", "Cucuta", "Madrid", "Miami", "Cancun", "Lima"];
