# app/OrchestratorAgent/planner.py  (v3.1 - flujo "plan-and-execute" del Orchestrator)
#
# Por que existe este modulo
# --------------------------
# Planear un viaje es un flujo de pasos FIJOS (entender, clima, vuelos, presupuesto, itinerario, guardar).
# Dejar que un LLM pequeno decida ese orden en texto libre fallaba a cada rato (se detenia a mitad,
# inventaba datos, mezclaba ciudades). Aqui el CODIGO conduce el flujo y el LLM solo hace lo que
# hace bien (interpretar casos raros, repartir lugares por dia, redactar el resumen):
#
#   Orchestrator --A2A--> Weather Agent (MCP Open-Meteo)   \  en paralelo
#   Orchestrator --A2A--> Flight Agent  (MCP Kiwi)         /
#   Orchestrator: presupuesto + itinerario (Nova Pro con catalogo de lugares reales)
#   Orchestrator --Tool--> Lambda --> DynamoDB (create_trip, create_itinerary)
#
# Cada paso emite eventos de streaming (plan / step / tool_start / tool_result) que el frontend
# pinta como una LINEA DE TIEMPO en vivo. Las funciones de este archivo son puras o reciben sus
# dependencias (deps), asi se prueban sin AWS.
import asyncio
import json
import math
import re
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from typing import Optional


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def cop_fmt(n) -> str:
    return "$" + f"{int(round(n)):,}".replace(",", ".")


def dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


# --------------------------------------------------------------------------
# Ciudades y lugares reales
# --------------------------------------------------------------------------
CITIES = {  # nombre normalizado -> nombre para mostrar
    "bogota": "Bogotá", "medellin": "Medellín", "cali": "Cali", "cartagena": "Cartagena",
    "barranquilla": "Barranquilla", "santa marta": "Santa Marta", "san andres": "San Andrés",
    "pereira": "Pereira", "bucaramanga": "Bucaramanga", "cucuta": "Cúcuta", "leticia": "Leticia",
    "manizales": "Manizales", "armenia": "Armenia", "villavicencio": "Villavicencio",
    "monteria": "Montería", "riohacha": "Riohacha", "madrid": "Madrid", "miami": "Miami",
    "cancun": "Cancún", "lima": "Lima", "buenos aires": "Buenos Aires", "nueva york": "Nueva York",
    "ciudad de mexico": "Ciudad de México",
    "rio de janeiro": "Río de Janeiro", "sao paulo": "São Paulo", "quito": "Quito", "guayaquil": "Guayaquil",
    "montevideo": "Montevideo", "la habana": "La Habana", "punta cana": "Punta Cana", "paris": "París",
    "londres": "Londres", "roma": "Roma", "barcelona": "Barcelona", "lisboa": "Lisboa", "orlando": "Orlando",
    "los angeles": "Los Ángeles", "cusco": "Cusco", "toronto": "Toronto", "tokio": "Tokio",
}
ALIASES = {"cartagena de indias": "cartagena", "bogota dc": "bogota", "bogota d c": "bogota",
           "san andres isla": "san andres", "new york": "nueva york", "cdmx": "ciudad de mexico",
           "mexico df": "ciudad de mexico"}
_CITY_RX = re.compile(r"\b(" + "|".join(sorted(list(CITIES) + list(ALIASES), key=len, reverse=True)) + r")\b")


# Un pais no es un destino con clima, vuelos y lugares: se pide una ciudad concreta.
COUNTRIES = {
    "brasil": ("Brasil", "Río de Janeiro o São Paulo"), "brazil": ("Brasil", "Río de Janeiro o São Paulo"),
    "mexico": ("México", "Cancún o Ciudad de México"), "espana": ("España", "Madrid o Barcelona"),
    "argentina": ("Argentina", "Buenos Aires"), "peru": ("Perú", "Lima o Cusco"), "chile": ("Chile", "Santiago"),
    "ecuador": ("Ecuador", "Quito o Guayaquil"), "panama": ("Panamá", "Ciudad de Panamá"), "uruguay": ("Uruguay", "Montevideo"),
    "estados unidos": ("Estados Unidos", "Miami, Orlando o Nueva York"), "eeuu": ("Estados Unidos", "Miami, Orlando o Nueva York"),
    "usa": ("Estados Unidos", "Miami, Orlando o Nueva York"), "francia": ("Francia", "París"), "italia": ("Italia", "Roma"),
    "reino unido": ("Reino Unido", "Londres"), "inglaterra": ("Inglaterra", "Londres"), "portugal": ("Portugal", "Lisboa"),
    "cuba": ("Cuba", "La Habana"), "republica dominicana": ("República Dominicana", "Punta Cana"),
    "costa rica": ("Costa Rica", "San José"), "canada": ("Canadá", "Toronto"), "japon": ("Japón", "Tokio"),
    "alemania": ("Alemania", "Berlín"), "venezuela": ("Venezuela", "Caracas"), "bolivia": ("Bolivia", "La Paz"),
    "colombia": ("Colombia", "Cartagena, Medellín o Santa Marta"),
}


def country_of(place) -> Optional[tuple]:
    return COUNTRIES.get(norm(str(place or "")))


def canon_city(value) -> str:
    """'cartagena de indias' -> 'Cartagena'; una ciudad desconocida se devuelve limpia (sin departamento)."""
    raw = str(value or "").split(",")[0].strip()
    key = norm(raw)
    key = ALIASES.get(key, key)
    if key in CITIES:
        return CITIES[key]
    return " ".join(w.capitalize() for w in raw.split())[:40]


def _catalog_key(city: str) -> str:
    return norm(city)


# Lugares reales por destino: el itinerario se arma SOLO con estos (el modelo mezclaba sitios de otras ciudades).
PLACES = {
    "Cartagena": ["Ciudad Amurallada y Centro Histórico", "Castillo San Felipe de Barajas", "Getsemaní y Plaza de la Trinidad",
                  "Convento de la Popa", "Islas del Rosario", "Playa Blanca en Barú", "Plaza de Santo Domingo",
                  "Museo Naval del Caribe", "Palacio de la Inquisición", "Torre del Reloj", "Bocagrande"],
    "Santa Marta": ["Parque Nacional Tayrona (Cabo San Juan, Playa Cristal)", "Playa del Rodadero", "Quinta de San Pedro Alejandrino",
                    "Centro Histórico y Plaza de los Novios", "Museo del Oro Tairona", "Taganga", "Minca (cascadas y cafetales)",
                    "Playa Los Cocos", "Acuario y Museo del Mar", "Catedral de Santa Marta"],
    "Bogotá": ["Cerro de Monserrate", "Barrio La Candelaria", "Museo del Oro", "Museo Botero", "Plaza de Bolívar", "Usaquén",
               "Parque Simón Bolívar", "Jardín Botánico", "Catedral de Sal de Zipaquirá (excursión de un día)"],
    "Medellín": ["Comuna 13 (recorrido de grafiti)", "Parque Arví", "Plaza Botero", "Metrocable", "Jardín Botánico",
                 "Pueblito Paisa", "El Poblado y Parque Lleras", "Museo de Antioquia", "Parque Explora",
                 "Guatapé y Piedra del Peñol (excursión de un día)"],
    "Cali": ["Cristo Rey", "Zoológico de Cali", "Barrio San Antonio", "Boulevard del Río", "Iglesia La Ermita", "Río Pance",
             "Parque del Perro", "Museo del Oro Calima", "Una noche de salsa"],
    "Barranquilla": ["Gran Malecón del Río", "Catedral Metropolitana", "Museo del Caribe", "Bocas de Ceniza", "Barrio El Prado",
                     "Castillo de Salgar en Puerto Colombia", "Playa de Puerto Velero", "Museo de Arte Moderno"],
    "San Andrés": ["Johnny Cay", "Acuario", "Hoyo Soplador", "La Piscinita", "Cueva de Morgan", "Playa Spratt Bight",
                   "Mar de los Siete Colores", "West View", "Laguna Big Pond"],
    "Leticia": ["Río Amazonas", "Isla de los Micos", "Parque Nacional Natural Amacayacu", "Puerto Nariño",
                "Parque Santander y sus loros al atardecer", "Frontera con Tabatinga (Brasil)"],
    "Pereira": ["Termales de Santa Rosa de Cabal", "Bioparque Ukumarí", "Plaza de Bolívar (Bolívar Desnudo)",
                "Viaducto César Gaviria Trujillo", "Valle del Cocora en Salento (excursión de un día)"],
    # Destinos internacionales frecuentes (lugares reales y conocidos; el LLM solo elige entre ellos por indice)
    "Punta Cana": ["Playa Bávaro", "Playa Macao", "Isla Saona (excursión de un día)", "Cap Cana y Playa Juanillo",
                   "Scape Park (cuevas, cenotes y tirolesa)", "Parque Ojos Indígenas", "Altos de Chavón (excursión de un día)",
                   "Paseo en catamarán con snorkel", "Isla Catalina (excursión de un día)", "Puntacana Village y Playa Blanca"],
    "Río de Janeiro": ["Cristo Redentor y el Corcovado", "Pan de Azúcar (Pão de Açúcar)", "Playa de Copacabana",
                       "Playa de Ipanema y mirador Arpoador", "Escalera Selarón y barrio de Lapa", "Santa Teresa y su tranvía",
                       "Jardín Botánico de Río", "Estadio Maracanã", "Parque Nacional da Tijuca", "Museo del Mañana y Praça Mauá",
                       "Laguna Rodrigo de Freitas"],
    "São Paulo": ["Avenida Paulista", "MASP (Museo de Arte de São Paulo)", "Parque Ibirapuera", "Mercado Municipal (Mercadão)",
                  "Barrio Liberdade (barrio japonés)", "Beco do Batman en Vila Madalena", "Catedral da Sé", "Pinacoteca do Estado",
                  "Museo del Fútbol en Pacaembu", "Calle 25 de Março"],
    "Miami": ["South Beach y Ocean Drive", "Wynwood Walls", "Little Havana (Calle Ocho)", "Parque Nacional Everglades (excursión)",
              "Vizcaya Museum and Gardens", "Bayside Marketplace", "Key Biscayne", "Coral Gables y la Venetian Pool",
              "Lincoln Road", "Pérez Art Museum Miami"],
    "Cancún": ["Playa Delfines", "Isla Mujeres (excursión de un día)", "Chichén Itzá (excursión de un día)",
               "Tulum y sus ruinas frente al mar (excursión)", "Parque Xcaret (excursión de un día)", "Cenotes de la Riviera Maya",
               "Museo Maya de Cancún y zona arqueológica San Miguelito", "Playa del Carmen y la Quinta Avenida",
               "Parque de las Palapas", "Museo Subacuático de Arte (snorkel)"],
    "Madrid": ["Museo del Prado", "Palacio Real", "Parque del Retiro", "Plaza Mayor", "Puerta del Sol",
               "Museo Reina Sofía (el Guernica)", "Gran Vía", "Mercado de San Miguel", "Templo de Debod", "Barrio de Malasaña",
               "Toledo (excursión de un día)"],
    "Lima": ["Centro histórico y Plaza Mayor", "Miraflores y el Malecón", "Huaca Pucllana", "Barranco y el Puente de los Suspiros",
             "Museo Larco", "Parque del Amor", "Catacumbas del Convento de San Francisco", "Circuito Mágico del Agua",
             "Mercado de Surquillo", "Cena de ceviche y cocina peruana"],
    "Buenos Aires": ["Plaza de Mayo y Casa Rosada", "Caminito en La Boca", "Recoleta (cementerio y feria)", "San Telmo y su feria",
                     "Puerto Madero", "Teatro Colón", "Bosques de Palermo", "Obelisco y Avenida 9 de Julio",
                     "Librería El Ateneo Grand Splendid", "Una noche de tango", "Tigre y el Delta (excursión)"],
    "Nueva York": ["Central Park", "Times Square", "Estatua de la Libertad y Ellis Island", "Puente de Brooklyn y DUMBO",
                   "Empire State Building", "Museo Metropolitano de Arte (MET)", "The High Line", "Memorial del 11 de Septiembre",
                   "Top of the Rock", "Grand Central Terminal", "Una función en Broadway"],
    "Ciudad de México": ["Zócalo y Catedral Metropolitana", "Museo Nacional de Antropología", "Bosque y Castillo de Chapultepec",
                         "Coyoacán y la Casa Azul de Frida Kahlo", "Xochimilco (paseo en trajinera)", "Teotihuacán (excursión de un día)",
                         "Palacio de Bellas Artes", "Colonias Roma y Condesa", "Basílica de Guadalupe", "Torre Latinoamericana"],
    "París": ["Torre Eiffel", "Museo del Louvre", "Catedral de Notre-Dame", "Arco del Triunfo y Campos Elíseos",
              "Montmartre y Sacré-Cœur", "Museo de Orsay", "Crucero por el Sena", "Barrio Latino y Jardines de Luxemburgo",
              "Palacio de Versalles (excursión de un día)", "Sainte-Chapelle e Île de la Cité"],
    "Orlando": ["Magic Kingdom (Walt Disney World)", "Universal Studios Florida", "Islands of Adventure", "Epcot",
                "Disney's Hollywood Studios", "SeaWorld Orlando", "Disney Springs", "ICON Park", "Lake Eola Park",
                "Kennedy Space Center (excursión de un día)"],
    "Barcelona": ["Sagrada Familia", "Parque Güell", "La Rambla y Mercado de la Boquería", "Barrio Gótico", "Casa Batlló y Paseo de Gràcia",
                  "Playa de la Barceloneta", "Montjuïc", "Museo Picasso", "Camp Nou", "Tibidabo"],
    "Roma": ["Coliseo y Foro Romano", "Fontana de Trevi", "Panteón", "Ciudad del Vaticano (Basílica de San Pedro y Museos Vaticanos)",
             "Plaza de España y Piazza Navona", "Trastevere", "Villa Borghese", "Castel Sant'Angelo", "Capilla Sixtina"],
    "Londres": ["Big Ben y el Parlamento", "Torre de Londres y Tower Bridge", "Museo Británico", "London Eye", "Palacio de Buckingham",
                "Covent Garden", "Camden Market", "Hyde Park", "Notting Hill y Portobello Road", "Museo de Historia Natural"],
}
_GENERIC_PLACES = ["Recorrido por el centro histórico", "Museo local", "Mercado de artesanías y compras de recuerdos",
                   "Mirador de la ciudad al atardecer", "Parque principal y paseo a pie", "Visita a la catedral o iglesia principal",
                   "Plaza de mercado y comida típica", "Paseo por el barrio más tradicional", "Café local y caminata sin afán"]
