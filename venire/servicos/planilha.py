"""Cópia dos gastos na Google Planilha "Venire – Gastos".

O banco é a fonte oficial; a planilha é a sua "janela" para ver tudo em tabela.
Se a planilha falhar, o gasto continua salvo no banco.
"""

import json

import httpx

API = "https://sheets.googleapis.com/v4/spreadsheets/{id}"
ESCOPO = ["https://www.googleapis.com/auth/spreadsheets"]
CABECALHO = ["ID", "Data", "Valor", "Categoria", "Descrição", "Estabelecimento", "Pagamento", "Origem", "Mensagem"]


class ErroPlanilha(Exception):
    pass


def _verificar(resposta: httpx.Response) -> None:
    """Transforma erros do Google em mensagens que dizem o que fazer."""
    if resposta.status_code < 400:
        return
    try:
        detalhe = resposta.json().get("error", {}).get("message", "")
    except ValueError:
        detalhe = resposta.text[:200]
    dicas = {
        403: "o robô não tem acesso: compartilhe a planilha com o e-mail dele como Editor "
             "e confira se a Google Sheets API está ativada no projeto do Google Cloud",
        404: "planilha não encontrada: confira o GOOGLE_SHEET_ID (trecho entre /d/ e /edit)",
        400: "pedido inválido para a planilha",
    }
    dica = dicas.get(resposta.status_code, "erro do Google")
    raise ErroPlanilha(f"{resposta.status_code} – {dica}. Detalhe do Google: {detalhe}")


def ler_conta_servico(texto: str) -> dict:
    """Lê o JSON da conta de serviço, tolerando erros comuns de copiar e colar."""
    import base64

    valor = (texto or "").strip().lstrip("\ufeff")
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
        raise ErroPlanilha(
            "GOOGLE_SERVICE_ACCOUNT_JSON não é um JSON válido: copie o arquivo inteiro, do { inicial ao } final. "
            f"O valor cadastrado começa com “{inicio}”, termina com “{valor[-1:] if valor else ''}” "
            f"e tem {len(valor)} caracteres (o arquivo certo começa com “{{”, termina com “}}” "
            "e costuma ter mais de 2.000 caracteres)."
        ) from erro
    faltando = [campo for campo in ("client_email", "private_key") if not info.get(campo)]
    if faltando:
        raise ErroPlanilha(f"O JSON cadastrado não é o da conta de serviço (faltam: {', '.join(faltando)}).")
    return info


class Planilha:
    def __init__(self, conta_servico_json: str, planilha_id: str, cliente: httpx.Client | None = None):
        if not conta_servico_json or not planilha_id:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON ou GOOGLE_SHEET_ID não configurados.")
        self._info = ler_conta_servico(conta_servico_json)
        self._url = API.format(id=planilha_id)
        self._cliente = cliente or httpx.Client(timeout=20)
        self._token = None

    def _cabecalhos(self) -> dict:
        if self._token is None:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account

            credenciais = service_account.Credentials.from_service_account_info(self._info, scopes=ESCOPO)
            credenciais.refresh(Request())
            self._token = credenciais.token
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, caminho: str, **params) -> dict:
        resposta = self._cliente.get(self._url + caminho, headers=self._cabecalhos(), params=params)
        _verificar(resposta)
        return resposta.json()

    def _post(self, caminho: str, corpo: dict, **params) -> dict:
        resposta = self._cliente.post(self._url + caminho, headers=self._cabecalhos(), json=corpo, params=params)
        _verificar(resposta)
        return resposta.json()

    @property
    def email_robo(self) -> str:
        return self._info.get("client_email", "")

    def testar(self) -> str:
        """Lê o título da planilha. Serve para o diagnóstico."""
        return self._get("", fields="properties.title")["properties"]["title"]

    def _garantir_cabecalho(self) -> None:
        primeira = self._get("/values/A1:I1").get("values", [])
        if not primeira:
            self._post("/values/A1:I1:append", {"values": [CABECALHO]}, valueInputOption="RAW")

    def adicionar(self, gasto) -> None:
        self._garantir_cabecalho()
        linha = [
            gasto.id, gasto.data.strftime("%d/%m/%Y"), float(gasto.valor), gasto.categoria,
            gasto.descricao, gasto.estabelecimento, gasto.forma_pagamento, gasto.origem, gasto.texto_original,
        ]
        self._post("/values/A:I:append", {"values": [linha]},
                   valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS")

    def remover(self, gasto_id: str) -> bool:
        ids = [linha[0] if linha else "" for linha in self._get("/values/A:A").get("values", [])]
        if gasto_id not in ids:
            return False
        aba = self._get("", fields="sheets.properties.sheetId")["sheets"][0]["properties"]["sheetId"]
        indice = ids.index(gasto_id)
        self._post(":batchUpdate", {"requests": [{"deleteDimension": {"range": {
            "sheetId": aba, "dimension": "ROWS", "startIndex": indice, "endIndex": indice + 1,
        }}}]})
        return True
