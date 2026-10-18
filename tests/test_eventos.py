"""Testes da fase 3: agenda. Referência: terça-feira, 13/10/2026, 10h da manhã."""

from contextlib import contextmanager
from datetime import date, time
import os

import psycopg
import pytest

from tests.banco_teste import recriar_tabelas

from venire.datas import data_do_evento, hora_do_evento
from venire.nucleo.respostas import Dependencias, processar_botao, processar_texto
from venire.servicos import eventos

HOJE = date(2026, 10, 13)  # terça
AGORA = time(10, 0)
URL_TESTE = os.getenv("TEST_DATABASE_URL", "")


# ---------------------------------------------------------------- Datas e horas

@pytest.mark.parametrize("texto, esperado", [
    ("", [date(2026, 10, 13)]),
    ("amanhã", [date(2026, 10, 14)]),
    ("depois de amanhã", [date(2026, 10, 15)]),
    ("quinta", [date(2026, 10, 15)]),
    ("na quinta-feira", [date(2026, 10, 15)]),
    ("quinta que vem", [date(2026, 10, 15)]),
    ("próxima quinta", [date(2026, 10, 15)]),
    ("quinta da semana que vem", [date(2026, 10, 22)]),
    ("sexta da próxima semana", [date(2026, 10, 23)]),
    ("domingo", [date(2026, 10, 18)]),
    ("terça", [date(2026, 10, 13), date(2026, 10, 20)]),  # hoje é terça → perguntar
    ("dia 15 desse mês", [date(2026, 10, 15)]),
    ("dia 5", [date(2026, 11, 5)]),
    ("mês que vem dia 10", [date(2026, 11, 10)]),
    ("dia 10 do mês que vem", [date(2026, 11, 10)]),
    ("10 de dezembro", [date(2026, 12, 10)]),
    ("5 de março", [date(2027, 3, 5)]),
    ("10/12", [date(2026, 12, 10)]),
    ("01/02", [date(2027, 2, 1)]),
    ("dia 31 do mês que vem", None),  # 31/11 não existe
])
def test_data_do_evento(texto, esperado):
    resultado = data_do_evento(texto, HOJE)
    assert (resultado.opcoes if resultado else None) == esperado


def test_nota_quando_o_dia_ja_passou():
    assert "já passou" in data_do_evento("dia 5", HOJE).nota


@pytest.mark.parametrize("texto, esperado", [
    ("9", time(9)), ("9h", time(9)), ("às 9", time(9)), ("9:30", time(9, 30)),
    ("9h30", time(9, 30)), ("9 e meia", time(9, 30)), ("3", time(15)), ("3 da tarde", time(15)),
    ("15h", time(15)), ("8 da noite", time(20)), ("7 da manhã", time(7)), ("meio-dia", time(12)),
    ("meia-noite", time(0)), ("12", time(12)), ("", None), ("de manhã cedo", None), ("25h", None),
])
def test_hora_do_evento(texto, esperado):
    assert hora_do_evento(texto) == esperado


# ---------------------------------------------------------------- Montagem do evento

def test_reuniao_as_9_na_quinta():
    r = eventos.de_dados({"titulo": "reunião", "data_texto": "quinta", "hora_texto": "9"},
                         "marcar reunião às 9 na quinta", HOJE, AGORA)
    assert r.evento.titulo == "Reunião"
    assert r.evento.data == date(2026, 10, 15)
    assert (r.evento.inicio, r.evento.fim) == (time(9), time(10))  # 1 hora quando não há fim


def test_revisao_do_carro_vira_dia_inteiro():
    r = eventos.de_dados({"titulo": "revisão do carro", "data_texto": "mês que vem dia 10"},
                         "Agendar mes que vem dia 10 revisao carro", HOJE, AGORA)
    assert r.evento.dia_inteiro
    assert r.evento.data == date(2026, 11, 10)
    assert "dia inteiro" in eventos.quando(r.evento)


def test_horario_final_e_duracao():
    r = eventos.de_dados({"titulo": "aula", "data_texto": "amanhã", "hora_texto": "19h", "hora_fim_texto": "22h"},
                         "aula amanhã das 19h às 22h", HOJE, AGORA)
    assert (r.evento.inicio, r.evento.fim) == (time(19), time(22))
    r = eventos.de_dados({"titulo": "treino", "data_texto": "amanhã", "hora_texto": "7 da manhã",
                          "duracao_minutos": 90}, "treino amanhã 7 da manhã por 1h30", HOJE, AGORA)
    assert (r.evento.inicio, r.evento.fim) == (time(7), time(8, 30))


def test_sem_data_e_horario_passado_vai_para_amanha():
    r = eventos.de_dados({"titulo": "ligar para o cliente", "hora_texto": "8"}, "ligar pro cliente às 8",
                         HOJE, AGORA)
    assert r.evento.data == date(2026, 10, 14)
    assert "amanhã" in r.evento.nota