_GENERIC_TITLES = ["Cultura y centro histórico", "Sabores y paseo", "Miradores y plazas", "Barrios y descanso"]
_FILLERS = ["Último paseo libre y desayuno tranquilo", "Paseo libre y compras de recuerdos",
            "Tarde de descanso en el hotel o la playa"]


def places_for(city: str) -> list:
    return PLACES.get(canon_city(city)) or []


def _short(place: str) -> str:
    return re.split(r"\s*\(", place)[0].strip()


# --------------------------------------------------------------------------
# Interpretacion de la solicitud (reglas deterministas; el LLM solo cubre los huecos)
# --------------------------------------------------------------------------
_W = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
      "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14}
_NUMRX = r"(?:\d{1,2}|" + "|".join(_W) + r")"


def _int(tok) -> Optional[int]:
    tok = str(tok).strip()
    if tok in _W:
        return _W[tok]
    return int(tok) if tok.isdigit() else None


def scan_cities(text) -> list:
    """Ciudades conocidas que aparecen en el texto: [(inicio, fin, nombre)]."""
    t = norm(text)
    out = []
    for m in _CITY_RX.finditer(t):
        key = ALIASES.get(m.group(1), m.group(1))
        out.append((m.start(), m.end(), CITIES[key]))
    return out


_ORIGIN_PRE = re.compile(r"(?:desde|salgo de|salir de|salimos de|saliendo de|partir de|parto de|partimos de|origen|vivo en|"
                         r"vivimos en|estoy en|estamos en|voy de|vamos de|viajo de|viajamos de)\s*(?:la ciudad de\s+)?$")
_DEST_PRE = re.compile(r"(?:\ba|\ben|hacia|para|rumbo a|destino|ir a|irme a|irnos a|viajar a|visitar|conocer|hasta)\s*(?:la ciudad de\s+)?$")
_PAIR = re.compile(r"\b(?:de|desde)\s+(?P<o>[a-z ]+?)\s+(?:a|hasta|hacia|para|con destino a)\s+(?P<d>[a-z ]+?)(?=[\s,.;:!?]|$)")


def det_cities(text):
    """(origen, destino) con reglas; None si no se puede asegurar."""
    t = norm(text)
    found = scan_cities(text)
    origin = dest = None
    m = _PAIR.search(t)
    if m:
        o, d = canon_city(m.group("o")), canon_city(m.group("d"))
        names = {c for _, _, c in found}
        if o in names and d in names and o != d:
            return o, d
    for s, _e, c in found:
        pre = t[max(0, s - 26):s]
        if _ORIGIN_PRE.search(pre):
            origin = c
        elif _DEST_PRE.search(pre) and c != origin:
            dest = c
    if dest and dest == origin:
        dest = None
    others = [c for _, _, c in found if c != origin and c != dest]
    if origin and not dest and len(others) == 1:
        dest = others[0]
    if dest and not origin and len(others) == 1 and len(found) == 2:
        origin = others[0]
    return origin, dest


def parse_days(t: str) -> Optional[int]:
    best = None
    for m in re.finditer(rf"\b({_NUMRX})\s*(dias?|noches?|semanas?)\b", t):
        if re.search(r"(?:\ben|dentro de|hace|dentro|de aqui a)\s*$", t[max(0, m.start() - 14):m.start()]):
            continue                                        # "en 10 dias" es cuando sale, no cuanto dura
        n, unit = _int(m.group(1)), m.group(2)
        if n is None:
            continue
        best = n + 1 if unit.startswith("noche") else n * 7 if unit.startswith("semana") else n
    if best is None and re.search(r"\b(?:fin de semana|puente)\b", t):
        best = 3
    return best


def parse_travelers(t: str) -> Optional[int]:
    m = re.search(rf"\bcon\s+({_NUMRX})\s+(?:personas?|amigos?|adultos?|familiares|pasajeros?|viajeros?)\s+mas\b", t)
    if m and _int(m.group(1)):
        return 1 + _int(m.group(1))                        # "con dos personas mas" = tu + 2
    ms = list(re.finditer(rf"\b(?:somos|seremos|viajamos|vamos|iremos|viajaremos)\s+({_NUMRX})\b", t))
    if ms and _int(ms[-1].group(1)):
        return _int(ms[-1].group(1))
    ms = list(re.finditer(rf"\b({_NUMRX})\s+(?:personas?|viajeros?|adultos?|pasajeros?)\b", t))
    if ms and _int(ms[-1].group(1)):
        return _int(ms[-1].group(1))
    if re.search(r"\b(?:voy|viajo|iria|ire|salgo|viajare)\s+(?:solo|sola|sin compania)\b|\bsolo yo\b|\bsolamente yo\b|\bpara mi\b", t):
        return 1
    if re.search(r"\b(?:mi (?:esposa|esposo|novia|novio|pareja) y yo|con mi (?:esposa|esposo|novia|novio|pareja)|en pareja)\b", t) \
            and not re.search(r"\b(?:hijos?|ninos?|ninas?|familia|amigos?)\b", t):
        return 2
    return None


def parse_budget(text) -> Optional[int]:
    """Presupuesto en COP a partir de '5 millones', '5.000.000', '800 mil', 'medio millon'. El ultimo mencionado gana."""
    t = norm(text)
    found = []                                              # (posicion, valor)
    taken = []

    def free(a, b):
        return all(b <= x or a >= y for x, y in taken)

    def add(m, val):
        if free(m.start(), m.end()) and val is not None:
            taken.append((m.start(), m.end()))
            found.append((m.start(), val))

    for m in re.finditer(rf"\b({_NUMRX}|\d+(?:[.,]\d+)?)\s+(?:de\s+)?millon(?:es)?(\s+y\s+medio)?\b", t):
        tok = m.group(1)
        base = _int(tok) if tok in _W or tok.isdigit() else float(tok.replace(",", "."))
        add(m, None if base is None else (base + (0.5 if m.group(2) else 0)) * 1_000_000)
    for m in re.finditer(r"\bmedio\s+millon(?:es)?\b", t):
        add(m, 500_000)
    for m in re.finditer(r"\$?\s*(\d{1,3}(?:[.,]\d{3})+)(?!\d)", t):
        add(m, int(re.sub(r"[.,]", "", m.group(1))))
    for m in re.finditer(rf"\b({_NUMRX}|\d+)\s*mil\b(?!lon)", t):
        n = _int(m.group(1))
        add(m, None if n is None else n * 1000)
    for m in re.finditer(r"(?<![\d/.,-])(\d{5,9})(?![\d/.,-])", t):
        add(m, int(m.group(1)))
    vals = [(p, v) for p, v in sorted(found) if 50_000 <= v <= 5_000_000_000]
    return int(vals[-1][1]) if vals else None


_DATE_HINT = re.compile(r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b"
                        r"|\b\d{1,2}[/-]\d{1,2}\b|\bproxim[oa]s?\b|\bnavidad\b|\bsemana santa\b|\bfin de ano\b|\bano nuevo\b"
                        r"|\bmanana\b|\bpasado manana\b|\bel \d{1,2}\b|\bpuente\b")


