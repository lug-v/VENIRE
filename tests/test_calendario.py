"""Testa a integração com o Google Agenda usando uma API do Google simulada."""

from datetime import date, time
import json

import httpx

from venire.servicos.calendario import Calendario
from venire.servicos.eventos import Evento

CONTA = '{"type": "service_account", "client_email": "robo@x.iam.gserviceaccount.com", "private_key": "k"}'
BASE = "/calendar/v3/calendars/lucas@gmail.com"


class GoogleFalso:
    def __init__(self, existentes=None):
        self.criados: dict = {}
        self.existentes = existentes or []
        self.pedidos: list = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        caminho = request.url.path
        assert caminho.startswith(BASE), caminho
        resto = caminho[len(BASE):]
        self.pedidos.append((request.method, resto, dict(request.url.params)))
        if request.method == "POST" and resto == "/events":
            corpo = json.loads(request.content)
            self.criados[corpo["id"]] = corpo
            return httpx.Response(200, json={"htmlLink": "https://calendar/x"})
        if request.method == "GET" and resto == "/events":
            return httpx.Response(200, json={"items": self.existentes})
        if request.method == "DELETE":
            evento_id = resto.rsplit("/", 1)[1]
            return httpx.Response(204) if self.criados.pop(evento_id, None) else httpx.Response(410)
        if request.method == "GET" and resto == "":
            return httpx.Response(200, json={"summary": "Lucas V.G"})
        return httpx.Response(404)


def criar(google):
    calendario = Calendario(CONTA, "lucas@gmail.com", cliente=httpx.Client(transport=httpx.MockTransport(google)))
    calendario._token = "token-falso"
    return calendario


def test_cria_evento_com_horario_no_fuso_de_brasilia():
    google = GoogleFalso()
    evento = Evento(titulo="Reunião", data=date(2026, 10, 15), inicio=time(9), fim=time(10), local="Escritório")
    criar(google).criar(evento)
    corpo = google.criados[evento.id]
    assert corpo["summary"] == "Reunião" and corpo["location"] == "Escritório"
    assert corpo["start"] == {"dateTime": "2026-10-15T09:00:00-03:00", "timeZone": "America/Sao_Paulo"}
    assert corpo["end"]["dateTime"] == "2026-10-15T10:00:00-03:00"


def test_evento_de_dia_inteiro_termina_no_dia_seguinte():
    google = GoogleFalso()
    evento = Evento(titulo="Revisão do carro", data=date(2026, 11, 10))
    criar(google).criar(evento)
    corpo = google.criados[evento.id]
    assert corpo["start"] == {"date": "2026-11-10"} and corpo["end"] == {"date": "2026-11-11"}


def test_apagar_e_apagar_de_novo():
    google = GoogleFalso()
    calendario = criar(google)
    evento = Evento(titulo="X", data=date(2026, 10, 15), inicio=time(9), fim=time(10))
    calendario.criar(evento)
    assert calendario.apagar(evento.id) is True
    assert calendario.apagar(evento.id) is False  # já tinha sido apagado


def test_conflitos_ignoram_dia_inteiro_e_livre():
    existentes = [
        {"summary": "Dentista", "start": {"dateTime": "2026-10-15T09:30:00-03:00"}},
        {"summary": "Feriado", "start": {"date": "2026-10-15"}},
        {"summary": "Lembrete", "start": {"dateTime": "2026-10-15T09:00:00-03:00"}, "transparency": "transparent"},
    ]
    google = GoogleFalso(existentes)
    evento = Evento(titulo="Reunião", data=date(2026, 10, 15), inicio=time(9), fim=time(10))
    conflitos = criar(google).conflitos(evento)
    assert [c["summary"] for c in conflitos] == ["Dentista"]
    _, _, params = google.pedidos[-1]
    assert params["timeMin"] == "2026-10-15T09:00:00-03:00" and params["timeMax"] == "2026-10-15T10:00:00-03:00"


def test_testar_e_erro_403():
    assert criar(GoogleFalso()).testar() == "Lucas V.G"

    def proibido(request):
        return httpx.Response(403, json={"error": {"message": "Forbidden"}})
    try:
        criar(proibido).testar()
    except Exception as erro:
        assert "Fazer alterações nos eventos" in str(erro)
    else:
        raise AssertionError("deveria ter falhado")
