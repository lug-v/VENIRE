"""Porta de entrada do Venire na Vercel.

Rotas:
  GET  /                      → verifica se o Venire está no ar
  GET  /telegram/configurar   → registra o webhook no Telegram (protegido por senha)
  POST /webhook               → recebe as mensagens do Telegram
"""

import hmac
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

# Permite importar o pacote "venire" quando a Vercel executa este arquivo.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from venire.canais.telegram import ErroTelegram, Telegram  # noqa: E402
from venire.config import carregar_config  # noqa: E402
from venire.nucleo.respostas import responder  # noqa: E402

app = FastAPI(title="Venire", docs_url=None, redoc_url=None)


def iguais(a: str, b: str) -> bool:
    """Compara senhas em tempo constante (evita ataques de temporização)."""
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


@app.get("/")
def status():
    config = carregar_config()
    return {
        "app": "Venire",
        "fase": 1,
        "telegram_configurado": bool(config.telegram_token),
        "senha_configurada": bool(config.venire_secret),
        "chat_autorizado": config.chat_id_autorizado is not None,
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


@app.post("/webhook")
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    config = carregar_config()
    if not iguais(x_telegram_bot_api_secret_token, config.segredo_webhook):
        raise HTTPException(status_code=401, detail="Não autorizado.")

    update = await request.json()
    mensagem = update.get("message") or {}
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

    nome = (mensagem.get("from") or {}).get("first_name", "")
    telegram.enviar(chat_id, responder(mensagem.get("text", ""), nome))
    return {"ok": True}
