"""Testa a integração com a Google Planilha usando uma API do Google simulada."""

from datetime import date
import json

import httpx

from venire.servicos.gastos import Gasto
from venire.servicos.planilha import CABECALHO, Planilha


class GoogleFalso:
    def __init__(self):
        self.linhas: list[list] = []
        self.chamadas: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        caminho = request.url.path.split("/spreadsheets/planilha123", 1)[1]
        self.chamadas.append(f"{request.method} {caminho}")
        if request.method == "GET" and caminho == "/values/A1:I1":
            return httpx.Response(200, json={"values": self.linhas[:1]})
        if request.method == "GET" and caminho == "/values/A:A":
            return httpx.Response(200, json={"values": [[linha[0]] for linha in self.linhas]})
        if request.method == "GET" and caminho == "":
            return httpx.Response(200, json={"sheets": [{"properties": {"sheetId": 0}}]})
        if request.method == "POST" and caminho.endswith(":append"):
            self.linhas.extend(json.loads(request.content)["values"])
            return httpx.Response(200, json={})
        if request.method == "POST" and caminho == ":batchUpdate":
            intervalo = json.loads(request.content)["requests"][0]["deleteDimension"]["range"]
            del self.linhas[intervalo["startIndex"]:intervalo["endIndex"]]
            return httpx.Response(200, json={})
        return httpx.Response(404)


def criar_planilha(google):
    planilha = Planilha('{"type": "service_account"}', "planilha123",
                        cliente=httpx.Client(transport=httpx.MockTransport(google)))
    planilha._token = "token-falso"  # pula o login real no Google
    return planilha


def test_adiciona_cabecalho_e_linhas_e_remove():
    google = GoogleFalso()
    planilha = criar_planilha(google)
    primeiro = Gasto(valor=250, data=date(2026, 10, 13), descricao="gasolina", categoria="Transporte")
    segundo = Gasto(valor=15, data=date(2026, 10, 13), descricao="energético", categoria="Alimentação")

    planilha.adicionar(primeiro)
    planilha.adicionar(segundo)
    assert google.linhas[0] == CABECALHO
    assert [linha[0] for linha in google.linhas[1:]] == [primeiro.id, segundo.id]
    assert google.linhas[1][1:4] == ["13/10/2026", 250.0, "Transporte"]

    assert planilha.remover(primeiro.id) is True
    assert [linha[0] for linha in google.linhas] == ["ID", segundo.id]
    assert planilha.remover("nao-existe") is False
