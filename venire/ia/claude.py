"""Conversa com o Claude (API da Anthropic): usado no filtro de ideias e nos posts do LinkedIn.

Tem a mesma interface do Groq (`json(instrucoes, mensagem)`), então o núcleo pode usar
um ou outro sem saber qual é.
"""

import json
import re

import httpx

from venire.ia.groq import ErroIA

URL = "https://api.anthropic.com/v1/messages"
VERSAO_API = "2023-06-01"


def extrair_json(texto: str) -> dict:
    """Lê o JSON da resposta, mesmo que venha dentro de ```json ... ``` ou com texto em volta."""
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fim <= inicio:
        raise ErroIA("O Claude não devolveu um JSON.")
    try:
        return json.loads(texto[inicio:fim + 1])
    except json.JSONDecodeError as erro:
        raise ErroIA("O Claude não devolveu um JSON válido.") from erro


class Claude:
    def __init__(self, chave: str, modelo: str, cliente: httpx.Client | None = None):
        if not chave:
            raise ErroIA("ANTHROPIC_API_KEY não configurada.")
        self._chave = chave
        self._modelo = modelo
        self._cliente = cliente or httpx.Client(timeout=50)

    def texto(self, instrucoes: str, mensagem: str, max_tokens: int = 4000) -> str:
        resposta = self._cliente.post(URL, headers={
            "x-api-key": self._chave,
            "anthropic-version": VERSAO_API,
            "content-type": "application/json",
        }, json={
            "model": self._modelo,
            "max_tokens": max_tokens,
            "system": instrucoes,
            "messages": [{"role": "user", "content": mensagem}],
        })
        if resposta.status_code != 200:
            raise ErroIA(f"Claude respondeu {resposta.status_code}: {resposta.text[:300]}")
        blocos = resposta.json().get("content", [])
        return "".join(b.get("text", "") for b in blocos if b.get("type") == "text").strip()

    def json(self, instrucoes: str, mensagem: str, max_tokens: int = 8000) -> dict:
        return extrair_json(self.texto(instrucoes, mensagem, max_tokens=max_tokens))
