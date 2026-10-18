"""Cópia dos gastos na Google Planilha "Venire – Gastos".

O banco é a fonte oficial; a planilha é a sua "janela" para ver tudo em tabela.
Se a planilha falhar, o gasto continua salvo no banco.
"""

from urllib.parse import quote

import httpx

from venire.servicos.google import ErroGoogle, esquecer_token, ler_conta_servico, token, verificar

API = "https://sheets.googleapis.com/v4/spreadsheets/{id}"
ESCOPO = ["https://www.googleapis.com/auth/spreadsheets"]
CABECALHO = ["ID", "Data", "Valor", "Categoria", "Descrição", "Estabelecimento", "Pagamento", "Origem", "Mensagem"]
ABA_RECEITAS = "Receitas"
CABECALHO_RECEITAS = ["ID", "Data", "Valor", "Categoria", "Descrição", "De quem", "Origem", "Mensagem"]


ErroPlanilha = ErroGoogle

DICAS = {
    403: "o robô não tem acesso: compartilhe a planilha com o e-mail dele como Editor "
         "e confira se a Google Sheets API está ativada no projeto do Google Cloud",
    404: "planilha não encontrada: confira o GOOGLE_SHEET_ID (trecho entre /d/ e /edit)",
    400: "pedido inválido para a planilha",
}


def ler_id_planilha(texto: str) -> str:
    """Aceita o ID da planilha ou o link inteiro dela e devolve só o ID."""
    import re

    valor = (texto or "").strip().strip("'\"")
    no_link = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", valor)
    if no_link:
        return no_link.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", valor):
        return valor
    if valor.startswith("{"):
        raise ErroPlanilha("GOOGLE_SHEET_ID está com o JSON do robô. Cole nele só o ID da planilha "
                           "(o trecho entre /d/ e /edit no endereço) ou o link inteiro da planilha.")
    raise ErroPlanilha(f"GOOGLE_SHEET_ID não parece um ID de planilha (tem {len(valor)} caracteres). "
                       "Cole o trecho entre /d/ e /edit no endereço da planilha, ou o link inteiro.")


class Planilha:
    def __init__(self, conta_servico_json: str, planilha_id: str, cliente: httpx.Client | None = None):
        if not conta_servico_json or not planilha_id:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON ou GOOGLE_SHEET_ID não configurados.")
        self._info = ler_conta_servico(conta_servico_json)
        self._url = API.format(id=ler_id_planilha(planilha_id))
        self._cliente = cliente or httpx.Client(timeout=20)
        self._token = None

    def _cabecalhos(self) -> dict:
        return {"Authorization": f"Bearer {self._token or token(self._info, ESCOPO)}"}

    def _pedir(self, metodo: str, caminho: str, **kwargs) -> httpx.Response:
        resposta = self._cliente.request(metodo, self._url + caminho, headers=self._cabecalhos(), **kwargs)
        if resposta.status_code == 401 and not self._token:  # token vencido: renova e tenta de novo
            esquecer_token(self._info, ESCOPO)
            resposta = self._cliente.request(metodo, self._url + caminho, headers=self._cabecalhos(), **kwargs)
        verificar(resposta, DICAS)
        return resposta

    def _get(self, caminho: str, **params) -> dict:
        return self._pedir("GET", caminho, params=params).json()

    def _post(self, caminho: str, corpo: dict, **params) -> dict:
        return self._pedir("POST", caminho, json=corpo, params=params).json()

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

    def remover(self, gasto_id: str, aba_nome: str | None = None) -> bool:
        prefixo = f"/values/{quote(aba_nome)}!" if aba_nome else "/values/"
        ids = [linha[0] if linha else "" for linha in self._get(prefixo + "A:A").get("values", [])]
        if gasto_id not in ids:
            return False
        abas = self._get("", fields="sheets.properties(sheetId,title)")["sheets"]
        if aba_nome:
            aba = next(a["properties"]["sheetId"] for a in abas if a["properties"]["title"] == aba_nome)
        else:
            aba = abas[0]["properties"]["sheetId"]
        indice = ids.index(gasto_id)
        self._post(":batchUpdate", {"requests": [{"deleteDimension": {"range": {
            "sheetId": aba, "dimension": "ROWS", "startIndex": indice, "endIndex": indice + 1,
        }}}]})
        return True

    # ---------------------------------------------------------------- Receitas (aba própria)

    def _garantir_aba_receitas(self) -> None:
        abas = self._get("", fields="sheets.properties.title")["sheets"]
        if not any(a["properties"]["title"] == ABA_RECEITAS for a in abas):
            self._post(":batchUpdate", {"requests": [{"addSheet": {"properties": {"title": ABA_RECEITAS}}}]})
            self._post(f"/values/{quote(ABA_RECEITAS)}!A1:H1:append", {"values": [CABECALHO_RECEITAS]},
                       valueInputOption="RAW")

    def adicionar_receita(self, receita) -> None:
        self._garantir_aba_receitas()
        linha = [
            receita.id, receita.data.strftime("%d/%m/%Y"), float(receita.valor), receita.categoria,
            receita.descricao, receita.pagador, receita.origem, receita.texto_original,
        ]
        self._post(f"/values/{quote(ABA_RECEITAS)}!A:H:append", {"values": [linha]},
                   valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS")

    def remover_receita(self, receita_id: str) -> bool:
        return self.remover(receita_id, ABA_RECEITAS)
