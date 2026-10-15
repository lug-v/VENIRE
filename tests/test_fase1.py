"""Testes da fase 1. Rode com:  python -m pytest

O Telegram é simulado: nenhuma mensagem de verdade é enviada.
"""

import pytest
from fastapi.testclient import TestClient

from api import index
from venire.config import carregar_config
from venire.nucleo.respostas import responder

DONO = 111222333


class TelegramFalso:
    enviados: list = []
    webhook: dict = {}

    def __init__(self, token):
        assert token

    def enviar(self, chat_id, texto):
        TelegramFalso.enviados.append((chat_id, texto))

    def registrar_webhook(self, url, segredo):
        TelegramFalso.webhook = {"url": url, "segredo": segredo}
        return {"username": "venire_teste_bot"}


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("VENIRE_SECRET", "senha-de-teste")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", str(DONO))
    monkeypatch.setattr(index, "Telegram", TelegramFalso)
    TelegramFalso.enviados = []
    return TestClient(index.app)


def mensagem(chat_id, texto, tipo="private"):
    return {"update_id": 1, "message": {
        "message_id": 1, "text": texto,
        "chat": {"id": chat_id, "type": tipo},
        "from": {"id": chat_id, "first_name": "Lucas"},
    }}


def cabecalho():
    return {"X-Telegram-Bot-Api-Secret-Token": carregar_config().segredo_webhook}


def test_status(cliente):
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert resposta.json()["telegram_configurado"] is True


def test_recusa_webhook_sem_senha(cliente):
    resposta = cliente.post("/webhook", json=mensagem(DONO, "oi"))
    assert resposta.status_code == 401
    assert TelegramFalso.enviados == []


def test_responde_o_dono(cliente):
    resposta = cliente.post("/webhook", json=mensagem(DONO, "oi"), headers=cabecalho())
    assert resposta.status_code == 200
    chat_id, texto = TelegramFalso.enviados[0]
    assert chat_id == DONO
    assert "Olá, Lucas" in texto


def test_ignora_estranhos(cliente):
    cliente.post("/webhook", json=mensagem(999, "oi"), headers=cabecalho())
    assert TelegramFalso.enviados == []


def test_ignora_grupos(cliente):
    cliente.post("/webhook", json=mensagem(DONO, "oi", tipo="group"), headers=cabecalho())
    assert TelegramFalso.enviados == []


def test_ensina_o_id_quando_dono_nao_configurado(cliente, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    cliente.post("/webhook", json=mensagem(444, "oi"), headers=cabecalho())
    assert "444" in TelegramFalso.enviados[0][1]


def test_configurar_exige_chave(cliente):
    assert cliente.get("/telegram/configurar?chave=errada").status_code == 401
    resposta = cliente.get("/telegram/configurar?chave=senha-de-teste")
    assert resposta.status_code == 200
    assert TelegramFalso.webhook["url"].endswith("/webhook")
    assert TelegramFalso.webhook["segredo"] == carregar_config().segredo_webhook


def test_respostas_do_nucleo():
    assert "Venire" in responder("/start")
    assert "texto" in responder("")


def test_configurar_usa_endereco_oficial(cliente, monkeypatch):
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "venire-oficial.vercel.app")
    cliente.get("/telegram/configurar?chave=senha-de-teste")
    assert TelegramFalso.webhook["url"] == "https://venire-oficial.vercel.app/webhook"
