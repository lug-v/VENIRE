"""Resumo SÓ DE LEITURA para o Drill (robô de mesa do Lucas).

O Drill não grava nada na Venire: ele só espelha compromissos, o mês (gastos,
saldo e projeção) e as tarefas abertas. Quem chama é a rota GET /drill/resumo,
protegida pela senha DRILL_SECRET.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import logging
from zoneinfo import ZoneInfo

from venire.config import FUSO
from venire.servicos import financas, tarefas
from venire.servicos.gastos import formatar_reais

log = logging.getLogger("venire.drill")
MAX_EVENTOS = 8
MAX_TAREFAS = 10


def _instante(campo: dict) -> tuple[datetime | None, bool]:
    """Converte o start/end do Google Agenda em (data e hora, dia inteiro?)."""
    if campo.get("dateTime"):
        return datetime.fromisoformat(campo["dateTime"]), False
    if campo.get("date"):
        return datetime.combine(date.fromisoformat(campo["date"]), time.min, ZoneInfo(FUSO)), True
    return None, False


def eventos(calendario, dia: date) -> list[dict]:
    """Compromissos de hoje e amanhã: título, início/fim (epoch), dia inteiro e local/descrição."""
    inicio = datetime.combine(dia, time.min, ZoneInfo(FUSO))
    lista = []
    for ev in calendario.listar(inicio, inicio + timedelta(days=2)):
        ini, dia_todo = _instante(ev.get("start") or {})
        fim, _ = _instante(ev.get("end") or {})
        if ini is None:
            continue
        lista.append({
            "titulo": (ev.get("summary") or "(sem título)")[:60],
            "inicio": int(ini.timestamp()),
            "fim": int((fim or ini + timedelta(hours=1)).timestamp()),
            "dia_todo": dia_todo,
            "local": (ev.get("location") or "")[:300],
            "descricao": (ev.get("description") or "")[:1000],
        })
    return lista[:MAX_EVENTOS]


def _valor(v: Decimal) -> dict:
    return {"valor": float(v), "texto": formatar_reais(abs(v)), "positivo": v >= 0}


def mes(conexao, dia: date) -> dict:
    atual = financas.mes(conexao, dia.replace(day=1), dia)
    previsto = financas.projecao(conexao, dia)
    return {
        "nome": financas.MESES[dia.month - 1],
        "entrou": _valor(atual.entrou),
        "gastos": _valor(atual.saiu),
        "saldo": _valor(atual.saldo),
        "projecao_saldo": _valor(previsto.saldo),
    }


def montar(deps, dia: date) -> dict:
    """Junta tudo. Se uma parte falhar, as outras continuam (o erro vai em "erros")."""
    resultado: dict = {"dia": dia.isoformat(), "eventos": [], "mes": None, "tarefas": [], "erros": []}
    if deps.calendario:
        try:
            resultado["eventos"] = eventos(deps.calendario(), dia)
        except Exception as erro:
            log.exception("Drill: falha na agenda")
            resultado["erros"].append(f"agenda: {erro}"[:200])
    try:
        with deps.banco() as conexao:
            resultado["mes"] = mes(conexao, dia)
            resultado["tarefas"] = [texto for _, texto, _ in tarefas.abertas(conexao)][:MAX_TAREFAS]
    except Exception as erro:
        log.exception("Drill: falha no banco")
        resultado["erros"].append(f"banco: {erro}"[:200])
    return resultado