def test_data_invalida_explica():
    r = eventos.de_dados({"titulo": "x", "data_texto": "31/02"}, "x dia 31/02", HOJE, AGORA)
    assert r.evento is None and "Não entendi a data" in r.problema


# ---------------------------------------------------------------- Fluxo completo

class IAFalsa:
    def __init__(self, resposta):
        self.resposta = resposta

    def json(self, instrucoes, mensagem):
        return self.resposta


class CalendarioFalso:
    eventos: dict = {}
    ocupado: list = []

    def conflitos(self, evento):
        return [] if evento.dia_inteiro else list(CalendarioFalso.ocupado)

    def criar(self, evento):
        CalendarioFalso.eventos[evento.id] = evento
        return "https://calendar.google.com/x"

    def apagar(self, evento_id):
        return CalendarioFalso.eventos.pop(evento_id, None) is not None

    def do_dia(self, dia):
        return [{"summary": e.titulo, "start": {"dateTime": f"{e.data}T{e.inicio}-03:00"},
                 "end": {"dateTime": f"{e.data}T{e.fim}-03:00"}}
                for e in CalendarioFalso.eventos.values() if e.data == dia and e.inicio]


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
    CalendarioFalso.ocupado = []
    estado = {"ia": IAFalsa({})}
    d = Dependencias(ia=lambda: estado["ia"], banco=banco, calendario=lambda: CalendarioFalso(),
                     hoje=lambda: HOJE, hora_atual=lambda: AGORA)
    d.estado = estado
    return d


def pedir(deps, texto, **campos):
    deps.estado["ia"] = IAFalsa({"tipo": "evento", **campos})
    return processar_texto(texto, deps)


def test_agenda_direto_e_desfaz(deps):
    resposta = pedir(deps, "marcar reunião às 9 na quinta", titulo="reunião", data_texto="quinta", hora_texto="9")
    assert "📅 Agendado: Reunião" in resposta.texto
    assert "quinta, 15/10 · 09:00–10:00" in resposta.texto
    assert len(CalendarioFalso.eventos) == 1
    rotulo, dado = resposta.botoes[0]
    assert rotulo == "↩️ Desfazer" and len(dado.encode()) <= 64
    assert "removido" in processar_botao(dado, deps).texto
    assert CalendarioFalso.eventos == {}


def test_conflito_pergunta_e_agenda_mesmo_assim(deps):
    CalendarioFalso.ocupado = [{"summary": "Dentista", "start": {"dateTime": "2026-10-15T09:00:00-03:00"},
                                "end": {"dateTime": "2026-10-15T10:00:00-03:00"}}]
    resposta = pedir(deps, "reunião quinta às 9", titulo="reunião", data_texto="quinta", hora_texto="9")
    assert "conflita com" in resposta.texto and "Dentista" in resposta.texto
    assert CalendarioFalso.eventos == {}
    dado = [d for r, d in resposta.botoes if r.startswith("✅")][0]
    assert len(dado.encode()) <= 64
    assert "📅 Agendado" in processar_botao(dado, deps).texto
    assert len(CalendarioFalso.eventos) == 1
    assert "expirou" in processar_botao(dado, deps).texto  # botão só vale uma vez


def test_conflito_cancelar(deps):
    CalendarioFalso.ocupado = [{"summary": "Dentista", "start": {"dateTime": "2026-10-15T09:00:00-03:00"}}]
    resposta = pedir(deps, "reunião quinta às 9", titulo="reunião", data_texto="quinta", hora_texto="9")
    dado = [d for r, d in resposta.botoes if r.startswith("❌")][0]
    assert "não agendei" in processar_botao(dado, deps).texto
    assert CalendarioFalso.eventos == {}


def test_hoje_e_o_dia_citado_pergunta_qual(deps):
    resposta = pedir(deps, "reunião terça às 15h", titulo="reunião", data_texto="terça", hora_texto="15h")
    assert "Para qual dia" in resposta.texto
    rotulos = [r for r, _ in resposta.botoes]
    assert rotulos == ["Hoje (13/10)", "Terça, 20/10"]
    resposta = processar_botao(resposta.botoes[1][1], deps)
    assert "terça, 20/10 · 15:00–16:00" in resposta.texto


def test_agenda_do_dia(deps):
    pedir(deps, "reunião hoje às 15h", titulo="reunião", data_texto="hoje", hora_texto="15h")
    texto = processar_texto("/agenda", deps).texto
    assert "15:00–16:00 — Reunião" in texto
    assert "Nada marcado" in texto  # amanhã está livre


def test_sem_agenda_configurada(deps):
    deps.calendario = None
    resposta = pedir(deps, "reunião quinta às 9", titulo="reunião", data_texto="quinta", hora_texto="9")
    assert "GOOGLE_CALENDAR_ID" in resposta.texto


def test_mensagem_que_nao_e_gasto_nem_evento(deps):
    deps.estado["ia"] = IAFalsa({"tipo": "outro"})
    resposta = processar_texto("qual a capital da França?", deps)
    assert "Ainda não sei fazer isso" in resposta.texto
