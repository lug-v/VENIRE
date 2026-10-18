"""Conversa com a IA do Groq: texto e imagem viram JSON; áudio vira texto."""

import base64
import json

import httpx

URL_CHAT = "https://api.groq.com/openai/v1/chat/completions"
URL_AUDIO = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELO_AUDIO = "whisper-large-v3-turbo"


class ErroIA(Exception):
    pass


class Groq:
    def __init__(self, chave: str, modelo: str, cliente: httpx.Client | None = None):
        if not chave:
            raise ErroIA("GROQ_API_KEY não configurada.")
        self._chave = chave
        self._modelo = modelo
        self._cliente = cliente or httpx.Client(timeout=45)

    @property
    def _autorizacao(self) -> dict:
        return {"Authorization": f"Bearer {self._chave}"}

    def _verificar(self, resposta: httpx.Response) -> None:
        if resposta.status_code != 200:
            raise ErroIA(f"Groq respondeu {resposta.status_code}: {resposta.text[:300]}")

    def _chat_json(self, mensagens: list[dict], max_tokens: int = 800) -> dict:
        resposta = self._cliente.post(URL_CHAT, headers=self._autorizacao, json={
            "model": self._modelo,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
            "temperature": 0.1,
            "max_completion_tokens": max_tokens,
            "messages": mensagens,
        })
        self._verificar(resposta)
        conteudo = resposta.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(conteudo)
        except json.JSONDecodeError as erro:
            raise ErroIA("A IA não devolveu um JSON válido.") from erro

    def json(self, instrucoes: str, mensagem: str) -> dict:
        return self._chat_json([
            {"role": "system", "content": instrucoes},
            {"role": "user", "content": mensagem},
        ])

    def json_imagem(self, instrucoes: str, imagem: bytes, tipo: str = "image/jpeg", legenda: str = "") -> dict:
        """Envia a foto (em base64) junto com as instruções e devolve JSON."""
        endereco = f"data:{tipo};base64,{base64.b64encode(imagem).decode()}"
        texto = f"Legenda enviada junto com a foto: “{legenda}”" if legenda else "A foto veio sem legenda."
        return self._chat_json([
            {"role": "system", "content": instrucoes},
            {"role": "user", "content": [
                {"type": "text", "text": texto},
                {"type": "image_url", "image_url": {"url": endereco}},
            ]},
        ], max_tokens=1500)

    def transcrever(self, audio: bytes, nome_arquivo: str = "audio.ogg") -> str:
        """Transforma o áudio em texto com o Whisper (em português)."""
        resposta = self._cliente.post(
            URL_AUDIO,
            headers=self._autorizacao,
            data={"model": MODELO_AUDIO, "language": "pt", "response_format": "json", "temperature": "0"},
            files={"file": (nome_arquivo, audio)},
        )
        self._verificar(resposta)
        return str(resposta.json().get("text", "")).strip()
