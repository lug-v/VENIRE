"""Lista de compromissos colada numa mensagem (ex.: cronograma de aulas). Referência: domingo, 18/10/2026."""

from contextlib import contextmanager
from datetime import date, time
import os

import psycopg
import pytest

from tests.banco_teste import recriar_tabelas
from venire.datas import data_ja_passou
from venire.nucleo.respostas import Dependencias, processar_botao, processar_texto

HOJE = date(2026, 10, 18)
URL_TESTE = os.getenv("TEST_DATABASE_URL", "")


@pytest.mark.parametrize("texto, esperado", [
    ("05 de Outubro", True), ("17/10", True), ("19 de Outubro", False), ("18 de outubro", False),
    ("30/10", False), ("05 de outubro de 2026", False), ("dia 5", False), ("sexta", False),
    ("10 de janeiro", False),  # 9 meses atrás: é o próximo janeiro
])
def test_data_ja_passou(texto, esperado):
    assert data_ja_passou(texto, HOJE) is esperado


def aula(dia, titulo="Aula Live – Big Data Analytics", hora="19:00", fim="22:20", local="Online"):
    return {"titulo": titulo, "data_texto": dia, "hora_texto": hora, "hora_fim_texto": fim,
            "local": local, "detalhes": "Professor: Vinicius Aquino do Vale"}


class IAFalsa:
    def __init__(self, resposta):
        self.resposta = resposta

    def json(self, instrucoes, mensagem):
        return self.resposta


class CalendarioFalso:
    eventos: dict = {}

    def conflitos(self, evento):
        return [{"summary": "Muay Thai"}] if evento.data == date(2026, 10, 20) else []

    def criar(self, evento):
        CalendarioFalso.eventos[evento.id] = evento

    def apagar(self, evento_id):
        return CalendarioFalso.eventos.pop(evento_id, None) is not None


@pytest.fixture
def deps():
    if not URL_TESTE:
        pytest.skip("TEST_DATABASE_URL não configurada")
    recriar_tabelas(URL_TESTE)

    @contextmanager
    def banco():
        with psycopg.connect(URL_TESTE) as conexao:
            yield conexao
            conexao.commit()

    CalendarioFalso.eventos = {}
    return Dependencias(ia=lambda: None, banco=banco, calendario=lambda: CalendarioFalso(),
                        hoje=lambda: HOJE, hora_atual=lambda: time(15))


def test_cronograma_da_fgv(deps):
    lista = [aula("05 de Outubro"), aula("06 de Outubro"), {"titulo": "Feriado", "data_texto": "12 de Outubro"},
             aula("17 de Outubro", "1ª chamada – Técnicas Não Supervisionadas", "08:00", "", "Dr. Bozano, 715"),
             aula("19 de Outubro"), aula("20 de Outubro"), aula("26 de Outubro"), aula("27 de Outubro"),
             aula("30 de Outubro", "2ª chamada – Técnicas Não Supervisionadas", "19:00", "", "Dr. Bozano, 715"),
             {"titulo": "Prova", "data_texto": "31 de fevereiro"}]
    deps.ia = lambda: IAFalsa({"tipo": "eventos", "eventos": lista})
    resposta = processar_texto("* 05 de Outubro\nEvento: Aula Live ...", deps)
    assert resposta.texto.splitlines()[:7] == [
        "📅 Agendei 5 compromissos:",
        "• segunda, 19/10 · 19:00–22:20 — Aula Live – Big Data Analytics",
        "• terça, 20/10 · 19:00–22:20 — Aula Live – Big Data Analytics",
        "• segunda, 26/10 · 19:00–22:20 — Aula Live – Big Data Analytics",
        "• terça, 27/10 · 19:00–22:20 — Aula Live – Big Data Analytics",
        "• sexta, 30/10 · 19:00–20:00 — 2ª chamada – Técnicas Não Supervisionadas",
        "",
    ]
    assert "⏭️ Pulei 4 que já passaram (05 de Outubro a 17 de Outubro)." in resposta.texto
    assert "• Aula Live – Big Data Analytics (20/10) × Muay Thai" in resposta.texto
    assert "❓ Não entendi a data de:\n• Prova (31 de fevereiro)" in resposta.texto
    criado = next(e for e in CalendarioFalso.eventos.values() if e.data == date(2026, 10, 19))
    assert criado.detalhes == "Professor: Vinicius Aquino do Vale" and criado.local == "Online"

    assert processar_botao(resposta.botoes[0][1], deps).texto == "↩️ Removi 5 compromissos da agenda."
    assert CalendarioFalso.eventos == {}
    assert "já foi desfeito" in processar_botao(resposta.botoes[0][1], deps).texto


def test_lista_com_um_item_vira_agendamento_normal(deps):
    deps.ia = lambda: IAFalsa({"tipo": "eventos", "eventos": [aula("19 de Outubro")]})
    resposta = processar_texto("aula dia 19", deps)
    assert resposta.texto.startswith("📅 Agendado: Aula Live – Big Data Analytics")
