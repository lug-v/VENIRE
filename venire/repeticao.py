"""Compromissos que se repetem: "toda terça e quinta", "todo dia 10", "dias úteis"...

A IA só copia a expressão de repetição como foi dita. Aqui ela vira uma regra RRULE
(o padrão que o Google Agenda entende) e a data da primeira ocorrência é calculada.
"""

from dataclasses import dataclass
from calendar import monthrange
from datetime import date, time, timedelta
import re

from venire.datas import DIAS_SEMANA, normalizar

CODIGOS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]   # segunda = 0 ... domingo = 6
NOMES = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


@dataclass
class Repeticao:
    regra: str          # ex.: "FREQ=WEEKLY;BYDAY=TU,TH"
    descricao: str      # ex.: "toda terça e quinta"
    dias_semana: tuple[int, ...] = ()
    dia_do_mes: int | None = None

    def primeira_data(self, a_partir_de: date, inicio: time | None = None, hora_atual: time | None = None) -> date:
        """Primeira ocorrência a partir de uma data (pula hoje se o horário já passou)."""
        dia = a_partir_de
        if inicio and hora_atual and inicio <= hora_atual:
            dia += timedelta(days=1)
        if self.dias_semana:
            return min(dia + timedelta(days=(d - dia.weekday()) % 7) for d in self.dias_semana)
        if self.dia_do_mes:
            ano, mes = dia.year, dia.month
            while True:
                if self.dia_do_mes <= monthrange(ano, mes)[1]:
                    candidato = date(ano, mes, self.dia_do_mes)
                    if candidato >= dia:
                        return candidato
                ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
        return dia


def _juntar(nomes: list[str]) -> str:
    return nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]


def _semanal(dias: list[int], intervalo: int = 1) -> Repeticao:
    dias = sorted(set(dias))
    regra = "FREQ=WEEKLY" + (f";INTERVAL={intervalo}" if intervalo > 1 else "") + ";BYDAY=" + ",".join(CODIGOS[d] for d in dias)
    if dias == [0, 1, 2, 3, 4]:
        descricao = "de segunda a sexta"
    else:
        prefixo = "a cada 2 semanas, " if intervalo == 2 else ""
        artigo = "todo" if all(d >= 5 for d in dias) else "toda"
        descricao = f"{prefixo}{artigo} {_juntar([NOMES[d] for d in dias])}"
    return Repeticao(regra, descricao, dias_semana=tuple(dias))


def interpretar(texto: str, data_base: date | None = None) -> Repeticao | None:
    """Transforma a expressão de repetição em regra. None = não é (ou não entendi) repetição.

    "toda terça e quinta"        → semanal, terça e quinta
    "dias úteis", "seg a sex"    → semanal, segunda a sexta
    "todo dia 10", "todo mês dia 10" → mensal, dia 10
    "todos os dias", "diariamente"   → diária
    "toda semana" / "todo mês" / "todo ano" → usa o dia da data_base
    "a cada 15 dias", "quinzenal", "a cada 2 semanas"
    """
    t = normalizar(texto).replace("-feiras", "").replace("-feira", "")
    if not t:
        return None
    base = data_base or date.today()

    if re.search(r"dias? ute(is|l)|segunda a sexta|seg a sex", t):
        return _semanal([0, 1, 2, 3, 4])

    dia_mes = re.search(r"(?:todo|todos os|cada)\s+(?:mes\s+)?(?:no\s+)?dia\s+(\d{1,2})\b", t) \
        or re.search(r"todo mes.*?\bdia\s+(\d{1,2})\b", t) or re.search(r"\bdia\s+(\d{1,2})\s+de\s+todo\s+mes", t)
    if dia_mes and 1 <= int(dia_mes.group(1)) <= 31:
        numero = int(dia_mes.group(1))
        return Repeticao(f"FREQ=MONTHLY;BYMONTHDAY={numero}", f"todo dia {numero}", dia_do_mes=numero)

    dias = [n for nome, n in DIAS_SEMANA.items() if re.search(rf"\b{nome}s?\b", t)]
    quinzena = bool(re.search(r"quinzena|quinzenal|a cada (2|duas) semanas|semana sim,? semana nao", t))
    if dias:
        return _semanal(dias, 2 if quinzena else 1)
    if quinzena:
        return _semanal([base.weekday()], 2)

    a_cada = re.search(r"a cada (\d{1,3}) dias", t)
    if a_cada and int(a_cada.group(1)) > 1:
        n = int(a_cada.group(1))
        return Repeticao(f"FREQ=DAILY;INTERVAL={n}", f"a cada {n} dias")
    if re.search(r"todos os dias|todo dia|diariamente|diario", t):
        return Repeticao("FREQ=DAILY", "todos os dias")
    if re.search(r"toda semana|semanal|todas as semanas", t):
        return _semanal([base.weekday()])
    if re.search(r"todo mes|mensal|todos os meses", t):
        return Repeticao(f"FREQ=MONTHLY;BYMONTHDAY={base.day}", f"todo dia {base.day}", dia_do_mes=base.day)
    if re.search(r"todo ano|anual|todos os anos", t):
        return Repeticao("FREQ=YEARLY", f"todo ano em {base:%d/%m}")
    return None
