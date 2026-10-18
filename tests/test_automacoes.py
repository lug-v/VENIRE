"""Testes da fase 6: clima, dólar, avisos e resumos. Referência: segunda, 19/10/2026."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
import json
import os
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from tests.banco_teste import recriar_tabelas
from fastapi.testclient import TestClient

from api import index
from venire.nucleo import automacoes
from venire.nucleo.respostas import Dependencias, processar_texto
from venire.servicos import gastos
from venire.servicos.cambio import Cambio
from venire.servicos.clima import Clima, PrevisaoDia

FUSO = ZoneInfo("America/Sao_Paulo")
SEGUNDA = date(2026, 10, 19)
URL_TESTE = os.getenv("TEST_DATABASE_URL", "")


# ---------------------------------------------------------------- APIs públicas

def test_clima_le_dias_e_horas():
    recebido = {}

    def open_meteo(request: httpx.Request):
        recebido.update(dict(request.url.params))
        horas = [f"2026-10-19T{h:02d}:00" for h in range(24)] + [f"2026-10-20T{h:02d}:00" for h in range(24)]
        chances = [0] * 19 + [80] + [0] * 4 + [10] * 24
        return httpx.Response(200, json={
            "daily": {"time": ["2026-10-19", "2026-10-20"], "temperature_2m_min": [12.4, 14],
                      "temperature_2m_max": [21.6, 25], "precipitation_probability_max": [80, 10],
                      "weather_code": [63, 1]},
            "hourly": {"time": horas, "precipitation_probability": chances},
        })

    previsao = Clima(-29.17, -51.52, cliente=httpx.Client(transport=httpx.MockTransport(open_meteo))).previsao(2)
    assert recebido["timezone"] == "America/Sao_Paulo"
    hoje = previsao[SEGUNDA]
    assert hoje.chuva_por_hora[19] == 80 and hoje.chuva_por_hora[18] == 0
    assert hoje.resumo("Bento Gonçalves") == "🌧️ Bento Gonçalves: 12° a 22°, chuva, 80% de chance de chuva"


def test_dolar_usa_a_ultima_ptax_e_calcula_variacao():
    recebido = {}

    def bcb(request: httpx.Request):
        recebido.update(dict(request.url.params))
        return httpx.Response(200, json={"value": [
            {"cotacaoVenda": 5.4000, "dataHoraCotacao": "2026-10-15 13:04:12.5"},
            {"cotacaoVenda": 5.4162, "dataHoraCotacao": "2026-10-16 13:07:30.31"},
        ]})

    cotacao = Cambio(cliente=httpx.Client(transport=httpx.MockTransport(bcb))).ultima(SEGUNDA)
    assert recebido["@dataFinalCotacao"] == "'10-19-2026'" and recebido["@dataInicial"] == "'10-09-2026'"
    assert cotacao.dia == date(2026, 10, 16)
    assert cotacao.resumo() == "💵 Dólar (PTAX 16/10): R$ 5,42 ▲ 0,3%"


# ---------------------------------------------------------------- Peças puras

def item(titulo, inicio, fim=None, **extra):
    corpo = {"id": titulo.lower(), "summary": titulo, "start": {"dateTime": inicio.isoformat()}, **extra}
    if fim:
        corpo["end"] = {"dateTime": fim.isoformat()}
    return corpo


def test_lembrete_so_na_proxima_hora():
    agora = datetime(2026, 10, 19, 8, 0, tzinfo=FUSO)
    itens = [
        item("Reunião", agora + timedelta(minutes=58), location="Escritório"),
        item("Almoço", agora + timedelta(minutes=20)),
        item("Treino", agora + timedelta(hours=3)),
        item("Já começou", agora - timedelta(minutes=5)),
        {"id": "x", "summary": "Feriado", "start": {"date": "2026-10-19"}},
    ]
    avisos = dict(automacoes.lembretes_proximos(itens, agora))
    textos = list(avisos.values())
    assert textos[0] == "⏰ Em 1 hora: Reunião (08:58)\n📍 Escritório"
    assert textos[1] == "⏰ Em 20 min: Almoço (08:20)"
    assert len(textos) == 2


def test_alerta_de_chuva_no_horario_do_compromisso():
    previsao = PrevisaoDia(SEGUNDA, 12, 21, 80, 63, {19: 80, 20: 40, 9: 10})
    itens = [
        item("Treino", datetime(2026, 10, 19, 19, 0, tzinfo=FUSO), datetime(2026, 10, 19, 20, 0, tzinfo=FUSO)),
        item("Reunião", datetime(2026, 10, 19, 9, 0, tzinfo=FUSO)),
    ]
    assert automacoes.alertas_de_chuva(itens, previsao) == [
        "☔ 80% de chance de chuva às 19h, no horário de “Treino”. Leve guarda-chuva."]


# ---------------------------------------------------------------- Com banco

class ClimaFalso:
    def previsao(self, dias=2):
        return {SEGUNDA: PrevisaoDia(SEGUNDA, 12, 21, 80, 63, {19: 80}),
                SEGUNDA + timedelta(days=1): PrevisaoDia(SEGUNDA + timedelta(days=1), 14, 25, 10, 1, {})}


class CambioFalso:
    def ultima(self, dia):
        from venire.servicos.cambio import Cotacao
        return Cotacao(5.42, date(2026, 10, 16), 0.3)


class CambioQuebrado:
    def ultima(self, dia):
        raise RuntimeError("BCB fora do ar")


class CalendarioFalso:
    itens: list = []

    def do_dia(self, dia):
        return [i for i in CalendarioFalso.itens if i["start"]["dateTime"].startswith(dia.isoformat())]

    def listar(self, inicio, fim):
        return CalendarioFalso.itens


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

    CalendarioFalso.itens = [
        item("Treino", datetime(2026, 10, 19, 19, 0, tzinfo=FUSO), datetime(2026, 10, 19, 20, 0, tzinfo=FUSO)),
        item("Reunião", datetime(2026, 10, 20, 9, 0, tzinfo=FUSO), datetime(2026, 10, 20, 10, 0, tzinfo=FUSO)),
    ]
    return Dependencias(ia=lambda: None, banco=banco, calendario=lambda: CalendarioFalso(),
                        clima=lambda: ClimaFalso(), cambio=lambda: CambioFalso(),
                        hoje=lambda: SEGUNDA, agora=lambda: datetime(2026, 10, 19, 18, 5, tzinfo=FUSO))


def gastar(deps, valor, dia, categoria="Mercado"):
    with deps.banco() as conexao:
        gastos.salvar(conexao, gastos.Gasto(valor=valor, data=dia, categoria=categoria))


def test_resumo_da_manha_completo(deps):
    gastar(deps, 100, date(2026, 10, 5))
    gastar(deps, 50.5, SEGUNDA)
    texto = processar_texto("/hoje", deps).texto
    assert texto.startswith("☀️ Bom dia! Segunda, 19/10")
    assert "🌧️ Bento Gonçalves: 12° a 21°, chuva, 80% de chance de chuva" in texto
    assert "💵 Dólar (PTAX 16/10): R$ 5,42" in texto
    assert "• 19:00–20:00 — Treino" in texto
    assert "☔ 80% de chance de chuva às 19h, no horário de “Treino”" in texto
    assert "💰 Outubro até agora\nEntrou R$ 0,00 · Saiu R$ 150,50\nSaldo: falta de R$ 150,50" in texto


def test_resumo_continua_se_uma_parte_falhar(deps):
    deps.cambio = lambda: CambioQuebrado()
    texto = processar_texto("/hoje", deps).texto
    assert "Dólar" not in texto and "Treino" in texto


def test_amanha(deps):
    texto = processar_texto("/amanha", deps).texto
    assert texto.startswith("🌙 Amanhã é terça, 20/10")
    assert "☀️" not in texto.split("\n")[0]
    assert "09:00–10:00 — Reunião" in texto and "Dólar" not in texto


def test_manha_envia_uma_vez_e_segunda_inclui_a_semana(deps):
    gastar(deps, 80, date(2026, 10, 14), "Transporte")  # semana passada (11 a 17/10)
    enviadas = []
    assert automacoes.executar_manha(deps, enviadas.append) == {"enviados": 2}
    assert enviadas[0].startswith("☀️ Bom dia!")
    assert "Gastos da semana passada (11/10 a 17/10)" in enviadas[1] and "R$ 80,00" in enviadas[1]
    assert automacoes.executar_manha(deps, enviadas.append) == {"enviados": 0}  # cron chamado de novo
    assert len(enviadas) == 2


def test_lembretes_nao_duplicam(deps):
    enviadas = []
    assert automacoes.executar_lembretes(deps, enviadas.append) == {"enviados": 1}
    assert enviadas == ["⏰ Em 55 min: Treino (19:00)"]
    assert automacoes.executar_lembretes(deps, enviadas.append) == {"enviados": 0}


def test_envio_que_falha_tenta_de_novo(deps):
    def falha(texto):
        raise RuntimeError("Telegram fora do ar")
    with pytest.raises(RuntimeError):
        automacoes.executar_noite(deps, falha)
    enviadas = []
    assert automacoes.executar_noite(deps, enviadas.append) == {"enviados": 1}
    assert enviadas[0].startswith("🌙 Amanhã é terça, 20/10")


# ---------------------------------------------------------------- Rotas /cron

def test_rotas_cron_exigem_chave(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("VENIRE_SECRET", "senha")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    chamadas = []
    monkeypatch.setattr(index.automacoes, "executar_noite", lambda deps, enviar: chamadas.append(1) or {"enviados": 1})
    cliente = TestClient(index.app)
    assert cliente.get("/cron/noite").status_code == 401
    assert cliente.get("/cron/noite?chave=errada").status_code == 401
    assert cliente.get("/cron/noite?chave=senha").json() == {"ok": True, "enviados": 1}
    assert cliente.get("/cron/noite", headers={"Authorization": "Bearer senha"}).status_code == 200
    assert len(chamadas) == 2
    assert json.loads(json.dumps(cliente.get("/").json()))["fase"] == 8
