"""Porta de entrada do Venire na Vercel.

Rotas:
  GET  /                      → verifica se o Venire está no ar
  GET  /telegram/configurar   → registra o webhook no Telegram (protegido por senha)
  GET  /telegram/diagnostico  → mostra erros do webhook (protegido por senha)
  GET  /planilha/diagnostico  → testa o acesso à Google Planilha (protegido por senha)
  GET  /agenda/diagnostico    → testa o acesso ao Google Agenda (protegido por senha)
  POST /webhook               → recebe as mensagens e os cliques nos botões
"""

import hmac
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

# Permite importar o pacote "venire" quando a Vercel executa este arquivo.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from venire.banco import conectar  # noqa: E402
from venire.canais.telegram import ErroTelegram, Telegram  # noqa: E402
from venire.config import Config, carregar_config  # noqa: E402
from venire.ia.groq import Groq  # noqa: E402
from venire.nucleo.respostas import (  # noqa: E402
    Dependencias, Resposta, processar_audio, processar_botao, processar_foto, processar_texto,
)
from venire.servicos.calendario import Calendario  # noqa: E402
from venire.servicos.planilha import Planilha  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("venire")

app = FastAPI(title="Venire", docs_url=None, redoc_url=None)


def iguais(a: str, b: str) -> bool:
    """Compara senhas em tempo constante (evita ataques de temporização)."""
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


@app.get("/")
def status():
    config = carregar_config()
    return {
        "app": "Venire",
        "fase": 4,
        "telegram_configurado": bool(config.telegram_token),
        "senha_configurada": bool(config.venire_secret),
        "chat_autorizado": config.chat_id_autorizado is not None,
        "banco_configurado": bool(config.database_url),
        "ia_configurada": bool(config.groq_api_key),
        "planilha_configurada": bool(config.google_conta_servico_json and config.google_planilha_id),
        "agenda_configurada": bool(config.google_conta_servico_json and config.google_agenda_id),
    }


@app.get("/telegram/configurar")
def configurar_telegram(request: Request, chave: str = ""):
    config = carregar_config()
    if not iguais(chave, config.venire_secret):
        raise HTTPException(status_code=401, detail="Chave inválida.")
    # Usa sempre o endereço oficial (produção). Endereços de um deploy específico
    # mudam a cada publicação e ficam protegidos por login da Vercel.
    dominio = os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or request.headers.get("host")
    url = f"https://{dominio}/webhook"
    try:
        bot = Telegram(config.telegram_token).registrar_webhook(url, config.segredo_webhook)
    except ErroTelegram as erro:
        raise HTTPException(status_code=502, detail=f"O Telegram recusou: {erro}") from erro
    return {"ok": True, "bot": f"@{bot.get('username')}", "webhook": url}


@app.get("/telegram/diagnostico")
def diagnostico_telegram(chave: str = ""):
    """Mostra o que o Telegram sabe sobre o webhook: endereço, erros e mensagens na fila."""
    config = carregar_config()
    if not iguais(chave, config.venire_secret):
        raise HTTPException(status_code=401, detail="Chave inválida.")
    try:
        info = Telegram(config.telegram_token).chamar("getWebhookInfo")
    except ErroTelegram as erro:
        raise HTTPException(status_code=502, detail=f"O Telegram recusou: {erro}") from erro
    return {
        "webhook_registrado": info.get("url") or "(nenhum)",
        "endereco_oficial": os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or "(não informado pela Vercel)",
        "mensagens_na_fila": info.get("pending_update_count", 0),
        "ultimo_erro": info.get("last_error_message") or "(nenhum)",
        "ultimo_erro_em": info.get("last_error_date"),
    }


@app.get("/planilha/diagnostico")
def diagnostico_planilha(chave: str = ""):
    """Testa o acesso à Google Planilha e explica o erro, se houver."""
    config = carregar_config()
    if not iguais(chave, config.venire_secret):
        raise HTTPException(status_code=401, detail="Chave inválida.")
    resultado = {
        "json_cadastrado": bool(config.google_conta_servico_json),
        "id_da_planilha_tem_caracteres": len(config.google_planilha_id),
    }
    try:
        planilha = Planilha(config.google_conta_servico_json, config.google_planilha_id)
        resultado["email_do_robo"] = planilha.email_robo
        resultado["titulo_da_planilha"] = planilha.testar()
        resultado["ok"] = True
    except Exception as erro:
        resultado["ok"] = False
        resultado["erro"] = str(erro)[:600]
    return resultado


