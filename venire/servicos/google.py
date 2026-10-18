"""Login do robô (conta de serviço) no Google e tratamento de erros comuns."""

import base64
import json

import time

import httpx


class ErroGoogle(Exception):
    pass


def ler_conta_servico(texto: str) -> dict:
    """Lê o JSON da conta de serviço, tolerando erros comuns de copiar e colar."""
    valor = (texto or "").strip().lstrip("﻿")
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
        valor = valor[1:-1].strip()  # aspas sobrando em volta
    if not valor.startswith("{"):
        try:  # aceita também o arquivo convertido em base64
            valor = base64.b64decode(valor, validate=True).decode("utf-8").strip()
        except Exception:
            pass
    original = valor
    if valor.startswith("{") and not valor.endswith("}"):
        valor += "\n}"  # a última chave costuma ficar de fora ao copiar e colar
    try:
        info = json.loads(valor)
    except json.JSONDecodeError as erro:
        valor = original
        inicio = valor[:12].replace("\n", " ")
        raise ErroGoogle(
            "GOOGLE_SERVICE_ACCOUNT_JSON não é um JSON válido: copie o arquivo inteiro, do { inicial ao } final. "
            f"O valor cadastrado começa com “{inicio}”, termina com “{valor[-1:] if valor else ''}” "
            f"e tem {len(valor)} caracteres (o arquivo certo começa com “{{”, termina com “}}” "
            "e costuma ter mais de 2.000 caracteres)."
        ) from erro
    faltando = [campo for campo in ("client_email", "private_key") if not info.get(campo)]
    if faltando:
        raise ErroGoogle(f"O JSON cadastrado não é o da conta de serviço (faltam: {', '.join(faltando)}).")
    return info


_tokens: dict[tuple, tuple[str, float]] = {}
VALIDADE = 45 * 60  # o Google aceita o token por 1 hora; renovamos antes disso


def token(info: dict, escopos: list[str]) -> str:
    """Faz o login do robô e reaproveita o token por até 45 minutos.

    Na Vercel a função pode ficar "acordada" por horas (o cron chama a cada 5 minutos),
    então o token precisa ser renovado antes de expirar.
    """
    chave = (info.get("client_email"), tuple(escopos))
    guardado = _tokens.get(chave)
    if guardado and time.monotonic() < guardado[1]:
        return guardado[0]
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    credenciais = service_account.Credentials.from_service_account_info(info, scopes=escopos)
    credenciais.refresh(Request())
    _tokens[chave] = (credenciais.token, time.monotonic() + VALIDADE)
    return credenciais.token


def esquecer_token(info: dict, escopos: list[str]) -> None:
    """Descarta o token guardado (usado quando o Google responde 401)."""
    _tokens.pop((info.get("client_email"), tuple(escopos)), None)


def verificar(resposta: httpx.Response, dicas: dict[int, str]) -> None:
    """Transforma erros do Google em mensagens que dizem o que fazer."""
    if resposta.status_code < 400:
        return
    try:
        detalhe = resposta.json().get("error", {}).get("message", "")
    except ValueError:
        detalhe = resposta.text[:200]
    dica = dicas.get(resposta.status_code, "erro do Google")
    raise ErroGoogle(f"{resposta.status_code} – {dica}. Detalhe do Google: {detalhe}")
