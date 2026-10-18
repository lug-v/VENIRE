"""Testes da fase 4: áudio e foto."""

import base64
from contextlib import contextmanager
from datetime import date, time
import json
import os

import httpx
import psycopg
import pytest

from tests.banco_teste import recriar_tabelas
from fastapi.testclient import TestClient

from api import index
from venire.config import carregar_config
from venire.ia.groq import Groq
from venire.nucleo.respostas import Dependencias, processar_audio, processar_foto

HOJE = date(2026, 10, 18)  # domingo
URL_TESTE = os.getenv("TEST_DATABASE_URL", "")


# ---------------------------------------------------------------- Cliente do Groq

def test_transcrever_envia_audio_em_portugues():
    recebido = {}

    def groq(request: httpx.Request):
        recebido["url"] = str(request.url)
        recebido["corpo"] = request.content
        return httpx.Response(200, json={"text": "  gastei 40 no posto  "})

    ia = Groq("gsk_x", "modelo", cliente=httpx.Client(transport=httpx.MockTransport(groq)))
    assert ia.transcrever(b"OggS-audio", "voz.ogg") == "gastei 40 no posto"
    assert recebido["url"].endswith("/audio/transcriptions")
    assert b"whisper-large-v3-turbo" in recebido["corpo"]
    assert b'name="language"\r\n\r\npt' in recebido["corpo"]
    assert b"OggS-audio" in recebido["corpo"]


