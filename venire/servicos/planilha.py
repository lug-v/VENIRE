"""Cópia dos gastos na Google Planilha "Venire – Gastos".

O banco é a fonte oficial; a planilha é a sua "janela" para ver tudo em tabela.
Se a planilha falhar, o gasto continua salvo no banco.
"""

import json

import httpx

API = "https://sheets.googleapis.com/v4/spreadsheets/{id}"
ESCOPO = ["https://www.googleapis.com/auth/spreadsheets"]
CABECALHO = ["ID", "Data", "Valor", "Categoria", "Descrição", "Estabelecimento", "Pagamento", "Origem", "Mensagem"]


class Planilha:
    def __init__(self, conta_servico_json: str, planilha_id: str, cliente: httpx.Client | None = None):
        if not conta_servico_json or not planilha_id:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON ou GOOGLE_SHEET_ID não configurados.")
        self._info = json.loads(conta_servico_json)
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
        resposta.raise_for_status()
        return resposta.json()

    def _post(self, caminho: str, corpo: dict, **params) -> dict:
        resposta = self._cliente.post(self._url + caminho, headers=self._cabecalhos(), json=corpo, params=params)
        resposta.raise_for_status()
        return resposta.json()

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
