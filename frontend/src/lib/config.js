const raw = (typeof window !== "undefined" && window.TRIPPLANNER_CONFIG) || {};
const trim = (u) => (u || "").replace(/\/+$/, "");

export const config = {
  apiUrl: trim(raw.API_URL),
  streamUrl: (raw.STREAM_URL || "").trim(),
};
export const isConfigured = () => Boolean(config.apiUrl && config.streamUrl);
