"""Conversa com a IA do Groq e devolve JSON."""

import json

import httpx

URL = "https://api.groq.com/openai/v1/chat/completions"


class ErroIA(Exception):
    pass


class Groq:
    def __init__(self, chave: str, modelo: str, cliente: httpx.Client | None = None):
        if not chave:
            raise ErroIA("GROQ_API_KEY não configurada.")
        self._chave = chave
        self._modelo = modelo
        self._cliente = cliente or httpx.Client(timeout=30)

    def json(self, instrucoes: str, mensagem: str) -> dict:
        resposta = self._cliente.post(
            URL,
            headers={"Authorization": f"Bearer {self._chave}"},
            json={
                "model": self._modelo,
                "response_format": {"type": "json_object"},
                "reasoning_effort": "none",
                "temperature": 0.1,
                "max_completion_tokens": 800,
                "messages": [
                    {"role": "system", "content": instrucoes},
                    {"role": "user", "content": mensagem},
                ],
            },
        )
        if resposta.status_code != 200:
            raise ErroIA(f"Groq respondeu {resposta.status_code}: {resposta.text[:300]}")
        conteudo = resposta.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(conteudo)
        except json.JSONDecodeError as erro:
            raise ErroIA("A IA não devolveu um JSON válido.") from erro