@app.get("/agenda/diagnostico")
def diagnostico_agenda(chave: str = ""):
    """Testa o acesso ao Google Agenda e explica o erro, se houver."""
    config = carregar_config()
    if not iguais(chave, config.venire_secret):
        raise HTTPException(status_code=401, detail="Chave inválida.")
    resultado = {"agenda_cadastrada": config.google_agenda_id or "(não cadastrada)"}
    try:
        calendario = Calendario(config.google_conta_servico_json, config.google_agenda_id)
        resultado["email_do_robo"] = calendario.email_robo
        resultado["nome_da_agenda"] = calendario.testar()
        resultado["ok"] = True
    except Exception as erro:
        resultado["ok"] = False
        resultado["erro"] = str(erro)[:600]
    return resultado


def montar_dependencias(config: Config) -> Dependencias:
    tem_planilha = bool(config.google_conta_servico_json and config.google_planilha_id)
    tem_agenda = bool(config.google_conta_servico_json and config.google_agenda_id)
    return Dependencias(
        ia=lambda: Groq(config.groq_api_key, config.groq_modelo),
        banco=lambda: conectar(config.database_url),
        planilha=(lambda: Planilha(config.google_conta_servico_json, config.google_planilha_id))
        if tem_planilha else None,
        calendario=(lambda: Calendario(config.google_conta_servico_json, config.google_agenda_id))
        if tem_agenda else None,
    )


LIMITE_IMAGEM = 4 * 1024 * 1024  # o Groq aceita imagens em base64 de até 4 MB


def responder_mensagem(mensagem: dict, telegram: Telegram, deps: Dependencias) -> list[Resposta]:
    """Escolhe o caminho certo para texto, áudio ou foto."""
    nome = (mensagem.get("from") or {}).get("first_name", "")
    audio = mensagem.get("voice") or mensagem.get("audio")
    if audio:
        conteudo = telegram.baixar(audio["file_id"])
        nome_arquivo = audio.get("file_name") or "audio.ogg"
        return [processar_audio(conteudo, nome_arquivo, deps, nome)]

    documento = mensagem.get("document") or {}
    fotos = mensagem.get("photo") or []
    if fotos or str(documento.get("mime_type", "")).startswith("image/"):
        if fotos:
            # O Telegram manda vários tamanhos; usa o maior que caiba no limite da IA.
            cabem = [f for f in fotos if f.get("file_size", 0) <= LIMITE_IMAGEM] or fotos[:1]
            arquivo_id, tipo = cabem[-1]["file_id"], "image/jpeg"
        else:
            if documento.get("file_size", 0) > LIMITE_IMAGEM:
                return [Resposta("📸 Essa imagem é grande demais (máximo 4 MB). Mande como foto, não como arquivo.")]
            arquivo_id, tipo = documento["file_id"], documento["mime_type"]
        return processar_foto(telegram.baixar(arquivo_id), tipo, mensagem.get("caption", ""), deps)

    return [processar_texto(mensagem.get("text", ""), deps, nome)]


@app.post("/webhook")
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    config = carregar_config()
    if not iguais(x_telegram_bot_api_secret_token, config.segredo_webhook):
        raise HTTPException(status_code=401, detail="Não autorizado.")

    update = await request.json()
    clique = update.get("callback_query") or {}
    mensagem = update.get("message") or clique.get("message") or {}
    chat = mensagem.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat.get("type") != "private":
        return {"ok": True}

    telegram = Telegram(config.telegram_token)

    # Uso individual: só o seu chat é atendido.
    if config.chat_id_autorizado is None:
        telegram.enviar(
            chat_id,
            "🔐 O Venire ainda não sabe quem é o dono.\n\n"
            f"Seu ID do Telegram é: {chat_id}\n\n"
            "Cadastre esse número na Vercel como TELEGRAM_CHAT_ID e faça um novo deploy.",
        )
        return {"ok": True}
    if chat_id != config.chat_id_autorizado:
        return {"ok": True}  # ignora estranhos sem responder

    deps = montar_dependencias(config)
    try:
        if clique:
            resposta = processar_botao(clique.get("data", ""), deps)
            telegram.responder_botao(clique["id"])
            telegram.editar(chat_id, mensagem["message_id"], resposta.texto, resposta.botoes)
        else:
            telegram.digitando(chat_id)
            for resposta in responder_mensagem(mensagem, telegram, deps):
                telegram.enviar(chat_id, resposta.texto, resposta.botoes)
    except Exception:
        # Sempre responde 200 ao Telegram; senão ele reenvia a mesma mensagem várias vezes.
        log.exception("Erro ao processar a mensagem")
        telegram.enviar(chat_id, "😕 Tive um problema para processar isso. Tente de novo em instantes.")
    return {"ok": True}
