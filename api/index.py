"""Porta de entrada do Venire na Vercel.

Rotas:
  GET  /                      → verifica se o Venire está no ar
  GET  /telegram/configurar   → registra o webhook no Telegram (protegido por senha)
  GET  /telegram/diagnostico  → mostra erros do webhook (protegido por senha)
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
from venire.nucleo.respostas import Dependencias, processar_botao, processar_texto  # noqa: E402
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
        "fase": 2,
        "telegram_configurado": bool(config.telegram_token),
        "senha_configurada": bool(config.venire_secret),
        "chat_autorizado": config.chat_id_autorizado is not None,
        "banco_configurado": bool(config.database_url),
        "ia_configurada": bool(config.groq_api_key),
        "planilha_configurada": bool(config.google_conta_servico_json and config.google_planilha_id),
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


def montar_dependencias(config: Config) -> Dependencias:
    tem_planilha = bool(config.google_conta_servico_json and config.google_planilha_id)
    return Dependencias(
        ia=lambda: Groq(config.groq_api_key, config.groq_modelo),
        banco=lambda: conectar(config.database_url),
        planilha=(lambda: Planilha(config.google_conta_servico_json, config.google_planilha_id))
        if tem_planilha else None,
    )


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
            telegram.editar(chat_id, mensagem["message_id"], resposta.texto)
        else:
            telegram.digitando(chat_id)
            nome = (mensagem.get("from") or {}).get("first_name", "")
            resposta = processar_texto(mensagem.get("text", ""), deps, nome)
            telegram.enviar(chat_id, resposta.texto, resposta.botoes)
    except Exception:
        # Sempre responde 200 ao Telegram; senão ele reenvia a mesma mensagem várias vezes.
        log.exception("Erro ao processar a mensagem")
        telegram.enviar(chat_id, "😕 Tive um problema para processar isso. Tente de novo em instantes.")
    return {"ok": True}