def extract_json_obj(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    s = re.sub(r"<thinking>.*?</thinking>", "", str(raw or ""), flags=re.S).strip()
    for cand in (s, s[s.find("{"): s.rfind("}") + 1]):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            continue
    return {}


_DEST_WORD = re.compile(r"\b(?:a|para|hacia|en|ir a|viajar a|conocer)\s+(" + "|".join(sorted(map(re.escape, COUNTRIES), key=len, reverse=True)) + r")\b")


def _dest_word(t: str) -> str:
    """Pais nombrado como destino en el texto ('quiero viajar a brasil'), por si el LLM no esta disponible."""
    m = _DEST_WORD.search(t)
    return m.group(1) if m else ""


def llm_extract(llm_text, text: str, today: date) -> dict:
    system = ("Eres un extractor de datos de viajes. Respondes UNICAMENTE con un objeto JSON valido, "
              "sin texto adicional y sin etiquetas.")
    prompt = (f"Hoy es {today.isoformat()}.\nMensaje(s) del usuario:\n{text}\n\n"
              'Devuelve exactamente este JSON: {"origin": ciudad de salida o null, "destination": ciudad de destino o null, '
              '"days": numero de dias o null, "travelers": total de personas que viajan INCLUYENDO a quien escribe o null, '
              '"budget_cop": presupuesto total en pesos colombianos como entero o null, '
              '"start_date": "YYYY-MM-DD" o null, "end_date": "YYYY-MM-DD" o null}.\n'
              "Reglas: el destino y el origen son CIUDADES (si solo se nombra un pais, deja ese campo en null); solo el nombre de la ciudad (sin departamento ni pais); 'cinco millones' = 5000000; "
              "'con dos personas mas' = 3 viajeros; si un dato no se menciona, null (nunca lo inventes); "
              "una fecha sin ano es la proxima ocurrencia en el futuro.")
    return extract_json_obj(llm_text(system, prompt))


def _appears(city: str, text_norm: str) -> bool:
    """La ciudad que devolvio el LLM debe aparecer de verdad en el texto del usuario (anti-alucinacion)."""
    key = norm(city)
    if not key:
        return False
    return key in text_norm or bool(re.search(r"\b" + re.escape(key.split()[0]), text_norm))


def _iso(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


class TripRequest:
    def __init__(self, origin, destination, days, travelers, budget_cop, start, end, assumptions, how):
        self.origin, self.destination, self.days, self.travelers = origin, destination, days, travelers
        self.budget_cop, self.start, self.end = budget_cop, start, end
        self.assumptions, self.how = assumptions, how

    def interpretation(self) -> str:
        return (f"{self.origin} → {self.destination} · {self.days} {'día' if self.days == 1 else 'días'} · "
                f"{self.travelers} {'viajero' if self.travelers == 1 else 'viajeros'} · {cop_fmt(self.budget_cop)} · "
                f"{dmy(self.start)} al {dmy(self.end)}")


def extract_request(text: str, today: date, llm_text=None, allow_defaults: bool = False):
    """-> (TripRequest | None, faltantes, como). `faltantes` usa las mismas frases que la guarda de datos."""
    t = norm(text)
    origin, dest = det_cities(text)
    days, travelers, budget = parse_days(t), parse_travelers(t), parse_budget(text)
    llm = {}
    need_llm = not (origin and dest and travelers and budget) or bool(_DATE_HINT.search(t))
    how = "reglas"
    if need_llm and llm_text:
        try:
            llm = llm_extract(llm_text, text, today)
            how = "reglas + Nova Pro"
        except Exception as exc:                               # el LLM es opcional: las reglas siguen valiendo
            print(f"[plan] extraccion con LLM fallo, sigo con reglas: {exc}")
            llm = {}
    if not origin and llm.get("origin"):
        c = canon_city(llm["origin"])
        origin = c if _appears(c, t) else None
    llm_dest_raw = llm.get("destination")
    if not dest and llm_dest_raw:
        c = canon_city(llm_dest_raw)
        dest = c if _appears(c, t) and c != origin and not country_of(c) else None
    if not travelers and isinstance(llm.get("travelers"), (int, float)) and 1 <= int(llm["travelers"]) <= 20:
        travelers = int(llm["travelers"])
    if not budget and isinstance(llm.get("budget_cop"), (int, float)) and 50_000 <= float(llm["budget_cop"]) <= 5e9:
        budget = int(llm["budget_cop"])
    llm_days = llm.get("days") if isinstance(llm.get("days"), (int, float)) else None
    s_llm, e_llm = _iso(llm.get("start_date")), _iso(llm.get("end_date"))

    assumptions = []
    if allow_defaults:
        if not origin:
            origin = "Bogotá"; assumptions.append("Asumí que sales desde Bogotá")
        if not travelers:
            travelers = 1; assumptions.append("Asumí 1 viajero")
        if not budget:
            budget = 2_000_000; assumptions.append(f"Asumí un presupuesto de {cop_fmt(2_000_000)}")
    missing = []
    if not origin:
        missing.append("la ciudad de salida")
    if not dest:
        c = country_of(llm_dest_raw) or country_of(_dest_word(t))
        missing.append(f"la ciudad de destino en {c[0]} (por ejemplo, {c[1]})" if c else "el destino")
    if not travelers:
        missing.append("cuántas personas viajan")
    if not budget:
        missing.append("tu presupuesto en pesos")
    if missing:
        return None, missing, how
    if origin == dest:
        return None, ["un destino distinto a la ciudad de salida"], how

    end = None
    if s_llm and today <= s_llm <= today + timedelta(days=400):
        start = s_llm
        if e_llm and start <= e_llm <= start + timedelta(days=30):
            end = e_llm
            days = (end - start).days + 1
    else:
        start = today + timedelta(days=10)
        assumptions.append(f"Asumí la salida el {dmy(start)}")
    if end is None:
        days = days or (int(llm_days) if llm_days else None)
        if not days:
            days = 4
            assumptions.append("Asumí 4 días de viaje")
    if days > 14:
        days = 14
        assumptions.append("Limité el viaje a 14 días")
    days = max(1, int(days))
    if end is None or (end - start).days + 1 != days:
        end = start + timedelta(days=days - 1)
    return TripRequest(origin, dest, days, int(travelers), int(budget), start, end, assumptions, how), [], how


# --------------------------------------------------------------------------
# Presupuesto, vuelos, clima (funciones puras)
# --------------------------------------------------------------------------
def compute_budget(budget_cop, travelers, days, flight_price_usd_per_person=0.0, round_trip=True, fx=4000.0) -> dict:
    """Hospedaje 150.000 COP/persona/noche y alimentacion + actividades 120.000 COP/persona/dia (estimados)."""
    travelers = max(int(travelers), 1)
    days = max(int(days), 1)
    nights = max(days - 1, 1)
    legs = 2 if round_trip else 1
    flights_cop = round((flight_price_usd_per_person or 0) * fx * legs * travelers)
    lodging_cop = 150_000 * nights * travelers
    food_act_cop = 120_000 * days * travelers
    total = flights_cop + lodging_cop + food_act_cop
    return {
        "budgetCOP": round(budget_cop), "fxCOPperUSD": fx, "travelers": travelers, "days": days, "nights": nights,
        "breakdown": [
            {"item": "Vuelos", "costCOP": flights_cop},
            {"item": "Hospedaje (estimado)", "costCOP": lodging_cop},
            {"item": "Alimentacion y actividades (estimado)", "costCOP": food_act_cop},
        ],
        "totalEstimatedCOP": total, "remainingCOP": round(budget_cop) - total,
        "withinBudget": total <= budget_cop, "perPersonCOP": math.ceil(total / travelers),
    }


def _price(f) -> float:
    try:
        p = float(f.get("price_usd"))
        return p if p > 0 else float("inf")
    except (TypeError, ValueError, AttributeError):
        return float("inf")


def sort_flights(flights) -> list:
    return sorted([f for f in (flights or []) if isinstance(f, dict)], key=_price)


def cheapest_flight(flights) -> Optional[dict]:
    fl = sort_flights(flights)
    return fl[0] if fl and _price(fl[0]) != float("inf") else None


def usd(v) -> str:
    """82.0 -> '82'; 82.5 -> '82.5'; lo que no sea numero se devuelve tal cual."""
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def flight_line(f: dict) -> str:
    stops = int(f.get("stops") or 0)
    return (f"{f.get('airline') or 'Aerolínea'} {f.get('flight') or ''} · sale {f.get('depart') or '--:--'} · "
            f"{'directo' if stops == 0 else f'{stops} escala' + ('s' if stops > 1 else '')} · US${usd(f.get('price_usd'))}").replace("  ", " ")


# "Chubascos" es una palabra poco usada fuera de España: se dice igual de claro con "lluvia".
_CONDITION_WORDS = {
    "chubascos ligeros": "Lluvia ligera por ratos",
    "chubascos fuertes": "Lluvia fuerte por ratos",
    "chubascos": "Lluvia por ratos",
    "chubasco": "Lluvia por ratos",
    "tormenta": "Tormenta eléctrica",
}


def friendly_condition(text) -> str:
    t = str(text or "").strip()
    return _CONDITION_WORDS.get(t.lower(), t)


def friendly_weather(w: dict) -> dict:
    """Devuelve el clima con las descripciones en lenguaje claro (no toca el resto de los datos)."""
    for d in (w.get("days") or []):
        if isinstance(d, dict) and d.get("condition"):
            d["condition"] = friendly_condition(d["condition"])
    return w


def weather_line(w: dict) -> str:
    days = [d for d in (w.get("days") or []) if isinstance(d, dict)]
    if not days:
        return ""
    temps = [t for d in days for t in (d.get("temp_min_c"), d.get("temp_max_c")) if isinstance(t, (int, float))]
    conds = [str(d.get("condition")) for d in days if d.get("condition")]
    rain = [d.get("rain_probability_pct") for d in days if isinstance(d.get("rain_probability_pct"), (int, float))]
    parts = []
    if temps:
        parts.append(f"{round(min(temps))}°–{round(max(temps))}°C")
    if conds:
        parts.append(max(set(conds), key=conds.count).lower())
    if rain and max(rain) >= 40:
        parts.append(f"lluvia hasta {round(max(rain))}%")
    if w.get("source") == "historico_ano_anterior":
        parts.append("referencia del año pasado")
    return " · ".join(parts)


def friendly_error(kind: str, res) -> str:
    raw = str((res or {}).get("error") or "") if isinstance(res, dict) else ""
    low = raw.lower()
    if "429" in low or "limit" in low or "throttl" in low:
        return f"El servicio de {kind} está saturado en este momento."
    if "timeout" in low or "timed out" in low:
        return f"El servicio de {kind} tardó demasiado en responder."
    return f"No se pudo obtener {kind} ahora."


# --------------------------------------------------------------------------
# Itinerario: el LLM reparte lugares REALES por dia (por numero), el codigo arma el texto
# --------------------------------------------------------------------------
def slots_for(n: int) -> list:
    """Cuantos lugares caben por dia: llegada (tarde + noche), dias completos (3), regreso (manana)."""
    if n <= 1:
        return [2]
    return [2] + [3] * (n - 2) + [1]


def _foreign_hit(text: str, dest: str) -> bool:
    t = norm(text)
    for city, places in PLACES.items():
        if city == dest:
            continue
        for p in places:
            key = norm(_short(p))
            if len(key) > 8 and key in t:
                return True
    return False


def _llm_assign(llm_text, req: TripRequest, catalog: list, weather: dict) -> dict:
    slots = slots_for(req.days)
    wx = {}
    for d in (weather or {}).get("days", []) or []:
        if isinstance(d, dict) and d.get("date"):
            wx[str(d["date"])[:10]] = f"{d.get('condition', '')} (lluvia {d.get('rain_probability_pct', '?')}%)"
    lines = []
    for i in range(req.days):
        day = req.start + timedelta(days=i)
        lines.append(f"Día {i + 1} ({day.isoformat()}): {slots[i]} lugar(es). Clima: {wx.get(day.isoformat(), 'sin dato')}")
    cat = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(catalog))
    system = ("Eres un planificador de viajes. Respondes UNICAMENTE con un objeto JSON valido, sin texto adicional "
              "y sin etiquetas <thinking>.")
    prompt = (f"Destino: {req.destination}. Viaje de {req.days} día(s).\nLUGARES DISPONIBLES (usa SOLO estos números):\n{cat}\n\n"
              "Cantidad de lugares por día y clima:\n" + "\n".join(lines) + "\n\n"
              'Devuelve exactamente: {"days":[{"day":1,"title":"titulo corto del dia (maximo 6 palabras)","places":[numeros]}]}\n'
              "Reglas: cada número se usa una sola vez en todo el viaje; agrupa lugares cercanos el mismo día; "
              "si un día tiene lluvia alta, prefiere lugares bajo techo (museos, iglesias, acuario); "
              "dentro de cada día pon primero lo que se hace de día (playas, islas, excursiones, museos, parques) y al final "
              "lo que sirve para el atardecer o la noche (plazas, barrios, calles, malecones, shows); "
              "los planes solo nocturnos (noche de salsa o tango, cenas, funciones) van únicamente en días intermedios, nunca el último día. "
              "No inventes lugares ni uses otros números.")
    return extract_json_obj(llm_text(system, prompt))


# Lugares que no tiene sentido ubicar de noche (playas, excursiones, parques, museos...): van al "atardecer".
_NOT_NIGHT = re.compile(r"playa|isla|excursi[oó]n|parque|bosque|cenote|snorkel|catamar[aá]n|mirador|jard[ií]n|cerro|termales|valle|"
                        r"laguna|cascada|castillo|museo|palacio|catedral|zool[oó]gico|acuario|estadio|cristo|pan de az[uú]car|"
                        r"everglades|key biscayne|delta|tirolesa|scape|xcaret|universal|epcot|magic kingdom|seaworld|islands of|"
                        r"studios|space center|versalles|teotihuac[aá]n|chich[eé]n|tulum|saona|catalina|tayrona|minca|guatap[eé]|arv[ií]", re.I)


def _day_order(place: str) -> int:
    return 0 if _NOT_NIGHT.search(place) else 2 if _NIGHT_ONLY.search(place) else 1


def _evening(place: str) -> str:
    return "Atardecer" if _NOT_NIGHT.search(place) else "Noche"


# Planes que solo tienen sentido de noche ("Una noche de salsa", "Cena de ceviche", "Una función en Broadway").
_NIGHT_ONLY = re.compile(r"\bnoche\b|\bcena\b|funci[oó]n|tango|nocturn", re.I)


def build_itinerary(req: TripRequest, flight: Optional[dict], assign: Optional[dict]) -> tuple:
    """Arma el itinerario. `assign` es la propuesta del LLM (puede ser None o invalida: se valida y se completa)."""
    catalog = places_for(req.destination) or list(_GENERIC_PLACES)
    slots = slots_for(req.days)
    used, titles, picks = set(), {}, {}
    from_llm = False
    if isinstance(assign, dict) and isinstance(assign.get("days"), list):
        for k, entry in enumerate(assign["days"]):
            if not isinstance(entry, dict):
                continue
            idx = (entry.get("day") - 1) if isinstance(entry.get("day"), int) else k
            if not (0 <= idx < req.days):
                continue
            ok = []
            for n in entry.get("places") or []:
                if isinstance(n, int) and 1 <= n <= len(catalog) and n not in used:
                    used.add(n)
                    ok.append(catalog[n - 1])
            picks[idx] = ok[: slots[idx]]
            title = str(entry.get("title") or "").strip()
            if 3 <= len(title) <= 60 and not _foreign_hit(title, req.destination):
                titles[idx] = title
            from_llm = from_llm or bool(ok)
    pool = [p for i, p in enumerate(catalog, 1) if i not in used]
    fill_i = 0
    gots = []
    for i in range(req.days):                                    # pasada 1: que lugares van en cada dia
        got = list(picks.get(i, []))
        while len(got) < slots[i]:
            if pool:
                got.append(pool.pop(0))
            else:
                got.append(_FILLERS[fill_i % len(_FILLERS)])
                fill_i += 1
        # Un plan solo nocturno va unicamente en el ultimo turno del dia (la noche) y nunca el dia del regreso.
        is_last = i == req.days - 1 and req.days > 1
        allowed_night = 0 if is_last else 1
        while sum(bool(_NIGHT_ONLY.search(pl)) for pl in got) > allowed_night:
            k = max(j for j, pl in enumerate(got) if _NIGHT_ONLY.search(pl))
            moved = got.pop(k)
            nxt = next((pl for pl in pool if not _NIGHT_ONLY.search(pl)), None)
            if nxt is not None:
                pool.remove(nxt)
            if is_last:
                # el nocturno ocupa la noche de un dia anterior y el lugar que estaba ahi pasa al dia del regreso
                for g in reversed(gots):
                    if len(g) >= 2 and not _NIGHT_ONLY.search(g[-1]):
                        g[-1], nxt = moved, (nxt or g[-1])
                        break
            if nxt is None:
                nxt = _FILLERS[fill_i % len(_FILLERS)]
                fill_i += 1
            got.insert(k, nxt)
            if not is_last:
                pool.append(moved)                               # puede servir en otro dia
        got.sort(key=_day_order)                                 # estable: lo diurno (playas, islas, museos...) primero, plazas y barrios despues y lo solo nocturno al final
        gots.append(got)
    days = []
    for i, got in enumerate(gots):                               # pasada 2: dibujar cada dia
        got.sort(key=_day_order)
        first = i == 0
        last = i == req.days - 1 and req.days > 1
        acts = []
        if first:
            tag = f" ({flight.get('airline') or 'aerolínea'} {flight.get('flight') or ''}, sale {flight.get('depart') or '--:--'})".replace("  ", " ") if flight else ""
            acts += [f"Vuelo {req.origin} → {req.destination}{tag}", "Check-in en el hotel y descanso"]
            acts += [f"Tarde: {got[0]}"] + ([f"{_evening(got[1])}: {got[1]}"] if len(got) > 1 else [])
            if req.days == 1:
                acts += [f"Regreso a {req.origin}"]
        elif last:
            acts += [f"Mañana: {got[0]}", "Almuerzo y compras de recuerdos", f"Check-out y regreso a {req.origin}"]
        else:
            acts += [f"Mañana: {got[0]}", "Almuerzo de comida típica de la región", f"Tarde: {got[1]}", f"{_evening(got[2])}: {got[2]}"]
        generic = not places_for(req.destination)
        default_title = (f"Llegada a {req.destination}" if first else "Despedida y regreso" if last
                         else _GENERIC_TITLES[(i - 1) % len(_GENERIC_TITLES)] if generic
                         else "Día libre y paseo tranquilo" if got[0] in _FILLERS
                         else _short(next((pl for pl in got if not _NIGHT_ONLY.search(pl)), got[0])))
        title = default_title if last else (titles.get(i) or default_title)       # el dia del regreso siempre es "Despedida y regreso"
        days.append({"day": i + 1, "title": title, "activities": acts})
    return days, ("lugares reales del destino" if places_for(req.destination) else "actividades generales")


# --------------------------------------------------------------------------
# Eventos de la linea de tiempo
# --------------------------------------------------------------------------
# "agent" es lo que ve el usuario (lenguaje normal). "tech" es el detalle de arquitectura: la interfaz solo lo muestra
# si la persona activa "Detalles técnicos" (util para explicar el sistema en una demostracion).
STEPS = [
    {"id": "understand", "label": "Entender tu solicitud", "agent": "Asistente", "tech": "Orchestrator · reglas + Nova Pro"},
    {"id": "weather", "label": "Consultar el clima", "agent": "Agente de clima", "tech": "Weather Agent · A2A · MCP Open-Meteo"},
    {"id": "flights", "label": "Buscar vuelos", "agent": "Agente de vuelos", "tech": "Flight Agent · A2A · MCP Kiwi"},
    {"id": "budget", "label": "Calcular el presupuesto", "agent": "Asistente", "tech": "Orchestrator · cálculo determinista"},
    {"id": "itinerary", "label": "Diseñar el itinerario", "agent": "Asistente", "tech": "Orchestrator · Nova Pro + catálogo de lugares"},
    {"id": "save_trip", "label": "Guardar el viaje", "agent": "Tu cuenta", "tech": "Tool create_trip · Lambda → DynamoDB"},
    {"id": "save_itinerary", "label": "Guardar el itinerario", "agent": "Tu cuenta", "tech": "Tool create_itinerary · Lambda → DynamoDB"},
]


def _step(pid, sid, status, **extra):
    ev = {"type": "step", "plan": pid, "id": sid, "status": status}
    ev.update({k: v for k, v in extra.items() if v is not None})
    return ev


def _ms(t0) -> int:
    return int((time.monotonic() - t0) * 1000)


async def _until(task, every=8):
    """Cede un 'ping' cada `every` s mientras la tarea sigue (mantiene viva la conexion de streaming)."""
    while not task.done():
        await asyncio.wait({task}, timeout=every)
        if not task.done():
            yield {"type": "ping"}


def _retry(fn, ok, *args):
    """Llama fn hasta 2 veces (en un hilo). Devuelve (resultado, intentos)."""
    res = {}
    for attempt in (1, 2):
        try:
            res = fn(*args)
        except Exception as exc:
            res = {"error": f"{type(exc).__name__}: {exc}"}
        if ok(res):
            return res, attempt
        if attempt == 1:
            if re.search(r"time(d)?[ -]?out|timeout", str(res).lower()):
                return res, attempt                     # un especialista lento no se reintenta: duplicaria la espera
            time.sleep(1.2)
    return res, 2


def _once(fn, *args):
    """Una sola llamada (para escrituras: reintentar podria duplicar un viaje o una reserva)."""
    try:
        return fn(*args)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


class Deps:
    """Dependencias externas del flujo (se inyectan: en produccion A2A/Lambda/Bedrock, en pruebas simuladas)."""
    def __init__(self, flights, weather, invoke_lambda, llm_text, fx=4000.0):
        self.flights, self.weather, self.invoke_lambda, self.llm_text, self.fx = flights, weather, invoke_lambda, llm_text, fx


def _is_ok_weather(r) -> bool:
    return isinstance(r, dict) and isinstance(r.get("days"), list) and len(r["days"]) > 0 and not r.get("error")


def _is_ok_flights(r) -> bool:
    return isinstance(r, dict) and isinstance(r.get("flights"), list) and not r.get("error")


def _is_ok_lambda(key):
    return lambda r: isinstance(r, dict) and bool(r.get(key)) and not r.get("error")


async def run_plan(text: str, deps: Deps, state: dict, today: Optional[date] = None, allow_defaults: bool = False):
    """Ejecuta el plan completo y emite los eventos. Resultado final en `state`:
    ok, missing, req, trip, cheapest, facts, suggestions."""
    today = today or date.today()
    pid = "plan-" + uuid.uuid4().hex[:8]
    t_all = time.monotonic()
    state.update(ok=False, missing=[], problems=[])

    # 1) Entender (antes de mostrar la linea de tiempo: si falta un dato, solo se pregunta, sin pasos en rojo) -----------
    t0 = time.monotonic()
    task = asyncio.create_task(asyncio.to_thread(extract_request, text, today, deps.llm_text, allow_defaults))
    async for ping in _until(task):
        yield ping
    req, missing, how = task.result()
    if not req:
        state["missing"] = missing
        return
    yield {"type": "plan", "id": pid, "title": "Armando tu viaje", "steps": [dict(s) for s in STEPS]}
    yield _step(pid, "understand", "done", detail=req.interpretation(), ms=_ms(t0),
                tech="Orchestrator · reglas" if how == "reglas" else "Orchestrator · reglas + Nova Pro",
                title=f"{req.origin} → {req.destination}", assumptions=req.assumptions or None)
    state["req"] = req
    problems = state["problems"]

    # 2) Clima y vuelos en paralelo (dos agentes especialistas, dos servidores MCP) ----------------
    d0, d1 = dmy(req.start), dmy(req.end)
    wid, fid = f"{pid}-w", f"{pid}-f"
    yield _step(pid, "weather", "running", label=f"Clima en {req.destination}", detail="Consultando el pronóstico…")
    yield {"type": "tool_start", "name": "ask_weather", "id": wid, "step": "weather"}
    yield _step(pid, "flights", "running", label=f"Vuelos {req.origin} → {req.destination}", detail="Buscando vuelos reales…")
    yield {"type": "tool_start", "name": "ask_flights", "id": fid, "step": "flights"}

    async def timed(kind, fn, ok, *args):
        t = time.monotonic()
        res, attempts = await asyncio.to_thread(_retry, fn, ok, *args)
        return kind, res, attempts, _ms(t)

    pending = {asyncio.create_task(timed("weather", deps.weather, _is_ok_weather, req.destination, d0, d1)),
               asyncio.create_task(timed("flights", deps.flights, _is_ok_flights, req.origin, req.destination, d0))}
    weather, flights_res, f_ms = {}, {}, 0
    flights_status = "error"
    while pending:
        done, pending = await asyncio.wait(pending, timeout=8, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            yield {"type": "ping"}
            continue
        for tk in done:
            kind, res, attempts, ms = tk.result()
            if kind == "weather":
                if _is_ok_weather(res):
                    weather = friendly_weather(res)
                    weather.setdefault("city", req.destination)
                    yield {"type": "tool_result", "id": wid, "name": "ask_weather", "status": "success", "data": weather, "step": "weather"}
                    yield _step(pid, "weather", "done", detail=weather_line(weather) or "Pronóstico listo", ms=ms, attempt=attempts)
                else:
                    print(f"[plan] clima fallo: {str(res)[:200]}")
                    msg = friendly_error("clima", res)
                    problems.append("clima")
                    yield {"type": "tool_result", "id": wid, "name": "ask_weather", "status": "error", "data": {"error": msg}, "step": "weather"}
                    yield _step(pid, "weather", "error", detail=msg, ms=ms, attempt=attempts,
                                retry=f"Consulta de nuevo el clima de {req.destination} del {d0} al {d1}.")
            else:
                if _is_ok_flights(res):
                    fl = sort_flights(res.get("flights"))
                    flights_res = {**{k: v for k, v in res.items() if k != "flights"}, "origin": req.origin,
                                   "destination": req.destination, "date": d0, "flights": fl[:6]}
                    f_ms = ms
                    flights_status = "ok" if fl else "empty"
                    best = cheapest_flight(fl)
                    detail = (f"{len(fl)} opciones · la más barata US${best['price_usd']:g} ({best.get('airline') or 'aerolínea'})"
                              if best else ("Sin vuelos para esa ruta y fecha" if not fl else f"{len(fl)} opciones"))
                    yield {"type": "tool_result", "id": fid, "name": "ask_flights", "status": "success", "data": flights_res, "step": "flights"}
                    yield _step(pid, "flights", "done", detail=detail, ms=ms, attempt=attempts)
                    if not fl:
                        problems.append("vuelos")
                else:
                    print(f"[plan] vuelos fallo: {str(res)[:200]}")
                    msg = friendly_error("vuelos", res)
                    problems.append("vuelos")
                    yield {"type": "tool_result", "id": fid, "name": "ask_flights", "status": "error", "data": {"error": msg}, "step": "flights"}
                    yield _step(pid, "flights", "error", detail=msg, ms=ms, attempt=attempts,
                                retry=f"Busca de nuevo vuelos de {req.origin} a {req.destination} para el {d0}.")
    flights_list = flights_res.get("flights") or []
    best = cheapest_flight(flights_list)

    # 3) Presupuesto (determinista) ----------------------------------------------------------------
    t0 = time.monotonic()
    yield _step(pid, "budget", "running", detail="Sumando vuelos, hospedaje y comidas…")
    bid = f"{pid}-b"
    yield {"type": "tool_start", "name": "calculate_budget", "id": bid, "step": "budget"}
    budget = compute_budget(req.budget_cop, req.travelers, req.days, float(best["price_usd"]) if best else 0.0, True, deps.fx)
    if not best:
        budget["note"] = "No incluye vuelos: no hubo precios disponibles."
    yield {"type": "tool_result", "id": bid, "name": "calculate_budget", "status": "success", "data": budget, "step": "budget"}
    verdict = (f"sobran {cop_fmt(budget['remainingCOP'])}" if budget["withinBudget"] else f"faltan {cop_fmt(-budget['remainingCOP'])}")
    yield _step(pid, "budget", "done", detail=f"Total estimado {cop_fmt(budget['totalEstimatedCOP'])} · {verdict}", ms=_ms(t0))

    # 4) Itinerario (LLM reparte lugares reales; el codigo valida y arma) ---------------------------
    t0 = time.monotonic()
    yield _step(pid, "itinerary", "running", detail="Repartiendo lugares reales por día…")
    iid = f"{pid}-i"
    yield {"type": "tool_start", "name": "plan_itinerary", "id": iid, "step": "itinerary"}
    catalog = places_for(req.destination)

    def design():
        assign = None
        if catalog:
            try:
                assign = _llm_assign(deps.llm_text, req, catalog, weather)
            except Exception as exc:
                print(f"[plan] el LLM no repartio el itinerario, uso el catalogo: {exc}")
        return build_itinerary(req, best, assign)

    task = asyncio.create_task(asyncio.to_thread(design))
    async for ping in _until(task):
        yield ping
    days, source = task.result()
    n_act = sum(len(d["activities"]) for d in days)
    yield {"type": "tool_result", "id": iid, "name": "plan_itinerary", "status": "success", "data": {"days": days, "source": source}, "step": "itinerary"}
    yield _step(pid, "itinerary", "done", detail=f"{req.days} {'día' if req.days == 1 else 'días'} · {n_act} actividades · {source}", ms=_ms(t0))

    # 5) Guardar el viaje y el itinerario (Tools -> Lambdas -> DynamoDB) ----------------------------
    t0 = time.monotonic()
    yield _step(pid, "save_trip", "running", detail="Guardando tu viaje…")
    tid = f"{pid}-t"
    yield {"type": "tool_start", "name": "create_trip", "id": tid, "step": "save_trip"}
    payload = {"userId": state.get("userId") or "demo-user", "origin": req.origin, "destination": req.destination,
               "startDate": d0, "endDate": d1, "travelers": req.travelers, "budget": req.budget_cop}
    task = asyncio.create_task(asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-CreateTrip", payload))
    async for ping in _until(task):
        yield ping
    trip = task.result()
    trip_ok = _is_ok_lambda("tripId")(trip)
    if trip_ok:
        state["trip"] = trip
        yield {"type": "tool_result", "id": tid, "name": "create_trip", "status": "success", "data": trip, "step": "save_trip"}
        yield _step(pid, "save_trip", "done", detail="Viaje guardado en tu cuenta", ms=_ms(t0))
    else:
        print(f"[plan] create_trip fallo: {str(trip)[:200]}")
        problems.append("guardado")
        yield {"type": "tool_result", "id": tid, "name": "create_trip", "status": "error",
               "data": {"error": "No se pudo guardar el viaje."}, "step": "save_trip"}
        yield _step(pid, "save_trip", "error", detail="No se pudo guardar el viaje. Puedes pedirme que lo guarde de nuevo.", ms=_ms(t0),
                    retry=f"Guarda el viaje de {req.origin} a {req.destination} del {d0} al {d1} para {req.travelers} viajeros con presupuesto de {req.budget_cop} pesos.")

    if trip_ok:
        t0 = time.monotonic()
        yield _step(pid, "save_itinerary", "running", detail="Guardando el itinerario…")
        sid = f"{pid}-s"
        yield {"type": "tool_start", "name": "create_itinerary", "id": sid, "step": "save_itinerary"}
        task = asyncio.create_task(asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-CreateItinerary",
                                                     {"tripId": trip["tripId"], "days": days}))
        async for ping in _until(task):
            yield ping
        it = task.result()
        if _is_ok_lambda("itineraryId")(it):
            state["itinerary_id"] = it["itineraryId"]
            yield {"type": "tool_result", "id": sid, "name": "create_itinerary", "status": "success", "data": it, "step": "save_itinerary"}
            yield _step(pid, "save_itinerary", "done", detail="Itinerario guardado", ms=_ms(t0))
        else:
            print(f"[plan] create_itinerary fallo: {str(it)[:200]}")
            problems.append("itinerario guardado")
            yield {"type": "tool_result", "id": sid, "name": "create_itinerary", "status": "error",
                   "data": {"error": "No se pudo guardar el itinerario."}, "step": "save_itinerary"}
            yield _step(pid, "save_itinerary", "error", detail="No se pudo guardar el itinerario.", ms=_ms(t0))
    else:
        yield _step(pid, "save_itinerary", "skipped", detail="Se guarda después de crear el viaje")

    # 6) Datos para el resumen y acciones sugeridas -------------------------------------------------
    state.update(ok=True, cheapest=best, flights=flights_list, weather=weather, budget=budget, days_plan=days)
    state["facts"] = build_facts(req, weather, best, flights_list, flights_status, budget, days, trip if trip_ok else None)
    state["suggestions"] = build_suggestions(req, best, state.get("trip"))
    yield {"type": "plan_done", "plan": pid, "ok": not problems, "ms": _ms(t_all)}


# --------------------------------------------------------------------------
# Resumen final y sugerencias
# --------------------------------------------------------------------------
def build_facts(req, weather, best, flights, flights_status, budget, days, trip, updated_notes=None, weather_kept=False) -> list:
    lines = []
    if updated_notes:
        lines.append("ACTUALIZACIÓN de un viaje que ya estaba guardado. Cambios hechos: " + "; ".join(updated_notes) + ".")
    lines.append(f"Viaje: {req.origin} → {req.destination}, {req.days} días, del {dmy(req.start)} al {dmy(req.end)}, "
                 f"{req.travelers} viajeros, presupuesto {cop_fmt(req.budget_cop)}.")
    wl = weather_line(weather) if weather else ""
    if weather_kept and not wl:
        lines.append("Clima: sin cambios (mismo destino y fechas que ya se le mostraron).")
    else:
        lines.append(f"Clima en {req.destination}: {wl}." if wl else "Clima: NO estuvo disponible (el servicio falló); dilo con claridad.")
    if best:
        lines.append(f"Vuelo de ida más barato: {flight_line(best)} por persona ({len(flights)} opciones en total).")
    elif flights_status == "empty":
        lines.append("Vuelos: no se encontraron opciones para esa ruta y fecha.")
    else:
        lines.append("Vuelos: NO estuvieron disponibles (el servicio falló); dilo con claridad.")
    verdict = f"sobran {cop_fmt(budget['remainingCOP'])}" if budget["withinBudget"] else f"faltan {cop_fmt(-budget['remainingCOP'])}"
    lines.append(f"Presupuesto: costo total estimado {cop_fmt(budget['totalEstimatedCOP'])} (vuelos ida y vuelta, hospedaje y comidas estimados); "
                 f"{verdict} frente a {cop_fmt(req.budget_cop)}.")
    if days is None:
        lines.append("El itinerario no cambió.")
    else:
        lines.append(f"Itinerario de {len(days)} días armado con lugares reales de {req.destination}.")
    if updated_notes:
        lines.append("El viaje quedó actualizado en su cuenta." if trip else "El viaje NO se pudo actualizar; dilo con claridad.")
    else:
        lines.append("El viaje y el itinerario quedaron guardados en su cuenta." if trip else "El viaje NO se pudo guardar; dilo con claridad.")
    if req.assumptions:
        lines.append("Supuestos que hiciste (menciónalos): " + "; ".join(req.assumptions) + ".")
    return lines


def template_summary(facts: list) -> str:
    """Resumen de respaldo (sin LLM) con los mismos datos."""
    upd = bool(facts) and facts[0].startswith("ACTUALIZACIÓN")
    o = 1 if upd else 0
    body = " ".join(facts[1 + o:5 + o])
    tail = " ".join(facts[5 + o:])
    head = "Listo, actualicé tu viaje. " + facts[0].split("Cambios hechos: ", 1)[-1] + " " if upd else "Listo, armé tu plan. "
    return (f"{head}{body} {tail} ¿Quieres que reserve el vuelo más barato? La reserva es simulada.").strip()


def build_suggestions(req, best, trip) -> list:
    items = []
    if best and trip:
        items.append({"label": "Reservar el vuelo más barato", "icon": "ticket",
                      "action": {"type": "reserve_flight", "tripId": trip["tripId"], "flight": best,
                                 "origin": req.origin, "destination": req.destination, "date": dmy(req.start), "travelers": req.travelers}})
    items.append({"label": "Ver mis viajes", "icon": "suitcase", "goto": "trips"})
    return items


# --------------------------------------------------------------------------
# Reserva de vuelo (determinista, con su propia mini linea de tiempo)
# --------------------------------------------------------------------------
_PICK_STEPS = [
    {"id": "pick", "label": "Confirmar el vuelo elegido", "agent": "Asistente", "tech": "Orchestrator · datos del Flight Agent"},
    {"id": "reserve", "label": "Registrar la reserva simulada", "agent": "Tu cuenta", "tech": "Tool create_reservation · Lambda → DynamoDB"},
]


def pick_flight(prompt: str, options: list) -> Optional[dict]:
    """Elige el vuelo que menciona el usuario (codigo o hora) o el mas barato."""
    opts = [o for o in (options or []) if isinstance(o, dict)]
    if not opts:
        return None
    t = norm(prompt)
    for o in opts:
        code = norm(o.get("flight"))
        if code and len(code) >= 3 and code in t:
            return o
    for o in opts:
        dep = str(o.get("depart") or "")
        if dep and re.search(rf"(?<!\d){re.escape(dep)}(?!\d)", t):
            return o
    return cheapest_flight(opts) or opts[0]


async def run_reserve(flight: dict, trip: dict, deps: Deps, state: dict):
    pid = "plan-" + uuid.uuid4().hex[:8]
    t_all = time.monotonic()
    state.update(ok=False, text="")
    origin, dest = trip.get("origin") or flight.get("origin") or "", trip.get("destination") or flight.get("destination") or ""
    yield {"type": "plan", "id": pid, "title": "Reservar vuelo", "steps": [dict(s) for s in _PICK_STEPS]}
    yield _step(pid, "pick", "running", detail="Revisando la opción…")
    price = flight.get("price_usd")
    travelers = int(trip.get("travelers") or flight.get("travelers") or 1)
    # No duplicar: si ese mismo vuelo ya esta reservado en este viaje, no se crea otra reserva.
    t_pick = time.monotonic()
    chk = asyncio.create_task(asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-GetReservations", {"tripId": trip["tripId"]}))
    async for ping in _until(chk):
        yield ping
    got = chk.result()
    dup = find_duplicate_flight(got.get("reservations") if isinstance(got, dict) and not got.get("error") else None, flight)
    yield _step(pid, "pick", "done", detail=flight_line(flight) + (" · ya está reservado" if dup else ""), ms=_ms(t_pick))
    if dup:
        st = STATUS_ES.get(str(dup.get("status")), str(dup.get("status") or "Simulada"))
        yield _step(pid, "reserve", "skipped", detail=f"Ya existe esa reserva ({st}); no se duplicó")
        state.update(ok=True, text=(f"Ese vuelo ({flight.get('airline') or ''} {flight.get('flight') or ''}, sale {flight.get('depart') or '--:--'}) "
                                    f"ya está reservado en este viaje con estado *{st}*, así que no creé otra reserva. "
                                    "Si quieres, elige otra opción de la lista o revisa tus reservas.").replace("  ", " "),
                     suggestions=[{"label": "Ver mis reservas", "icon": "ticket", "goto": "reservations"}])
        yield {"type": "plan_done", "plan": pid, "ok": True, "ms": _ms(t_all)}
        return
    t0 = time.monotonic()
    yield _step(pid, "reserve", "running", detail="Guardando la reserva…")
    rid = f"{pid}-r"
    yield {"type": "tool_start", "name": "create_reservation", "id": rid, "step": "reserve"}
    details = {"airline": flight.get("airline"), "flight": flight.get("flight"), "depart": flight.get("depart"),
               "arrive": flight.get("arrive"), "price_usd": price, "stops": flight.get("stops"),
               "origin": origin, "destination": dest, "date": trip.get("startDate") or flight.get("date"),
               "travelers": travelers, "total_usd": round(float(price) * travelers, 2) if isinstance(price, (int, float)) else None}
    details = {k: v for k, v in details.items() if v is not None}
    task = asyncio.create_task(asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-CreateReservation",
                                                 {"tripId": trip["tripId"], "type": "VUELO", "details": details}))
    async for ping in _until(task):
        yield ping
    res = task.result()
    if _is_ok_lambda("reservationId")(res):
        yield {"type": "tool_result", "id": rid, "name": "create_reservation", "status": "success", "data": res, "step": "reserve"}
        yield _step(pid, "reserve", "done", detail="Reserva guardada con estado Simulada", ms=_ms(t0))
        ptxt = f", US${price:g} por persona" if isinstance(price, (int, float)) else ""
        total = f" Para {travelers} viajeros son US${details['total_usd']:g} en total (solo ida)." if "total_usd" in details else ""
        state["text"] = (f"Listo, reservé de forma **simulada** el vuelo {flight.get('airline') or ''} {flight.get('flight') or ''} "
                         f"de {origin} a {dest} (sale {flight.get('depart') or '--:--'}{ptxt}).{total} "
                         "No se cobró nada: la reserva quedó guardada con estado *Simulada* y la ves en la sección **Reservas**.").replace("  ", " ")
        state["ok"] = True
    else:
        print(f"[reserva] create_reservation fallo: {str(res)[:200]}")
        yield {"type": "tool_result", "id": rid, "name": "create_reservation", "status": "error",
               "data": {"error": "No se pudo guardar la reserva."}, "step": "reserve"}
        yield _step(pid, "reserve", "error", detail="No se pudo guardar la reserva.", ms=_ms(t0),
                    retry=f"Reserva el vuelo {flight.get('airline')} {flight.get('flight')} de las {flight.get('depart')} para este viaje.")
        state["text"] = "No pude guardar la reserva en este momento. Puedes intentarlo de nuevo con el botón Reintentar."
    state["suggestions"] = [{"label": "Ver mis reservas", "icon": "ticket", "goto": "reservations"}] if state["ok"] else []
    yield {"type": "plan_done", "plan": pid, "ok": state["ok"], "ms": _ms(t_all)}


# --------------------------------------------------------------------------
# Reservas: estados, etiquetas y deteccion de duplicados
# --------------------------------------------------------------------------
STATUS_ES = {"SIMULADA": "Simulada", "PENDIENTE": "Pendiente", "CONFIRMADA": "Confirmada"}


def find_duplicate_flight(rows, flight: dict) -> Optional[dict]:
    """Reserva de VUELO ya guardada con el mismo vuelo (aerolinea, codigo, salida y, si ambos lo traen, llegada)."""
    def k(v):
        return norm(str(v)) if v is not None else ""
    for r in rows or []:
        if not isinstance(r, dict) or r.get("type") != "VUELO":
            continue
        d = r.get("details") if isinstance(r.get("details"), dict) else {}
        if (k(d.get("airline")), k(d.get("flight")), k(d.get("depart"))) != (k(flight.get("airline")), k(flight.get("flight")), k(flight.get("depart"))):
            continue
        if d.get("arrive") and flight.get("arrive") and k(d.get("arrive")) != k(flight.get("arrive")):
            continue
        return r
    return None


def res_label(r: dict) -> str:
    d = r.get("details") if isinstance(r.get("details"), dict) else {}
    t = r.get("type")
    if t == "VUELO":
        who = " ".join(str(x) for x in (d.get("airline"), d.get("flight")) if x)
        hour = f" ({d.get('depart')})" if d.get("depart") else ""
        return f"Vuelo {who}{hour}".replace("  ", " ").strip()
    name = d.get("name") or d.get("title") or d.get("hotel") or d.get("activity")
    return f"{'Hotel' if t == 'HOTEL' else 'Actividad'}{' ' + str(name) if name else ''}"


# --------------------------------------------------------------------------
# Eliminar reservas: el CODIGO decide cual se borra (el modelo borraba todas ante la duda)
# --------------------------------------------------------------------------
_DEL_STEPS = [
    {"id": "find", "label": "Buscar tus reservas", "agent": "Tu cuenta", "tech": "Tool get_reservations · Lambda → DynamoDB"},
    {"id": "delete", "label": "Eliminar la reserva", "agent": "Tu cuenta", "tech": "Tool delete_reservation · Lambda → DynamoDB"},
]
_DEL_ALL = re.compile(r"\b(todas|todos|todo)\b")
_DEL_LAST = re.compile(r"\b(ultim[oa]s?|reciente|recien|acabo|que hice|que hize|que cree|anterior)\b")
_DEL_KINDS = (("VUELO", re.compile(r"\b(vuelos?|pasajes?|tiquetes?|boletos?)\b")),
              ("HOTEL", re.compile(r"\bhotel(es)?\b")),
              ("ACTIVIDAD", re.compile(r"\b(actividad(es)?|tours?|excursion(es)?)\b")))
ASK_MARKER = "cual elimino"          # el ultimo mensaje del asistente lo contiene cuando pregunto cual borrar


def sort_reservations(rows) -> list:
    return sorted([r for r in rows or [] if isinstance(r, dict)], key=lambda r: str(r.get("createdAt") or ""))


def choose_reservations(text: str, rows) -> tuple:
    """Que reserva(s) quiere borrar el usuario. -> ('one', fila) | ('all', filas) | ('ask', filas) | ('nomatch', filas) | ('none', [])
    Prioridad: 'todas' > numero de la lista > tipo/codigo/aerolinea/hora > 'la ultima' / 'la que hice' > preguntar."""
    rows = sort_reservations(rows)
    if not rows:
        return "none", []
    t = norm(text)
    if _DEL_ALL.search(t):
        return "all", rows
    if len(t.split()) <= 6:
        m = re.search(r"(?<![a-z\d/:.$])(\d{1,2})(?![\d:/.a-z])", t)
        if m:
            n = int(m.group(1))
            return ("one", rows[n - 1]) if 1 <= n <= len(rows) else ("ask", rows)
    cands = rows
    kinds = [k for k, rx in _DEL_KINDS if rx.search(t)]
    if kinds:
        cands = [r for r in cands if r.get("type") in kinds]
        if not cands:
            return "nomatch", rows
    narrowed = []
    for r in cands:
        d = r.get("details") if isinstance(r.get("details"), dict) else {}
        code, air, dep = norm(d.get("flight")), norm(d.get("airline")).split(" ")[0], str(d.get("depart") or "")
        if (code and len(code) >= 3 and code in t) or (air and len(air) >= 3 and air in t) or \
                (dep and re.search(rf"(?<!\d){re.escape(dep)}(?!\d)", t)):
            narrowed.append(r)
    if narrowed:
        cands = narrowed
    if len(cands) == 1:
        return "one", cands[0]
    if _DEL_LAST.search(t):
        return "one", cands[-1]
    return "ask", rows


def reservations_list_text(rows) -> str:
    return "\n".join(f"{i}. {res_label(r)} · {STATUS_ES.get(str(r.get('status')), r.get('status') or '')}" for i, r in enumerate(rows, 1))


def delete_suggestions(rows, trip_id=None) -> list:
    items = [{"label": f"Eliminar {res_label(r)}", "icon": "trash",
              "action": {"type": "delete_reservation", "tripId": r.get("tripId"), "reservationId": r.get("reservationId")}}
             for r in rows[:4]]
    if len(rows) > 1:
        items.append({"label": "Eliminar todas", "icon": "trash", "action": {"type": "delete_reservation", "tripId": trip_id, "all": True}})
    return items


def _public_row(r: dict) -> dict:
    return {k: v for k, v in r.items() if not str(k).startswith("_")}


async def run_delete(prompt: str, deps: Deps, state: dict, trip: Optional[dict] = None, user_id: str = "", sel: Optional[dict] = None):
    """Elimina reservas con una linea de tiempo de dos pasos (buscar -> eliminar).
    `sel` (boton del navegador): {"reservationId": ...} o {"all": True}; sin `sel` se interpreta el texto."""
    pid = "plan-" + uuid.uuid4().hex[:8]
    t_all = time.monotonic()
    state.update(ok=False, text="", suggestions=[])
    retry_text = prompt if prompt else "Elimina mi reserva."
    yield {"type": "plan", "id": pid, "title": "Eliminar reservas", "steps": [dict(s) for s in _DEL_STEPS]}
    yield _step(pid, "find", "running", detail="Consultando tus reservas…")
    t0 = time.monotonic()
    fid = f"{pid}-g"
    yield {"type": "tool_start", "name": "get_reservations", "id": fid, "step": "find"}

    async def load():
        trips = [trip] if isinstance(trip, dict) and trip.get("tripId") else None
        if trips is None:
            tr = await asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-GetTrips", {"userId": user_id})
            lst = tr.get("trips") if isinstance(tr, dict) and not tr.get("error") else []
            trips = sorted([t for t in lst if isinstance(t, dict) and t.get("tripId")], key=lambda t: str(t.get("createdAt") or ""), reverse=True)[:10]
        results = await asyncio.gather(*[asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-GetReservations", {"tripId": t["tripId"]}) for t in trips])
        rows, failed = [], 0
        for t, res in zip(trips, results):
            if isinstance(res, dict) and isinstance(res.get("reservations"), list) and not res.get("error"):
                rows += [dict(r, _trip=t) for r in res["reservations"] if isinstance(r, dict)]
            else:
                failed += 1
        return rows, failed, trips

    task = asyncio.ensure_future(load())
    async for ping in _until(task):
        yield ping
    rows, failed, trips = task.result()
    rows = sort_reservations(rows)
    scope_trip_id = trips[0]["tripId"] if len(trips) == 1 else None
    if failed and not rows:
        yield {"type": "tool_result", "id": fid, "name": "get_reservations", "status": "error",
               "data": {"error": "No se pudieron consultar las reservas."}, "step": "find"}
        yield _step(pid, "find", "error", detail="No pude consultar tus reservas ahora.", ms=_ms(t0), retry=retry_text)
        yield _step(pid, "delete", "skipped", detail="No se pudo continuar")
        state["text"] = "No pude consultar tus reservas en este momento. Puedes intentarlo de nuevo con el botón Reintentar."
        yield {"type": "plan_done", "plan": pid, "ok": False, "ms": _ms(t_all)}
        return
    yield {"type": "tool_result", "id": fid, "name": "get_reservations", "status": "success",
           "data": {"reservations": [_public_row(r) for r in rows]}, "step": "find"}
    yield _step(pid, "find", "done", detail=(f"{len(rows)} reserva{'s' if len(rows) != 1 else ''}" if rows else "Sin reservas"), ms=_ms(t0))

    # --- decidir cual(es)
    if sel and sel.get("all"):
        kind, target = ("all", rows) if rows else ("none", [])
    elif sel and sel.get("reservationId"):
        hit = [r for r in rows if r.get("reservationId") == sel["reservationId"]]
        kind, target = ("one", hit[0]) if hit else ("gone", rows)
    else:
        kind, target = choose_reservations(prompt, rows)

    if kind == "none":
        yield _step(pid, "delete", "skipped", detail="No hay reservas que eliminar")
        state.update(ok=True, text="No tienes reservas guardadas para eliminar.")
    elif kind == "gone":
        yield _step(pid, "delete", "skipped", detail="Esa reserva ya no existe")
        state.update(ok=True, text="Esa reserva ya no existe (puede que ya la hayas eliminado).",
                     suggestions=[{"label": "Ver mis reservas", "icon": "ticket", "goto": "reservations"}])
    elif kind in ("ask", "nomatch"):
        yield _step(pid, "delete", "skipped", detail="Necesito que elijas cuál")
        head = ("No encontré una reserva de ese tipo. Estas son las que tienes:" if kind == "nomatch"
                else f"Tienes {len(rows)} reservas y no sé cuál quieres eliminar:")
        state.update(ok=True, text=f"{head}\n\n{reservations_list_text(rows)}\n\n¿Cuál elimino? Toca un botón o escribe el número, «la última» o «todas».",
                     suggestions=delete_suggestions(rows, scope_trip_id))
    else:
        targets = target if kind == "all" else [target]
        yield _step(pid, "delete", "running", label="Eliminar las reservas" if kind == "all" else None,
                    detail=f"Eliminando {len(targets)} reserva{'s' if len(targets) != 1 else ''}…")
        t1 = time.monotonic()
        deleted, failed_n = [], 0
        last_ev = None
        for i, r in enumerate(targets):
            did = f"{pid}-d{i}"
            yield {"type": "tool_start", "name": "delete_reservation", "id": did, "step": "delete"}
            dtask = asyncio.create_task(asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-DeleteReservation",
                                                          {"tripId": r.get("tripId"), "reservationId": r.get("reservationId")}))
            async for ping in _until(dtask):
                yield ping
            res = dtask.result()
            if isinstance(res, dict) and res.get("deleted") and not res.get("error"):
                deleted.append(r)
                last_ev = {"type": "tool_result", "id": did, "name": "delete_reservation", "status": "success",
                           "data": {"deleted": True, "tripId": r.get("tripId"), "reservationId": r.get("reservationId")}, "step": "delete"}
            else:
                failed_n += 1
                print(f"[eliminar] delete_reservation fallo: {str(res)[:200]}")
                last_ev = {"type": "tool_result", "id": did, "name": "delete_reservation", "status": "error",
                           "data": {"error": "No se pudo eliminar la reserva."}, "step": "delete"}
            yield last_ev
        left = len(rows) - len(deleted)
        if deleted and not failed_n:
            yield _step(pid, "delete", "done", detail=(f"{len(deleted)} reservas eliminadas" if len(deleted) > 1 else "Reserva eliminada"), ms=_ms(t1))
            what = f"las {len(deleted)} reservas" if len(deleted) > 1 else f"la reserva **{res_label(deleted[0])}**"
            rest = (f" Te quedan {left} reserva{'s' if left != 1 else ''}." if left else " Ya no tienes reservas guardadas.")
            state.update(ok=True, text=f"Listo, eliminé {what}.{rest}",
                         suggestions=[{"label": "Ver mis reservas", "icon": "ticket", "goto": "reservations"}] if left else [])
        else:
            yield _step(pid, "delete", "error", detail=("No se pudo eliminar" if not deleted else f"Se eliminaron {len(deleted)}, fallaron {failed_n}"),
                        ms=_ms(t1), retry=retry_text)
            state["text"] = ("No pude eliminar la reserva en este momento. Puedes intentarlo de nuevo con el botón Reintentar." if not deleted
                             else f"Eliminé {len(deleted)} reserva(s), pero {failed_n} no se pudieron eliminar. Intenta de nuevo con Reintentar.")
    yield {"type": "plan_done", "plan": pid, "ok": state.get("ok", False) or False, "ms": _ms(t_all)}




# --------------------------------------------------------------------------
# Cambios sobre un viaje ya guardado (dias, presupuesto, viajeros, fechas, ciudades).
# Los calcula el CODIGO: el agente libre, ante "dejalo en 3 dias", recalculaba el presupuesto con vuelos en $0
# y dejaba el itinerario de 4 dias.
# --------------------------------------------------------------------------
UPDATE_ASK_MARKER = "dime el nuevo valor"

_MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
           "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
_MONTH_RX = "|".join(_MONTHS)
_CHANGE_VERB = re.compile(r"\b(cambi\w*|modific\w*|actualiz\w*|ajust\w*|reduc\w*|aument\w*|ampli\w*|extiend\w*|extend\w*|alarg\w*|"
                          r"acort\w*|agreg\w*|anad\w*|quit\w*|sub[ae]|baj[ae]|que sean|que fuera|que quede|en vez de|en lugar de|dejal\w*|dejemos\w*)\b")
_SOFT_CHANGE = re.compile(rf"\b(?:mejor|ahora|nada mas|solamente)\b[^.?!]*?\b(?:{_NUMRX}|\d[\d.,]*)\s*(?:dias|noches|personas|viajeros|pasajeros|millones|mil)\b")
_CHANGE_NOUN = re.compile(r"\b(dias|noches|presupuesto|personas|viajeros|pasajeros|fechas?|salida|regreso|destino|origen|plata|dinero)\b")
_DATE_DECL = re.compile(r"\b(?:salgo|salimos|saldre|saldremos|salir|viajo|viajamos|viajar|regreso|regresamos|regresar|vuelvo|volver)\s+(?:el|del|para el)\s+\d{1,2}\b")
_DECLARATIVE = re.compile(rf"\b(?:somos|seremos|viajamos|vamos)\s+{_NUMRX}\b|\bpresupuesto\s+(?:ahora\s+|final\s+)?(?:es|sera|seria|de)\b")


def _mk_date(d, m, y, today: date) -> Optional[date]:
    try:
        if y is None:
            dt = date(today.year, m, d)
            return dt if dt >= today else date(today.year + 1, m, d)
        y = int(y)
        return date(y + 2000 if y < 100 else y, m, d)
    except ValueError:
        return None


def parse_dates(t: str, today: date) -> list:
    """Fechas escritas en el texto normalizado ('10 de diciembre', 'del 10 al 13 de diciembre', '10/12/2026'), en orden."""
    m = re.search(rf"\bdel\s+(\d{{1,2}})\s+al\s+(\d{{1,2}})\s+de\s+({_MONTH_RX})(?:\s+(?:de\s+)?(\d{{4}}))?", t)
    if m:
        a, b = _mk_date(int(m.group(1)), _MONTHS[m.group(3)], m.group(4), today), _mk_date(int(m.group(2)), _MONTHS[m.group(3)], m.group(4), today)
        return [d for d in (a, b) if d]
    found = []
    for m in re.finditer(rf"\b(\d{{1,2}})\s+de\s+({_MONTH_RX})(?:\s+(?:de\s+)?(\d{{4}}))?", t):
        found.append((m.start(), _mk_date(int(m.group(1)), _MONTHS[m.group(2)], m.group(3), today)))
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        found.append((m.start(), _mk_date(int(m.group(1)), int(m.group(2)), m.group(3), today)))
    return [d for _, d in sorted(found) if d]


def request_from_trip(trip: dict) -> Optional[TripRequest]:
    """Viaje guardado (el que el navegador manda como 'viaje activo') -> TripRequest. None si le faltan datos basicos."""
    try:
        start = datetime.strptime(str(trip["startDate"]), "%d/%m/%Y").date()
        end = datetime.strptime(str(trip["endDate"]), "%d/%m/%Y").date()
        origin, dest = str(trip["origin"]).strip(), str(trip["destination"]).strip()
        travelers = int(trip.get("travelers") or 1)
    except (KeyError, TypeError, ValueError):
        return None
    if not origin or not dest or end < start:
        return None
    budget = trip.get("budget")
    budget = int(budget) if isinstance(budget, (int, float)) and budget > 0 else 0
    return TripRequest(canon_city(origin) or origin, canon_city(dest) or dest, (end - start).days + 1, max(travelers, 1),
                       budget, start, end, [], "reglas")


def parse_changes(text: str, cur: TripRequest, today: date, followup: bool = False) -> Optional[dict]:
    """Lee un pedido de cambio sobre el viaje `cur`. -> None si el mensaje no es un cambio; si lo es:
    {"changes": {...}, "ask": str|None}. Claves de changes: days, start, end, travelers, budget, origin, destination."""
    t = norm(text)
    verb = bool(_CHANGE_VERB.search(t) or _SOFT_CHANGE.search(t))
    noun = bool(_CHANGE_NOUN.search(t))
    decl = bool(_DECLARATIVE.search(t) or _DATE_DECL.search(t))
    ch: dict = {}

    # dias: relativo ("2 dias mas", "alarga el viaje 2 dias") o absoluto ("que sean 3 dias")
    rel = re.search(rf"\b({_NUMRX})\s+(?:dias?|noches?)\s+(mas|menos|adicional(?:es)?|extra)\b", t)
    verb_rel = re.search(rf"\b(alarg\w*|extiend\w*|extend\w*|ampli\w*|agreg\w*|anad\w*|suma\w*|aument\w*|acort\w*|reduc\w*|quita\w*)\b"
                         rf"(?P<mid>[^.?!]*?)\b(?P<n>{_NUMRX})\s*(?:dias?|noches?)", t)
    if rel:
        n = _int(rel.group(1))
        if n:
            ch["days"] = cur.days + (-n if rel.group(2) == "menos" else n)
    elif verb_rel and not re.search(r"\b(?:a|en|hasta|de|por)\s*$", verb_rel.group("mid")) and _int(verb_rel.group("n")):
        n = _int(verb_rel.group("n"))
        ch["days"] = cur.days + (-n if re.match(r"(acort|reduc|quita)", verb_rel.group(1)) else n)
    else:
        d = parse_days(t)
        if d:
            ch["days"] = d

    # viajeros: relativo ("agrega 2 personas", "2 personas mas") o absoluto ("somos 3")
    m_add = re.search(rf"\b(agreg\w*|anad\w*|sum\w*|incluy\w*|quit\w*)\b[^.?!]*?\b({_NUMRX})\s+(?:personas?|viajeros?|amigos?|pasajeros?)", t)
    m_more = re.search(rf"\b({_NUMRX})\s+(?:personas?|viajeros?|amigos?|pasajeros?)\s+(mas|menos)\b", t)
    if m_add and _int(m_add.group(2)):
        ch["travelers"] = cur.travelers + (-1 if m_add.group(1).startswith("quit") else 1) * _int(m_add.group(2))
    elif m_more and _int(m_more.group(1)):
        ch["travelers"] = cur.travelers + (-1 if m_more.group(2) == "menos" else 1) * _int(m_more.group(1))
    else:
        tr = parse_travelers(t)
        if tr:
            ch["travelers"] = tr

    # presupuesto: relativo ("sube el presupuesto en 500 mil") o absoluto
    b = parse_budget(text)
    if b:
        mrel = re.search(r"\b(aument\w*|sub[ae]|agreg\w*|anad\w*|suma\w*|reduc\w*|baj[ae]|disminuy\w*|quit\w*)\b[^.?!]*\bpresupuesto\b[^.?!]*\b(?:en|por)\b", t)
        if mrel and cur.budget_cop:
            ch["budget"] = cur.budget_cop + (-b if re.match(r"(reduc|baj|disminuy|quit)", mrel.group(1)) else b)
        else:
            ch["budget"] = b

    # fechas
    ds = parse_dates(t, today)
    if ds and (verb or noun or decl or followup):
        if len(ds) >= 2:
            ch["start"], ch["end"] = ds[0], ds[1]
        elif re.search(r"\b(regreso|regresar|volver|vuelta|retorno)\b", t) and not re.search(r"\b(salida|salgo|salir|viajo|partir|parto|inicio|empez\w*)\b", t):
            ch["end"] = ds[0]
        else:
            ch["start"] = ds[0]

    # ciudades: solo si el mensaje habla explicitamente del destino/origen (evita confundir "agrega una visita a Medellin")
    cities = [(a, c) for a, _, c in scan_cities(text)]
    wants_dest = bool(re.search(r"\bdestino\b|en vez de|en lugar de|\bmejor\s+(?:a|en|para|vamos a|ir a)\b|\bcambi\w*\s+(?:el\s+)?viaje\s+a\b|\bcambi\w*\s+a\b", t))
    wants_org = bool(re.search(r"\borigen\b|\bsalida\s+(?:desde|de)\b|\bsalg\w*\s+de\b|\bsaliendo\s+de\b|\bsalir\s+de\b", t))
    ask = None
    for pos, c in cities:
        before = t[max(0, pos - 28):pos]
        is_org = bool(re.search(r"(origen|salida|salgo de|saliendo de|salir de|desde)\s*(?:a|de|por|en|:)?\s*$", before))
        if is_org and wants_org and norm(c) != norm(cur.origin):
            ch["origin"] = c
        elif not is_org and wants_dest and norm(c) != norm(cur.destination) and norm(c) != norm(cur.origin):
            ch["destination"] = c
    if wants_dest and "destination" not in ch:
        cty = country_of(_dest_word(t)) if _dest_word(t) else None
        if cty:
            ask = f"Para cambiar el destino necesito la ciudad concreta en {cty[0]} (por ejemplo, {cty[1]})."

    intent = (verb and (noun or bool(ch))) or decl or (followup and bool(ch))
    if not intent:
        return None
    if not ch and not ask:
        ask = (f"Claro. Para cambiarlo, {UPDATE_ASK_MARKER}: por ejemplo «3 días», «presupuesto de 4 millones», «somos 3 personas» "
               f"o «salgo el 10 de diciembre». Hoy tu viaje a {cur.destination} tiene {cur.days} {'día' if cur.days == 1 else 'días'}, "
               f"{cur.travelers} {'viajero' if cur.travelers == 1 else 'viajeros'} y {cop_fmt(cur.budget_cop) if cur.budget_cop else 'sin presupuesto definido'}.")
    return {"changes": ch, "ask": ask}


def apply_changes(cur: TripRequest, ch: dict, today: date) -> tuple:
    """-> (nuevo TripRequest | None, notas legibles, error | None, sin_cambio: lista de frases de lo que ya era asi)."""
    notes, same = [], []
    origin, dest, travelers, budget = cur.origin, cur.destination, cur.travelers, cur.budget_cop
    start, end, days = cur.start, cur.end, cur.days
    if "origin" in ch and ch["origin"] != origin:
        notes.append(f"Salida: {origin} → {ch['origin']}"); origin = ch["origin"]
    if "destination" in ch and ch["destination"] != dest:
        notes.append(f"Destino: {dest} → {ch['destination']}"); dest = ch["destination"]
    if origin == dest:
        return None, [], "El destino no puede ser igual a la ciudad de salida.", []
    if "travelers" in ch:
        n = int(ch["travelers"])
        if not 1 <= n <= 20:
            return None, [], "El número de viajeros debe estar entre 1 y 20.", []
        if n != travelers:
            notes.append(f"Viajeros: {travelers} → {n}"); travelers = n
        else:
            same.append(f"{n} {'viajero' if n == 1 else 'viajeros'}")
    if "budget" in ch:
        b = int(ch["budget"])
        if not 50_000 <= b <= 5_000_000_000:
            return None, [], "El presupuesto debe estar entre $50.000 y $5.000.000.000.", []
        if b != budget:
            notes.append(f"Presupuesto: {cop_fmt(budget) if budget else 'sin definir'} → {cop_fmt(b)}"); budget = b
        else:
            same.append(f"un presupuesto de {cop_fmt(b)}")
    new_start, new_end, new_days = start, end, days
    if "start" in ch:
        new_start = ch["start"]
        if "end" in ch:
            new_end = ch["end"]
        else:
            new_end = new_start + timedelta(days=days - 1)
    elif "end" in ch:
        new_end = ch["end"]
    if "days" in ch:
        new_end = new_start + timedelta(days=int(ch["days"]) - 1)
    new_days = (new_end - new_start).days + 1
    if new_start != start and (new_start < today or new_start > today + timedelta(days=400)):
        return None, [], "La fecha de salida debe ser desde hoy y dentro de los próximos 13 meses (escríbela como 10/12/2026).", []
    if new_days < 1:
        return None, [], "La fecha de regreso no puede ser anterior a la de salida.", []
    if new_days > 14:
        return None, [], "Puedo planear viajes de hasta 14 días.", []
    if new_start != start:
        notes.append(f"Salida: {dmy(start)} → {dmy(new_start)}")
    if new_days != days:
        notes.append(f"Duración: {days} → {new_days} {'día' if new_days == 1 else 'días'}")
    elif "days" in ch:
        same.append(f"{days} {'día' if days == 1 else 'días'}")
    if new_end != end and new_days == days and new_start == start:
        notes.append(f"Regreso: {dmy(end)} → {dmy(new_end)}")
    if not budget:
        return None, [], "Para recalcular necesito tu presupuesto en pesos (por ejemplo, «presupuesto de 4 millones»).", []
    req = TripRequest(origin, dest, new_days, travelers, budget, new_start, new_end, [], "reglas")
    return req, notes, None, same


STEPS_UPDATE = [
    {"id": "understand", "label": "Entender el cambio", "agent": "Asistente", "tech": "Orchestrator · reglas"},
    {"id": "weather", "label": "Revisar el clima", "agent": "Agente de clima", "tech": "Weather Agent · A2A · MCP Open-Meteo"},
    {"id": "flights", "label": "Revisar los vuelos", "agent": "Agente de vuelos", "tech": "Flight Agent · A2A · MCP Kiwi"},
    {"id": "budget", "label": "Recalcular el presupuesto", "agent": "Asistente", "tech": "Orchestrator · cálculo determinista"},
    {"id": "itinerary", "label": "Ajustar el itinerario", "agent": "Asistente", "tech": "Orchestrator · Nova Pro + catálogo de lugares"},
    {"id": "save_trip", "label": "Actualizar el viaje", "agent": "Tu cuenta", "tech": "Tool update_trip · Lambda → DynamoDB"},
    {"id": "save_itinerary", "label": "Actualizar el itinerario", "agent": "Tu cuenta", "tech": "Tool update_itinerary · Lambda → DynamoDB"},
]


def update_plan(cur: TripRequest, req: TripRequest, trip: dict, itinerary_id: Optional[str], flights: Optional[list], notes: list) -> dict:
    """Decide que hay que volver a consultar: el clima solo si cambian destino o fechas (o se alarga el viaje), los vuelos solo
    si cambia la ruta o la fecha de salida (o no hay vuelos a la mano), y el itinerario solo si cambian dias, fechas o ciudades."""
    route_changed = (cur.origin, cur.destination) != (req.origin, req.destination)
    dates_changed = cur.start != req.start or req.end > cur.end
    flights = [f for f in (flights or []) if isinstance(f, dict)]
    return {"old": cur, "req": req, "notes": notes, "trip": trip, "itinerary_id": itinerary_id, "flights": flights,
            "weather": route_changed or dates_changed,
            "flights_redo": route_changed or cur.start != req.start or not cheapest_flight(flights),
            "itinerary": route_changed or cur.start != req.start or cur.days != req.days}


async def run_update(base: dict, deps: Deps, state: dict):
    """Actualiza el viaje activo con linea de tiempo. Resultado en `state` (igual que run_plan)."""
    pid = "plan-" + uuid.uuid4().hex[:8]
    t_all = time.monotonic()
    req, notes, trip0 = base["req"], base["notes"], base["trip"]
    state.update(ok=False, missing=[], problems=[])
    problems = state["problems"]
    yield {"type": "plan", "id": pid, "title": "Actualizando tu viaje", "steps": [dict(s) for s in STEPS_UPDATE]}
    yield _step(pid, "understand", "done", detail="; ".join(notes) or req.interpretation(), ms=1, tech="Orchestrator · reglas",
                title=f"{req.origin} → {req.destination}")
    state["req"] = req
    d0, d1 = dmy(req.start), dmy(req.end)

    # clima y vuelos: solo lo que cambio (en paralelo) -------------------------------------------------
    wid, fid = f"{pid}-w", f"{pid}-f"
    pending = set()

    async def timed(kind, fn, ok, *args):
        t = time.monotonic()
        res, attempts = await asyncio.to_thread(_retry, fn, ok, *args)
        return kind, res, attempts, _ms(t)

    weather, flights_list, flights_status = {}, [], "error"
    if base["weather"]:
        yield _step(pid, "weather", "running", label=f"Clima en {req.destination}", detail="Consultando el pronóstico…")
        yield {"type": "tool_start", "name": "ask_weather", "id": wid, "step": "weather"}
        pending.add(asyncio.create_task(timed("weather", deps.weather, _is_ok_weather, req.destination, d0, d1)))
    else:
        yield _step(pid, "weather", "skipped", detail="Sin cambios en el destino ni en las fechas: se mantiene el pronóstico anterior.")
    if base["flights_redo"]:
        yield _step(pid, "flights", "running", label=f"Vuelos {req.origin} → {req.destination}", detail="Buscando vuelos reales…")
        yield {"type": "tool_start", "name": "ask_flights", "id": fid, "step": "flights"}
        pending.add(asyncio.create_task(timed("flights", deps.flights, _is_ok_flights, req.origin, req.destination, d0)))
    else:
        flights_list = sort_flights(base["flights"])
        flights_status = "ok"
        kept = cheapest_flight(flights_list)
        yield _step(pid, "flights", "done", detail=f"Sin cambios en la ruta ni en la salida: se mantiene el vuelo más barato, US${kept['price_usd']:g} ({kept.get('airline') or 'aerolínea'}).")
    while pending:
        done, pending = await asyncio.wait(pending, timeout=8, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            yield {"type": "ping"}
            continue
        for tk in done:
            kind, res, attempts, ms = tk.result()
            if kind == "weather":
                if _is_ok_weather(res):
                    weather = friendly_weather(res)
                    weather.setdefault("city", req.destination)
                    yield {"type": "tool_result", "id": wid, "name": "ask_weather", "status": "success", "data": weather, "step": "weather"}
                    yield _step(pid, "weather", "done", detail=weather_line(weather) or "Pronóstico listo", ms=ms, attempt=attempts)
                else:
                    print(f"[update] clima fallo: {str(res)[:200]}")
                    msg = friendly_error("clima", res)
                    problems.append("clima")
                    yield {"type": "tool_result", "id": wid, "name": "ask_weather", "status": "error", "data": {"error": msg}, "step": "weather"}
                    yield _step(pid, "weather", "error", detail=msg, ms=ms, attempt=attempts,
                                retry=f"Consulta de nuevo el clima de {req.destination} del {d0} al {d1}.")
            else:
                if _is_ok_flights(res):
                    fl = sort_flights(res.get("flights"))
                    flights_res = {**{k: v for k, v in res.items() if k != "flights"}, "origin": req.origin,
                                   "destination": req.destination, "date": d0, "flights": fl[:6]}
                    flights_list = flights_res["flights"]
                    flights_status = "ok" if fl else "empty"
                    best_ = cheapest_flight(fl)
                    detail = (f"{len(fl)} opciones · la más barata US${best_['price_usd']:g} ({best_.get('airline') or 'aerolínea'})"
                              if best_ else "Sin vuelos para esa ruta y fecha")
                    yield {"type": "tool_result", "id": fid, "name": "ask_flights", "status": "success", "data": flights_res, "step": "flights"}
                    yield _step(pid, "flights", "done", detail=detail, ms=ms, attempt=attempts)
                    if not fl:
                        problems.append("vuelos")
                else:
                    print(f"[update] vuelos fallo: {str(res)[:200]}")
                    msg = friendly_error("vuelos", res)
                    problems.append("vuelos")
                    yield {"type": "tool_result", "id": fid, "name": "ask_flights", "status": "error", "data": {"error": msg}, "step": "flights"}
                    yield _step(pid, "flights", "error", detail=msg, ms=ms, attempt=attempts,
                                retry=f"Busca de nuevo vuelos de {req.origin} a {req.destination} para el {d0}.")
    best = cheapest_flight(flights_list)

    # presupuesto: siempre, con el precio REAL del vuelo -------------------------------------------------
    t0 = time.monotonic()
    yield _step(pid, "budget", "running", detail="Sumando vuelos, hospedaje y comidas…")
    bid = f"{pid}-b"
    yield {"type": "tool_start", "name": "calculate_budget", "id": bid, "step": "budget"}
    budget = compute_budget(req.budget_cop, req.travelers, req.days, float(best["price_usd"]) if best else 0.0, True, deps.fx)
    if not best:
        budget["note"] = "No incluye vuelos: no hubo precios disponibles."
    yield {"type": "tool_result", "id": bid, "name": "calculate_budget", "status": "success", "data": budget, "step": "budget"}
    verdict = f"sobran {cop_fmt(budget['remainingCOP'])}" if budget["withinBudget"] else f"faltan {cop_fmt(-budget['remainingCOP'])}"
    yield _step(pid, "budget", "done", detail=f"Total estimado {cop_fmt(budget['totalEstimatedCOP'])} · {verdict}", ms=_ms(t0))

    # itinerario: solo si cambian dias, fechas o ciudades -------------------------------------------------
    days = None
    if base["itinerary"]:
        t0 = time.monotonic()
        yield _step(pid, "itinerary", "running", detail="Ajustando los días y los lugares…")
        iid = f"{pid}-i"
        yield {"type": "tool_start", "name": "plan_itinerary", "id": iid, "step": "itinerary"}
        catalog = places_for(req.destination)

        def design():
            assign = None
            if catalog:
                try:
                    assign = _llm_assign(deps.llm_text, req, catalog, weather)
                except Exception as exc:
                    print(f"[update] el LLM no repartio el itinerario, uso el catalogo: {exc}")
            return build_itinerary(req, best, assign)

        task = asyncio.create_task(asyncio.to_thread(design))
        async for ping in _until(task):
            yield ping
        days, source = task.result()
        n_act = sum(len(d["activities"]) for d in days)
        yield {"type": "tool_result", "id": iid, "name": "plan_itinerary", "status": "success", "data": {"days": days, "source": source}, "step": "itinerary"}
        yield _step(pid, "itinerary", "done", detail=f"{req.days} {'día' if req.days == 1 else 'días'} · {n_act} actividades · {source}", ms=_ms(t0))
    else:
        yield _step(pid, "itinerary", "skipped", detail="Sin cambios: el itinerario sigue igual.")

    # guardar: UpdateTrip (+ UpdateItinerary si cambio) ---------------------------------------------------
    t0 = time.monotonic()
    yield _step(pid, "save_trip", "running", detail="Guardando los cambios…")
    tid = f"{pid}-t"
    yield {"type": "tool_start", "name": "update_trip", "id": tid, "step": "save_trip"}
    payload = {"tripId": trip0["tripId"], "origin": req.origin, "destination": req.destination, "startDate": d0, "endDate": d1,
               "travelers": req.travelers, "budget": req.budget_cop}
    task = asyncio.create_task(asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-UpdateTrip", payload))
    async for ping in _until(task):
        yield ping
    trip = task.result()
    trip_ok = _is_ok_lambda("tripId")(trip)
    if trip_ok:
        state["trip"] = trip
        yield {"type": "tool_result", "id": tid, "name": "update_trip", "status": "success", "data": trip, "step": "save_trip"}
        yield _step(pid, "save_trip", "done", detail="Viaje actualizado en tu cuenta", ms=_ms(t0))
    else:
        print(f"[update] update_trip fallo: {str(trip)[:200]}")
        problems.append("guardado")
        yield {"type": "tool_result", "id": tid, "name": "update_trip", "status": "error", "data": {"error": "No se pudo actualizar el viaje."}, "step": "save_trip"}
        yield _step(pid, "save_trip", "error", detail="No se pudo actualizar el viaje. Puedes pedirme que lo intente de nuevo.", ms=_ms(t0),
                    retry=f"Cambia mi viaje a {req.destination} para que sea del {d0} al {d1}, con {req.travelers} viajeros y presupuesto de {req.budget_cop} pesos.")

    if days is None:
        yield _step(pid, "save_itinerary", "skipped", detail="Sin cambios en el itinerario.")
    elif not trip_ok:
        yield _step(pid, "save_itinerary", "skipped", detail="Se actualiza después de guardar el viaje.")
    else:
        t0 = time.monotonic()
        yield _step(pid, "save_itinerary", "running", detail="Guardando el itinerario…")
        sid = f"{pid}-s"
        it, itin_id = {}, base.get("itinerary_id")
        if itin_id:
            yield {"type": "tool_start", "name": "update_itinerary", "id": sid, "step": "save_itinerary"}
            task = asyncio.create_task(asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-UpdateItinerary",
                                                         {"tripId": trip["tripId"], "itineraryId": itin_id, "days": days}))
            async for ping in _until(task):
                yield ping
            it = task.result()
            if _is_ok_lambda("itineraryId")(it):
                state["itinerary_id"] = it["itineraryId"]
                yield {"type": "tool_result", "id": sid, "name": "update_itinerary", "status": "success", "data": it, "step": "save_itinerary"}
            else:
                print(f"[update] update_itinerary fallo, creo uno nuevo: {str(it)[:200]}")
                yield {"type": "tool_result", "id": sid, "name": "update_itinerary", "status": "error", "data": {"error": "No se pudo actualizar."}, "step": "save_itinerary"}
                itin_id = None
        if not itin_id:
            sid2 = f"{pid}-s2"
            yield {"type": "tool_start", "name": "create_itinerary", "id": sid2, "step": "save_itinerary"}
            task = asyncio.create_task(asyncio.to_thread(_once, deps.invoke_lambda, "TripPlanner-CreateItinerary",
                                                         {"tripId": trip["tripId"], "days": days}))
            async for ping in _until(task):
                yield ping
            it = task.result()
            if _is_ok_lambda("itineraryId")(it):
                state["itinerary_id"] = it["itineraryId"]
                yield {"type": "tool_result", "id": sid2, "name": "create_itinerary", "status": "success", "data": it, "step": "save_itinerary"}
            else:
                yield {"type": "tool_result", "id": sid2, "name": "create_itinerary", "status": "error", "data": {"error": "No se pudo guardar el itinerario."}, "step": "save_itinerary"}
        if state.get("itinerary_id"):
            yield _step(pid, "save_itinerary", "done", detail="Itinerario actualizado", ms=_ms(t0))
        else:
            problems.append("itinerario guardado")
            yield _step(pid, "save_itinerary", "error", detail="No se pudo actualizar el itinerario.", ms=_ms(t0))

    state.update(ok=True, cheapest=best, flights=flights_list, weather=weather, budget=budget, days_plan=days)
    state["facts"] = build_facts(req, weather, best, flights_list, flights_status, budget, days, trip if trip_ok else None,
                                 updated_notes=notes or ["sin cambios de fondo"], weather_kept=not base["weather"])
    state["suggestions"] = build_suggestions(req, best, state.get("trip") if trip_ok else None)
    yield {"type": "plan_done", "plan": pid, "ok": not problems, "ms": _ms(t_all)}
