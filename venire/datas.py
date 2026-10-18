"""Datas: a IA extrai o que foi dito ("ontem", "sexta", "dia 10"), e este código calcula.

Modelos de linguagem erram contas de calendário com frequência, então as contas
ficam aqui, em Python, com testes automáticos.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
import unicodedata
from zoneinfo import ZoneInfo

from venire.config import FUSO

# Python numera os dias assim: segunda = 0 ... domingo = 6
DIAS_SEMANA = {
    "segunda": 0, "terca": 1, "quarta": 2, "quinta": 3,
    "sexta": 4, "sabado": 5, "domingo": 6,
}


def hoje() -> date:
    return datetime.now(ZoneInfo(FUSO)).date()


def hora_atual() -> time:
    return datetime.now(ZoneInfo(FUSO)).time()


def normalizar(texto: str) -> str:
    """Minúsculas e sem acentos: "Terça-Feira" → "terca-feira"."""
    sem_acento = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in sem_acento if unicodedata.category(c) != "Mn").lower().strip()


def data_do_gasto(quando: str, referencia: date | None = None) -> date:
    """Resolve quando um gasto aconteceu. Gastos são sempre no passado (ou hoje).

    Exemplos (com referência na terça 13/10/2026):
      ""            → 13/10 (sem informação = hoje)
      "ontem"       → 12/10
      "sexta"       → 09/10 (a sexta mais recente)
      "dia 10"      → 10/10
      "dia 20"      → 20/09 (dia 20 deste mês ainda não chegou)
      "05/10"       → 05/10
    """
    ref = referencia or hoje()
    texto = normalizar(quando)

    if not texto or texto == "hoje":
        return ref
    if texto == "ontem":
        return ref - timedelta(days=1)
    if texto == "anteontem":
        return ref - timedelta(days=2)

    for nome, numero in DIAS_SEMANA.items():
        if texto.startswith(nome) or f" {nome}" in texto:
            dias_atras = (ref.weekday() - numero) % 7
            if "passad" in texto and dias_atras == 0:
                dias_atras = 7
            return ref - timedelta(days=dias_atras)

    completa = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", texto)
    if completa:
        dia, mes = int(completa.group(1)), int(completa.group(2))
        ano = int(completa.group(3)) if completa.group(3) else ref.year
        ano = ano + 2000 if ano < 100 else ano
        try:
            resultado = date(ano, mes, dia)
        except ValueError:
            return ref
        if not completa.group(3) and resultado > ref:
            resultado = resultado.replace(year=ano - 1)
        return min(resultado, ref)

    so_dia = re.search(r"dia (\d{1,2})", texto)
    if so_dia:
        dia = int(so_dia.group(1))
        ano, mes = ref.year, ref.month
        if dia > ref.day:  # ainda não chegou neste mês → mês passado
            mes -= 1
            if mes == 0:
                ano, mes = ano - 1, 12
        try:
            return date(ano, mes, dia)
        except ValueError:
            return ref

    return ref


def inicio_da_semana(referencia: date | None = None) -> date:
    """Semana brasileira: de domingo a sábado."""
    ref = referencia or hoje()
    return ref - timedelta(days=(ref.weekday() + 1) % 7)


def formatar(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


# ---------------------------------------------------------------- Eventos (futuro)

MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
NOMES_DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


@dataclass
class DataEvento:
    opcoes: list[date]        # uma opção = data definida; duas = perguntar ao usuário
    nota: str = ""            # explica uma suposição, ex.: "dia 5 já passou, usei o do mês que vem"


def _somar_meses(dia: int, ano: int, mes: int, meses: int) -> date | None:
    mes_total = mes - 1 + meses
    ano, mes = ano + mes_total // 12, mes_total % 12 + 1
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def data_do_evento(texto: str, referencia: date | None = None) -> DataEvento | None:
    """Resolve a data de um compromisso (sempre hoje ou no futuro).

    Regras (referência: terça 13/10/2026):
      "", "hoje"                        → 13/10
      "amanhã" / "depois de amanhã"     → 14/10 / 15/10
      "quinta", "na quinta", "quinta que vem", "próxima quinta" → 15/10
      "quinta da semana que vem"        → 22/10 (semana de domingo a sábado)
      "terça" (hoje é terça)            → pergunta: 13/10 ou 20/10
      "dia 15", "dia 15 desse mês"      → 15/10
      "dia 5" (já passou)               → 05/11, com nota
      "mês que vem dia 10"              → 10/11
      "10 de dezembro", "10/12"         → 10/12
    Devolve None quando a data não existe (ex.: 31/02).
    """
    ref = referencia or hoje()
    t = normalizar(texto).replace("-feira", "")

    if not t or t == "hoje":
        return DataEvento([ref])
    if "depois de amanha" in t:
        return DataEvento([ref + timedelta(days=2)])
    if "amanha" in t:
        return DataEvento([ref + timedelta(days=1)])

    proxima_semana = bool(re.search(r"semana que vem|proxima semana|outra semana", t))
    for nome, numero in DIAS_SEMANA.items():
        if re.search(rf"\b{nome}\b", t):
            if proxima_semana:
                domingo = inicio_da_semana(ref) + timedelta(days=7)
                return DataEvento([domingo + timedelta(days=(numero + 1) % 7)])
            dias = (numero - ref.weekday()) % 7
            if dias == 0:
                return DataEvento([ref, ref + timedelta(days=7)])
            return DataEvento([ref + timedelta(days=dias)])

    completa = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
    if completa:
        dia, mes = int(completa.group(1)), int(completa.group(2))
        ano = int(completa.group(3)) if completa.group(3) else ref.year
        ano = ano + 2000 if ano < 100 else ano
        resultado = _somar_meses(dia, ano, mes, 0)
        if resultado and not completa.group(3) and resultado < ref:
            resultado = _somar_meses(dia, ano + 1, mes, 0)
        return DataEvento([resultado]) if resultado else None

    numero_dia = re.search(r"(?:dia\s*)?(\d{1,2})(?:\s*de\s*([a-z]+))?", t)
    if numero_dia:
        dia = int(numero_dia.group(1))
        nome_mes = numero_dia.group(2)
        if nome_mes in MESES:
            resultado = _somar_meses(dia, ref.year, MESES[nome_mes], 0)
            if resultado and resultado < ref:
                resultado = _somar_meses(dia, ref.year + 1, MESES[nome_mes], 0)
            return DataEvento([resultado]) if resultado else None
        if re.search(r"mes que vem|proximo mes", t):
            resultado = _somar_meses(dia, ref.year, ref.month, 1)
            return DataEvento([resultado]) if resultado else None
        resultado = _somar_meses(dia, ref.year, ref.month, 0)
        if resultado and resultado < ref:
            resultado = _somar_meses(dia, ref.year, ref.month, 1)
            return DataEvento([resultado], f"o dia {dia} deste mês já passou, então usei o do mês que vem") \
                if resultado else None
        return DataEvento([resultado]) if resultado else None

    return None


def data_ja_passou(texto: str, referencia: date | None = None) -> bool:
    """True quando o texto traz dia e mês sem ano ("05 de outubro", "05/10") e essa data,
    no ano atual, ficou para trás há menos de 6 meses. Usado em listas de compromissos,
    para pular itens passados em vez de jogá-los para o ano que vem."""
    ref = referencia or hoje()
    t = normalizar(texto)
    completa = re.search(r"(\d{1,2})/(\d{1,2})(?!/)", t)
    por_extenso = re.search(r"(\d{1,2})\s*de\s*([a-z]+)", t)
    if completa:
        dia, mes = int(completa.group(1)), int(completa.group(2))
    elif por_extenso and por_extenso.group(2) in MESES:
        dia, mes = int(por_extenso.group(1)), MESES[por_extenso.group(2)]
    else:
        return False
    if re.search(r"\b20\d{2}\b", t):
        return False
    data = _somar_meses(dia, ref.year, mes, 0)
    return bool(data and data < ref and (ref - data).days <= 180)


def hora_do_evento(texto: str) -> time | None:
    """Converte a hora falada em horário.

      "9", "9h", "9:30", "9h30", "9 e meia" → 09:00 / 09:30
      "3", "3 da tarde", "15h"              → 15:00 (de 1 a 6 sem indicação, assume a tarde)
      "8 da noite"                          → 20:00
      "meio-dia" / "meia-noite"             → 12:00 / 00:00
    """
    t = normalizar(texto)
    if not t:
        return None
    if "meio dia" in t or "meio-dia" in t:
        return time(12, 30 if "meia" in t.replace("meio", "") else 0)
    if "meia noite" in t or "meia-noite" in t:
        return time(0, 0)
    achado = re.search(r"(\d{1,2})\s*(?:[:h]\s*(\d{2}))?", t)
    if not achado:
        return None
    hora = int(achado.group(1))
    minuto = int(achado.group(2) or 0)
    if "e meia" in t:
        minuto = 30
    if hora > 23 or minuto > 59:
        return None
    if ("tarde" in t or "noite" in t) and hora < 12:
        hora += 12
    elif "manha" not in t and "madrugada" not in t and 1 <= hora <= 6:
        hora += 12
    return time(hora, minuto)


def descrever(dia: date) -> str:
    """Ex.: "quinta, 15/10"."""
    return f"{NOMES_DIAS[dia.weekday()]}, {dia.strftime('%d/%m')}"
