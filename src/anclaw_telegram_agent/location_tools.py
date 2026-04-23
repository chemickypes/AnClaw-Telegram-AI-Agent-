import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_WMO_CODES: dict[int, str] = {
    0: "cielo sereno",
    1: "prevalentemente sereno",
    2: "parzialmente nuvoloso",
    3: "coperto",
    45: "nebbia",
    48: "nebbia gelata",
    51: "pioggerella leggera",
    53: "pioggerella moderata",
    55: "pioggerella intensa",
    61: "pioggia leggera",
    63: "pioggia moderata",
    65: "pioggia intensa",
    71: "neve leggera",
    73: "neve moderata",
    75: "neve intensa",
    77: "nevischio",
    80: "rovesci leggeri",
    81: "rovesci moderati",
    82: "rovesci intensi",
    85: "rovesci di neve",
    86: "rovesci di neve intensi",
    95: "temporale",
    96: "temporale con grandine",
    99: "temporale con grandine intensa",
}

_NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 8.0

_DAYS_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
_MONTHS_IT = [
    "", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]


def _fmt_date_it(date_str: str) -> str:
    """Converte '2026-04-24' in 'Venerdì 24 aprile'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = _DAYS_IT[dt.weekday()]
        return f"{day_name} {dt.day} {_MONTHS_IT[dt.month]}"
    except Exception:
        return date_str


def geocode_city(city: str) -> tuple[float, float, str] | None:
    """
    Converte il nome di una città in coordinate (lat, lon, nome_display).
    Ritorna None se la geocodifica fallisce.
    """
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(
                _NOMINATIM_SEARCH_URL,
                params={"q": city, "format": "json", "limit": 1, "accept-language": "it"},
                headers={"User-Agent": "AnClaw-TelegramBot/1.0"},
            )
            r.raise_for_status()
            results = r.json()
            if not results:
                return None
            first = results[0]
            return float(first["lat"]), float(first["lon"]), first.get("display_name", city)
    except Exception:
        logger.debug("geocode_city fallita per %r", city, exc_info=True)
        return None


def get_weather_forecast(city: str, days: int = 3) -> str:
    """
    Restituisce le previsioni meteo per una città per i prossimi giorni.

    Args:
        city: Nome della città o luogo (es. "Pordenone", "Roma", "Milano").
        days: Numero di giorni di previsione (1-7, default 3).

    Returns:
        Stringa con le previsioni giorno per giorno in italiano.
    """
    days = max(1, min(days, 7))

    geo = geocode_city(city)
    if geo is None:
        return f"Non ho trovato la posizione per '{city}'. Prova con un nome più preciso."

    lat, lon, display_name = geo

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(
                _OPEN_METEO_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": ",".join([
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "weather_code",
                        "precipitation_sum",
                        "precipitation_probability_max",
                        "wind_speed_10m_max",
                        "sunrise",
                        "sunset",
                    ]),
                    "timezone": "auto",
                    "forecast_days": days,
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        logger.debug("get_weather_forecast: Open-Meteo fallita", exc_info=True)
        return "Impossibile recuperare le previsioni meteo in questo momento. Riprova tra poco."

    daily = data.get("daily", {})
    tz_name = data.get("timezone", "UTC")
    dates = daily.get("time", [])

    def _fmt_time(iso: str) -> str:
        try:
            return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz_name)).strftime("%H:%M")
        except Exception:
            return iso[-5:] if iso else "N/D"

    city_short = display_name.split(",")[0].strip()
    lines = [f"Previsioni meteo per {city_short}:\n"]

    for i, date_str in enumerate(dates):
        code = (daily.get("weather_code") or [])[i] if i < len(daily.get("weather_code") or []) else -1
        desc = _WMO_CODES.get(code, "N/D").capitalize()
        t_max = (daily.get("temperature_2m_max") or [])[i] if i < len(daily.get("temperature_2m_max") or []) else None
        t_min = (daily.get("temperature_2m_min") or [])[i] if i < len(daily.get("temperature_2m_min") or []) else None
        prec = (daily.get("precipitation_sum") or [])[i] if i < len(daily.get("precipitation_sum") or []) else None
        prec_prob = (daily.get("precipitation_probability_max") or [])[i] if i < len(daily.get("precipitation_probability_max") or []) else None
        wind = (daily.get("wind_speed_10m_max") or [])[i] if i < len(daily.get("wind_speed_10m_max") or []) else None
        sunrise_raw = (daily.get("sunrise") or [])[i] if i < len(daily.get("sunrise") or []) else ""
        sunset_raw = (daily.get("sunset") or [])[i] if i < len(daily.get("sunset") or []) else ""

        label = "Oggi" if i == 0 else ("Domani" if i == 1 else _fmt_date_it(date_str))
        block = [f"**{label}** — {desc}"]
        if t_min is not None and t_max is not None:
            block.append(f"  Temperatura: {t_min}°C – {t_max}°C")
        if prec is not None and prec > 0:
            prob_str = f" (probabilità {prec_prob}%)" if prec_prob is not None else ""
            block.append(f"  Precipitazioni: {prec} mm{prob_str}")
        elif prec_prob is not None and prec_prob > 0:
            block.append(f"  Probabilità pioggia: {prec_prob}%")
        if wind is not None:
            block.append(f"  Vento max: {wind} km/h")
        if sunrise_raw and sunset_raw:
            block.append(f"  Alba {_fmt_time(sunrise_raw)} | Tramonto {_fmt_time(sunset_raw)}")

        lines.append("\n".join(block))

    return "\n\n".join(lines)


async def reverse_geocode(lat: float, lon: float) -> str:
    """Ritorna un indirizzo leggibile per le coordinate date, o stringa vuota se fallisce."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                _NOMINATIM_REVERSE_URL,
                params={"format": "json", "lat": lat, "lon": lon, "accept-language": "it"},
                headers={"User-Agent": "AnClaw-TelegramBot/1.0"},
            )
            r.raise_for_status()
            data = r.json()
            return data.get("display_name", "")
    except Exception:
        logger.debug("reverse_geocode fallita", exc_info=True)
        return ""