def test_imagem_vai_em_base64_com_legenda():
    recebido = {}

    def groq(request: httpx.Request):
        recebido.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tipo": "outro"}'}}]})

    ia = Groq("gsk_x", "qwen/qwen3.8-27b", cliente=httpx.Client(transport=httpx.MockTransport(groq)))
    assert ia.json_imagem("instruções", b"\xff\xd8foto", "image/jpeg", "mercado") == {"tipo": "outro"}
    conteudo = recebido["messages"][1]["content"]
    assert "mercado" in conteudo[0]["text"]
    assert conteudo[1]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8foto").decode()
    assert recebido["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------- Fluxos com banco

class IAFalsa:
    def __init__(self, transcricao="", texto=None, foto=None):
        self.transcricao, self.texto, self.foto = transcricao, texto or {}, foto or {}

    def transcrever(self, audio, nome_arquivo):
        return self.transcricao

    def json(self, instrucoes, mensagem):
        return self.texto

    def json_imagem(self, instrucoes, imagem, tipo, legenda):
        return self.foto


class CalendarioFalso:
    eventos: dict = {}

    def conflitos(self, evento):
        return []

    def criar(self, evento):
        CalendarioFalso.eventos[evento.id] = evento


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
    estado = {"ia": IAFalsa()}
    d = Dependencias(ia=lambda: estado["ia"], banco=banco, calendario=lambda: CalendarioFalso(),
                     hoje=lambda: HOJE, hora_atual=lambda: time(10))
    d.estado = estado
    return d


def ultimo_gasto(deps):
    with deps.banco() as conexao:
        return conexao.execute(
            "SELECT amount, category, source, spent_on, merchant FROM expenses ORDER BY created_at DESC LIMIT 1"
        ).fetchone()


def test_audio_de_gasto(deps):
    deps.estado["ia"] = IAFalsa("gastei 40 no posto", texto={
        "tipo": "gasto", "valor": 40, "descricao": "combustível", "categoria": "Transporte"})
    resposta = processar_audio(b"audio", "voz.ogg", deps)
    assert resposta.texto.startswith("🎙️ “gastei 40 no posto”")
    assert "R$ 40,00" in resposta.texto and resposta.botoes[0][0] == "↩️ Desfazer"
    assert ultimo_gasto(deps)[:3] == (40, "Transporte", "audio")


def test_audio_de_compromisso(deps):
    deps.estado["ia"] = IAFalsa("marcar dentista quinta às 3", texto={
        "tipo": "evento", "titulo": "dentista", "data_texto": "quinta", "hora_texto": "3"})
    resposta = processar_audio(b"audio", "voz.ogg", deps)
    assert "quinta, 22/10 · 15:00–16:00" in resposta.texto
    assert len(CalendarioFalso.eventos) == 1


def test_audio_sem_fala(deps):
    deps.estado["ia"] = IAFalsa("")
    assert "Não consegui entender o áudio" in processar_audio(b"", "voz.ogg", deps).texto


def test_foto_de_nota_fiscal(deps):
    deps.estado["ia"] = IAFalsa(foto={
        "tipo": "gasto", "valor": 187.43, "descricao": "compras de mercado", "estabelecimento": "Andreazza",
        "categoria": "Mercado", "forma_pagamento": "débito", "quando": "16/10/2026"})
    [resposta] = processar_foto(b"jpg", "image/jpeg", "", deps)
    assert resposta.texto.startswith("📸 ✅ Gasto registrado: R$ 187,43")
    valor, categoria, origem, dia, loja = ultimo_gasto(deps)
    assert (float(valor), categoria, origem, dia, loja) == (187.43, "Mercado", "foto", date(2026, 10, 16), "Andreazza")


def test_foto_de_convite_com_dois_compromissos(deps):
    deps.estado["ia"] = IAFalsa(foto={"tipo": "eventos", "eventos": [
        {"titulo": "aniversário da Ana", "data_texto": "sábado", "hora_texto": "20h", "local": "Salão"},
        {"titulo": "ensaio", "data_texto": "24/10", "hora_texto": "14h"},
    ]})
    [resposta] = processar_foto(b"jpg", "image/jpeg", "", deps)
    assert resposta.texto.startswith("📸 📅 Agendei 2 compromissos:")
    assert "• sábado, 24/10 · 14:00–15:00 — Ensaio" in resposta.texto
    assert "• sábado, 24/10 · 20:00–21:00 — Aniversário da Ana" in resposta.texto
    assert resposta.botoes[0][0] == "↩️ Desfazer tudo"
    assert len(CalendarioFalso.eventos) == 2


def test_foto_sem_nota_usa_a_legenda(deps):
    deps.estado["ia"] = IAFalsa(foto={"tipo": "outro"}, texto={
        "tipo": "gasto", "valor": 250, "descricao": "gasolina", "categoria": "Transporte"})
    [resposta] = processar_foto(b"jpg", "image/jpeg", "gasolina 250", deps)
    assert "R$ 250,00" in resposta.texto
    assert ultimo_gasto(deps)[2] == "foto"


def test_foto_sem_nada(deps):
    deps.estado["ia"] = IAFalsa(foto={"tipo": "outro"})
    [resposta] = processar_foto(b"jpg", "image/jpeg", "", deps)
    assert "Não encontrei" in resposta.texto


# ---------------------------------------------------------------- Webhook

class TelegramFalso:
    enviados: list = []
    baixados: list = []

    def __init__(self, token):
        pass

    def enviar(self, chat_id, texto, botoes=None):
        TelegramFalso.enviados.append(texto)

    def digitando(self, chat_id):
        pass

    def baixar(self, arquivo_id):
        TelegramFalso.baixados.append(arquivo_id)
        return b"conteudo"


class IAWebhook:
    def transcrever(self, audio, nome_arquivo):
        return "qual a previsão do tempo?"

    def json(self, instrucoes, mensagem):
        return {"tipo": "outro"}

    def json_imagem(self, instrucoes, imagem, tipo, legenda):
        IAWebhook.tipo_recebido = tipo
        return {"tipo": "outro"}


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("VENIRE_SECRET", "senha")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(index, "Telegram", TelegramFalso)
    monkeypatch.setattr(index, "montar_dependencias", lambda config: Dependencias(
        ia=lambda: IAWebhook(), banco=None))
    TelegramFalso.enviados, TelegramFalso.baixados = [], []
    return TestClient(index.app)


def enviar(cliente, **conteudo):
    update = {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 42, "type": "private"},
                                          "from": {"first_name": "Lucas"}, **conteudo}}
    cabecalho = {"X-Telegram-Bot-Api-Secret-Token": carregar_config().segredo_webhook}
    assert cliente.post("/webhook", json=update, headers=cabecalho).status_code == 200


def test_webhook_com_audio(cliente):
    enviar(cliente, voice={"file_id": "voz1", "file_size": 9000})
    assert TelegramFalso.baixados == ["voz1"]
    assert TelegramFalso.enviados[0].startswith("🎙️ “qual a previsão do tempo?”")


def test_webhook_escolhe_a_maior_foto_que_cabe(cliente):
    enviar(cliente, photo=[
        {"file_id": "pequena", "file_size": 2_000},
        {"file_id": "media", "file_size": 300_000},
        {"file_id": "gigante", "file_size": 9_000_000},
    ])
    assert TelegramFalso.baixados == ["media"]
    assert "Não encontrei" in TelegramFalso.enviados[0]


def test_webhook_imagem_enviada_como_arquivo(cliente):
    enviar(cliente, document={"file_id": "doc", "mime_type": "image/png", "file_size": 50_000})
    assert TelegramFalso.baixados == ["doc"] and IAWebhook.tipo_recebido == "image/png"
    enviar(cliente, document={"file_id": "grande", "mime_type": "image/png", "file_size": 8_000_000})
    assert "grande demais" in TelegramFalso.enviados[-1]
