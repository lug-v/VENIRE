"""Conversa com a API do Telegram: enviar mensagens e registrar o webhook."""

import httpx

API = "https://api.telegram.org/bot{token}/{metodo}"


class ErroTelegram(Exception):
    pass


class Telegram:
    def __init__(self, token: str, cliente: httpx.Client | None = None):
        if not token:
            raise ErroTelegram("TELEGRAM_BOT_TOKEN não configurado.")
        self._token = token
        self._cliente = cliente or httpx.Client(timeout=15)

    def chamar(self, metodo: str, **dados) -> dict:
        resposta = self._cliente.post(API.format(token=self._token, metodo=metodo), json=dados)
        corpo = resposta.json()
        if not corpo.get("ok"):
            # A descrição do erro vem do Telegram; o token nunca aparece na mensagem.
            raise ErroTelegram(corpo.get("description", f"Falha em {metodo}"))
        return corpo["result"]

    def enviar(self, chat_id: int, texto: str) -> dict:
        return self.chamar("sendMessage", chat_id=chat_id, text=texto)

    def registrar_webhook(self, url: str, segredo: str) -> dict:
        self.chamar(
            "setWebhook",
            url=url,
            secret_token=segredo,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        self.chamar("setMyCommands", commands=[
            {"command": "start", "description": "Começar a usar o Venire"},
            {"command": "ajuda", "description": "Ver o que o Venire sabe fazer"},
        ])
        return self.chamar("getMe")
