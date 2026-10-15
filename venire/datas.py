"""Datas: a IA extrai o que foi dito ("ontem", "sexta", "dia 10"), e este código calcula.

Modelos de linguagem erram contas de calendário com frequência, então as contas
ficam aqui, em Python, com testes automáticos.
"""

from datetime import date, datetime, timedelta
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