async def fetch_weather(lat: float, lon: float) -> dict:
    """
    Ritorna un dict con meteo attuale e orari alba/tramonto.
    Chiavi: temperature, humidity, wind_speed, weather_desc, sunrise, sunset.
    Ritorna dict vuoto se la chiamata fallisce.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                _OPEN_METEO_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "daily": "sunrise,sunset",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            r.raise_for_status()
            data = r.json()

        current = data.get("current", {})
        daily = data.get("daily", {})
        tz_name = data.get("timezone", "UTC")

        weather_code = current.get("weather_code", -1)
        weather_desc = _WMO_CODES.get(weather_code, f"codice {weather_code}")

        def _fmt_time(iso: str) -> str:
            try:
                dt = datetime.fromisoformat(iso).astimezone(ZoneInfo(tz_name))
                return dt.strftime("%H:%M")
            except Exception:
                return iso[-5:] if iso else "N/D"

        sunrises = daily.get("sunrise") or []
        sunsets = daily.get("sunset") or []
        sunrise = _fmt_time(sunrises[0]) if sunrises else "N/D"
        sunset = _fmt_time(sunsets[0]) if sunsets else "N/D"

        return {
            "temperature": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "weather_desc": weather_desc,
            "sunrise": sunrise,
            "sunset": sunset,
        }
    except Exception:
        logger.debug("fetch_weather fallita", exc_info=True)
        return {}


async def build_location_context(lat: float, lon: float) -> str:
    """
    Chiama reverse geocoding e meteo in parallelo e costruisce un blocco di testo
    pronto per essere iniettato nel prompt dell'agente.
    """
    import asyncio
    address_task = asyncio.create_task(reverse_geocode(lat, lon))
    weather_task = asyncio.create_task(fetch_weather(lat, lon))
    address, weather = await asyncio.gather(address_task, weather_task)

    parts: list[str] = [f"[POSIZIONE GPS: lat={lat:.5f}, lon={lon:.5f}]"]

    context_parts: list[str] = []
    if address:
        context_parts.append(f"Indirizzo: {address}")
    if weather:
        temp = weather.get("temperature")
        hum = weather.get("humidity")
        wind = weather.get("wind_speed")
        desc = weather.get("weather_desc", "")
        sunrise = weather.get("sunrise", "N/D")
        sunset = weather.get("sunset", "N/D")

        meteo_str = desc.capitalize()
        if temp is not None:
            meteo_str += f", {temp}°C"
        if hum is not None:
            meteo_str += f", umidità {hum}%"
        if wind is not None:
            meteo_str += f", vento {wind} km/h"

        context_parts.append(f"Meteo: {meteo_str}")
        context_parts.append(f"Alba: {sunrise} | Tramonto: {sunset}")

    if context_parts:
        parts.append("[CONTESTO LOCALE: " + " | ".join(context_parts) + "]")

    return "\n".join(parts)
