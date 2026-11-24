# app/WeatherAgent/weather_core.py
#
# Logica pura del clima, SIN dependencias de MCP. Datos REALES de Open-Meteo (gratis, sin
# API key): pronostico si la fecha esta dentro de ~16 dias, o el historico de esas mismas
# fechas el ano anterior. La usa el servidor MCP (weather_mcp_server.py) y, como respaldo,
# el Weather Agent si el subproceso MCP no puede arrancar.

import os
import json
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

FORECAST_URL = os.environ.get("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
ARCHIVE_URL = os.environ.get("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive")

# Coordenadas locales: evita depender del geocoder para las ciudades del demo.
CITIES = {
    "bogota": (4.711, -74.072), "cartagena": (10.391, -75.479),
    "medellin": (6.244, -75.581), "cali": (3.452, -76.532),
    "barranquilla": (10.964, -74.796), "santa marta": (11.241, -74.199),
    "san andres": (12.584, -81.700), "pereira": (4.813, -75.696),
    "bucaramanga": (7.119, -73.123), "cucuta": (7.894, -72.504),
    "leticia": (-4.215, -69.941), "manizales": (5.070, -75.517),
    "armenia": (4.534, -75.681), "villavicencio": (4.142, -73.627),
    "monteria": (8.748, -75.881), "riohacha": (11.544, -72.907),
    "madrid": (40.417, -3.704), "miami": (25.762, -80.192),
    "ciudad de mexico": (19.433, -99.133), "cancun": (21.161, -86.851),
    "lima": (-12.046, -77.043), "buenos aires": (-34.604, -58.382),
    "nueva york": (40.713, -74.006), "new york": (40.713, -74.006),
}

WMO = {
    0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado", 3: "Nublado",
    45: "Niebla", 48: "Niebla", 51: "Llovizna ligera", 53: "Llovizna", 55: "Llovizna intensa",
    61: "Lluvia ligera", 63: "Lluvia", 65: "Lluvia fuerte", 66: "Lluvia helada", 67: "Lluvia helada",
    71: "Nieve ligera", 73: "Nieve", 75: "Nieve fuerte", 80: "Lluvia ligera por ratos",
    81: "Lluvia por ratos", 82: "Lluvia fuerte por ratos", 95: "Tormenta eléctrica", 96: "Tormenta con granizo",
    99: "Tormenta fuerte con granizo",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower().strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.split(",")[0].strip()


def _parse_date(s: str) -> date:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Fecha invalida: {s!r} (usa YYYY-MM-DD o dd/mm/yyyy)")


def _http_json(url: str, timeout: int = 12) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _coords(city: str):
    key = _norm(city)
    if key in CITIES:
        return CITIES[key]
    q = urllib.parse.urlencode({"name": city.split(",")[0], "count": 1, "language": "es", "format": "json"})
    res = (_http_json("https://geocoding-api.open-meteo.com/v1/search?" + q).get("results") or [])
    if not res:
        raise ValueError(f"No encontre la ciudad: {city}")
    return res[0]["latitude"], res[0]["longitude"]


def _rows(daily: dict, source: str) -> list:
    out = []
    for i, d in enumerate(daily.get("time", [])):
        def g(k):
            v = daily.get(k) or []
            return v[i] if i < len(v) else None
        code = g("weather_code")
        out.append({
            "date": d,
            "temp_max_c": g("temperature_2m_max"),
            "temp_min_c": g("temperature_2m_min"),
            "rain_probability_pct": g("precipitation_probability_max"),
            "rain_mm": g("precipitation_sum"),
            "condition": WMO.get(code, "Variable") if code is not None else "Variable",
            "source": source,
        })
    return out


def _forecast_uncached(city: str, start_date: str, end_date: str = "") -> dict:
    """
    Clima REAL de una ciudad entre dos fechas (pronostico o, si la fecha esta
    lejos, el historico de esas mismas fechas el ano anterior).

    Args:
        city: Ciudad (ej. "Cartagena", "Bogota").
        start_date: Fecha inicial, YYYY-MM-DD o dd/mm/yyyy.
        end_date: Fecha final (opcional; si se omite, un solo dia).
    """
    try:
        d0 = _parse_date(start_date)
        d1 = _parse_date(end_date) if end_date else d0
        if d1 < d0:
            d0, d1 = d1, d0
        d1 = min(d1, d0 + timedelta(days=15))
        lat, lon = _coords(city)
    except Exception as exc:
        return {"city": city, "error": str(exc)}

    base = {"latitude": lat, "longitude": lon, "timezone": "auto"}
    try:
        q = urllib.parse.urlencode({
            **base, "start_date": d0.isoformat(), "end_date": d1.isoformat(),
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
        })
        data = _http_json(FORECAST_URL + "?" + q)
        if data.get("daily"):
            return {"city": city, "source": "pronostico", "days": _rows(data["daily"], "pronostico")}
    except Exception:
        pass  # fuera del rango de pronostico (>16 dias): usamos historico

    try:
        h0, h1 = d0.replace(year=d0.year - 1), d1.replace(year=d1.year - 1)
        q = urllib.parse.urlencode({
            **base, "start_date": h0.isoformat(), "end_date": h1.isoformat(),
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
        })
        data = _http_json(ARCHIVE_URL + "?" + q)
        days = _rows(data["daily"], "historico_ano_anterior")
        for row, real in zip(days, [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]):
            row["date"] = real.isoformat()
        return {"city": city, "source": "historico_ano_anterior", "days": days}
    except Exception as exc:
        return {"city": city, "error": f"No se pudo consultar el servicio meteorologico: {exc}"}


_CACHE: dict = {}
_CACHE_TTL = 15 * 60          # segundos: la API gratuita limita las peticiones por IP; repetir una consulta no la vuelve a llamar


def forecast(city: str, start_date: str, end_date: str = "") -> dict:
    """Igual que _forecast_uncached pero recuerda 15 minutos las consultas que salieron bien."""
    import time
    key = (_norm(city), str(start_date).strip(), str(end_date).strip())
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return json.loads(json.dumps(hit[1]))
    result = _forecast_uncached(city, start_date, end_date)
    if isinstance(result, dict) and "error" not in result:
        _CACHE[key] = (time.time(), result)
        if len(_CACHE) > 200:
            _CACHE.pop(next(iter(_CACHE)))
    return json.loads(json.dumps(result))

