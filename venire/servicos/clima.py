"""Previsão do tempo pelo Open-Meteo (gratuito, sem chave)."""

from dataclasses import dataclass, field
from datetime import date

import httpx

API = "https://api.open-meteo.com/v1/forecast"

# Códigos WMO usados pelo Open-Meteo → descrição e emoji.
CODIGOS = {
    0: ("céu limpo", "☀️"), 1: ("poucas nuvens", "🌤️"), 2: ("parcialmente nublado", "⛅"),
    3: ("nublado", "☁️"), 45: ("neblina", "🌫️"), 48: ("neblina", "🌫️"),
    51: ("garoa fraca", "🌦️"), 53: ("garoa", "🌦️"), 55: ("garoa forte", "🌦️"),
    56: ("garoa congelante", "🌧️"), 57: ("garoa congelante", "🌧️"),
    61: ("chuva fraca", "🌧️"), 63: ("chuva", "🌧️"), 65: ("chuva forte", "🌧️"),
    66: ("chuva congelante", "🌧️"), 67: ("chuva congelante", "🌧️"),
    71: ("neve fraca", "🌨️"), 73: ("neve", "🌨️"), 75: ("neve forte", "🌨️"), 77: ("granizo fino", "🌨️"),
    80: ("pancadas de chuva", "🌦️"), 81: ("pancadas de chuva", "🌧️"), 82: ("pancadas fortes de chuva", "⛈️"),
    85: ("pancadas de neve", "🌨️"), 86: ("pancadas de neve", "🌨️"),
    95: ("trovoadas", "⛈️"), 96: ("trovoadas com granizo", "⛈️"), 97: ("trovoadas fortes", "⛈️"),
    99: ("trovoadas com granizo", "⛈️"),
}


@dataclass
class PrevisaoDia:
    dia: date
    minima: float
    maxima: float
    chance_chuva: int
    codigo: int
    chuva_por_hora: dict[int, int] = field(default_factory=dict)  # hora → % de chance

    @property
    def descricao(self) -> str:
        return CODIGOS.get(self.codigo, ("tempo variável", "🌡️"))[0]

    @property
    def emoji(self) -> str:
        return CODIGOS.get(self.codigo, ("", "🌡️"))[1]

    def resumo(self, cidade: str) -> str:
        return (f"{self.emoji} {cidade}: {self.minima:.0f}° a {self.maxima:.0f}°, {self.descricao}, "
                f"{self.chance_chuva}% de chance de chuva")


class Clima:
    def __init__(self, latitude: float, longitude: float, cliente: httpx.Client | None = None):
        self._latitude, self._longitude = latitude, longitude
        self._cliente = cliente or httpx.Client(timeout=15)

    def previsao(self, dias: int = 2) -> dict[date, PrevisaoDia]:
        resposta = self._cliente.get(API, params={
            "latitude": self._latitude, "longitude": self._longitude,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "hourly": "precipitation_probability",
            "timezone": "America/Sao_Paulo", "forecast_days": dias,
        })
        resposta.raise_for_status()
        dados = resposta.json()
        diario, horario = dados["daily"], dados.get("hourly", {})

        por_dia: dict[date, PrevisaoDia] = {}
        for i, texto in enumerate(diario["time"]):
            dia = date.fromisoformat(texto)
            por_dia[dia] = PrevisaoDia(
                dia=dia,
                minima=diario["temperature_2m_min"][i] or 0,
                maxima=diario["temperature_2m_max"][i] or 0,
                chance_chuva=int(diario["precipitation_probability_max"][i] or 0),
                codigo=int(diario["weather_code"][i] or 0),
            )
        for texto, chance in zip(horario.get("time", []), horario.get("precipitation_probability", [])):
            dia, hora = date.fromisoformat(texto[:10]), int(texto[11:13])
            if dia in por_dia and chance is not None:
                por_dia[dia].chuva_por_hora[hora] = int(chance)
        return por_dia
