"""Testa a integração com a Google Planilha usando uma API do Google simulada."""

from datetime import date
import json

import httpx

from venire.servicos.gastos import Gasto
import base64

from venire.servicos.planilha import CABECALHO, Planilha, ler_conta_servico, ler_id_planilha


CONTA = '{"type": "service_account", "client_email": "robo@x.iam.gserviceaccount.com", "private_key": "k"}'


class GoogleFalso:
    def __init__(self):
        self.linhas: list[list] = []
        self.chamadas: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        caminho = request.url.path.split("/spreadsheets/planilha123planilha123", 1)[1]
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
    planilha = Planilha(CONTA, "planilha123planilha123",
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


def test_erro_403_explica_o_que_fazer():
    def google(request):
        return httpx.Response(403, json={"error": {"message": "The caller does not have permission"}})
    planilha = criar_planilha(google)
    try:
        planilha.testar()
    except Exception as erro:
        assert "compartilhe a planilha" in str(erro)
        assert "does not have permission" in str(erro)
    else:
        raise AssertionError("deveria ter falhado")


def test_json_invalido():
    try:
        Planilha("isso não é json", "planilha123")
    except Exception as erro:
        assert "JSON válido" in str(erro)
    else:
        raise AssertionError("deveria ter falhado")


def test_tolera_erros_de_copiar_e_colar():
    assert ler_conta_servico(CONTA)["client_email"].startswith("robo@")
    assert ler_conta_servico(f"  '{CONTA}'  ")["client_email"].startswith("robo@")
    assert ler_conta_servico("\ufeff" + CONTA)["client_email"].startswith("robo@")
    assert ler_conta_servico(base64.b64encode(CONTA.encode()).decode())["client_email"].startswith("robo@")


def test_completa_a_ultima_chave():
    bonito = json.dumps(json.loads(CONTA), indent=2)
    assert bonito.endswith("}")
    assert ler_conta_servico(bonito[:-1].rstrip())["client_email"].startswith("robo@")


def test_explica_json_cortado():
    try:
        ler_conta_servico(CONTA[:40])
    except Exception as erro:
        assert "40 caracteres" in str(erro)
        assert "começa com “{" in str(erro)
    else:
        raise AssertionError("deveria ter falhado")


def test_json_de_outra_coisa():
    try:
        ler_conta_servico('{"web": {"client_id": "x"}}')
    except Exception as erro:
        assert "client_email" in str(erro)
    else:
        raise AssertionError("deveria ter falhado")


ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_abcde"


def test_id_da_planilha():
    assert ler_id_planilha(ID) == ID
    assert ler_id_planilha(f"  {ID}\n") == ID
    assert ler_id_planilha(f"https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0") == ID
    for errado, trecho in [(CONTA, "JSON do robô"), ("abc\ndef", "não parece")]:
        try:
            ler_id_planilha(errado)
        except Exception as erro:
            assert trecho in str(erro)
        else:
            raise AssertionError("deveria ter falhado")
