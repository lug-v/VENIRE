"""Conversa com a API do Telegram: enviar mensagens, botões e registrar o webhook."""

import httpx

API = "https://api.telegram.org/bot{token}/{metodo}"


class ErroTelegram(Exception):
    pass


def teclado(botoes: list[tuple[str, str]]) -> dict | None:
    """Transforma [(rótulo, dado)] em botões do Telegram, todos na mesma linha."""
    if not botoes:
        return None
    return {"inline_keyboard": [[{"text": rotulo, "callback_data": dado} for rotulo, dado in botoes]]}


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

    def enviar(self, chat_id: int, texto: str, botoes: list[tuple[str, str]] | None = None) -> dict:
        dados = {"chat_id": chat_id, "text": texto}
        if teclado(botoes or []):
            dados["reply_markup"] = teclado(botoes)
        return self.chamar("sendMessage", **dados)

    def editar(self, chat_id: int, mensagem_id: int, texto: str,
               botoes: list[tuple[str, str]] | None = None) -> dict:
        dados = {"chat_id": chat_id, "message_id": mensagem_id, "text": texto}
        if teclado(botoes or []):
            dados["reply_markup"] = teclado(botoes)
        return self.chamar("editMessageText", **dados)

    def responder_botao(self, callback_id: str, aviso: str = "") -> dict:
        return self.chamar("answerCallbackQuery", callback_query_id=callback_id, text=aviso)

    def digitando(self, chat_id: int) -> None:
        try:
            self.chamar("sendChatAction", chat_id=chat_id, action="typing")
        except ErroTelegram:
            pass

    def registrar_webhook(self, url: str, segredo: str) -> dict:
        self.chamar(
            "setWebhook",
            url=url,
            secret_token=segredo,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        self.chamar("setMyCommands", commands=[
            {"command": "agenda", "description": "Compromissos de hoje e amanhã"},
            {"command": "gastos", "description": "Resumo dos gastos do mês"},
            {"command": "semana", "description": "Resumo dos gastos da semana"},
            {"command": "ajuda", "description": "Ver o que o Venire sabe fazer"},
        ])
        return self.chamar("getMe")
