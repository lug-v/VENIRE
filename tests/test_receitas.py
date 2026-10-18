"""Testes da fase 7: receitas, saldo do mês, projeção e fechamento. Referência: segunda, 19/10/2026."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import json
import os

import httpx
import psycopg
import pytest

from tests.banco_teste import recriar_tabelas

from venire.nucleo import automacoes
from venire.nucleo.interpretador import INSTRUCOES, INSTRUCOES_FOTO
from venire.nucleo.respostas import Dependencias, processar_botao, processar_foto, processar_texto
from venire.servicos import financas, gastos, receitas
from venire.servicos.planilha import ABA_RECEITAS, CABECALHO_RECEITAS, Planilha

HOJE = date(2026, 10, 19)
URL_TESTE = os.getenv("TEST_DATABASE_URL", "")


# ---------------------------------------------------------------- Sem banco

def test_de_dados_valida_categoria_e_data():
    receita = receitas.de_dados({"valor": "2000,50", "categoria_receita": "Salário", "quando": "ontem",
                                 "pagador": "Empresa X"}, "recebi 2000,50 de salário ontem", HOJE)
    assert receita.valor == Decimal("2000.50") and receita.categoria == "Salário"
    assert receita.data == date(2026, 10, 18)
    assert receitas.de_dados({"valor": 50, "categoria_receita": "Loteria"}, "ganhei 50", HOJE).categoria == "Outros"
    assert receitas.de_dados({"valor": 0}, "nada", HOJE) is None
    assert receitas.de_dados({"valor": "abc"}, "nada", HOJE) is None


def test_confirmacao():
    receita = receitas.Receita(valor=800, data=HOJE, categoria="Comissão", descricao="venda", pagador="Cliente Y")
    assert receitas.confirmacao(receita) == (
        "💵 Receita registrada: R$ 800,00\n📂 Comissão · venda\n📅 19/10/2026\n👤 Cliente Y")


def test_prompts_conhecem_receita():
    assert "receita" in INSTRUCOES and "categoria_receita" in INSTRUCOES
    assert "receita" in INSTRUCOES_FOTO


# ---------------------------------------------------------------- Planilha (aba Receitas)

class GoogleFalso:
    def __init__(self):
        self.abas = {"Gastos": [], ABA_RECEITAS: None}  # None = aba ainda não existe
        self.abas.pop(ABA_RECEITAS)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote
        caminho = unquote(request.url.path.split("/spreadsheets/planilha123planilha123", 1)[1])
        nomes = list(self.abas)
        if request.method == "GET" and caminho == "":
            return httpx.Response(200, json={"sheets": [
                {"properties": {"sheetId": i, "title": n}} for i, n in enumerate(nomes)]})
        if request.method == "GET" and caminho.startswith("/values/"):
            aba = caminho[len("/values/"):].split("!")[0] if "!" in caminho else nomes[0]
            return httpx.Response(200, json={"values": [[l[0]] for l in self.abas[aba]]})
        if request.method == "POST" and caminho.endswith(":append"):
            aba = caminho[len("/values/"):].split("!")[0] if "!" in caminho else nomes[0]
            self.abas[aba].extend(json.loads(request.content)["values"])
            return httpx.Response(200, json={})
        if request.method == "POST" and caminho == ":batchUpdate":
            pedido = json.loads(request.content)["requests"][0]
            if "addSheet" in pedido:
                self.abas[pedido["addSheet"]["properties"]["title"]] = []
            else:
                faixa = pedido["deleteDimension"]["range"]
                del self.abas[nomes[faixa["sheetId"]]][faixa["startIndex"]:faixa["endIndex"]]
            return httpx.Response(200, json={})
        return httpx.Response(404)


def test_planilha_cria_aba_receitas_e_remove():
    google = GoogleFalso()
    planilha = Planilha('{"type": "service_account", "client_email": "r@x", "private_key": "k"}',
                        "planilha123planilha123", cliente=httpx.Client(transport=httpx.MockTransport(google)))
    planilha._token = "token-falso"
    primeira = receitas.Receita(valor=2000, data=HOJE, categoria="Salário")
    segunda = receitas.Receita(valor=50, data=HOJE, categoria="Extra", pagador="Ana")
    planilha.adicionar_receita(primeira)
    planilha.adicionar_receita(segunda)
    linhas = google.abas[ABA_RECEITAS]
    assert linhas[0] == CABECALHO_RECEITAS
    assert linhas[2][:6] == [segunda.id, "19/10/2026", 50.0, "Extra", "", "Ana"]
    assert planilha.remover_receita(primeira.id) is True
    assert [l[0] for l in google.abas[ABA_RECEITAS]] == ["ID", segunda.id]
    assert google.abas["Gastos"] == []  # a aba de gastos não foi tocada


# ---------------------------------------------------------------- Com banco

class IAFalsa:
    def __init__(self, resposta):
        self.resposta = resposta

    def json(self, instrucoes, mensagem):
        return self.resposta

    def json_imagem(self, instrucoes, imagem, tipo, legenda):
        return self.resposta


class PlanilhaFalsa:
    receitas: dict = {}
    quebrada = False

    def adicionar_receita(self, receita):
        if PlanilhaFalsa.quebrada:
            raise RuntimeError("Google fora do ar")
        PlanilhaFalsa.receitas[receita.id] = receita

    def remover_receita(self, receita_id):
        return PlanilhaFalsa.receitas.pop(receita_id, None) is not None


@pytest.fixture
def deps():
    if not URL_TESTE:
        pytest.skip("TEST_DATABASE_URL não configurada")
    recriar_tabelas(URL_TESTE)

    @contextmanager
    def banco():
        with psycopg.connect(URL_TESTE) as conexao:
            yield conexao
            conexao.commit()

    PlanilhaFalsa.receitas = {}
    PlanilhaFalsa.quebrada = False
    estado = {"ia": IAFalsa({})}
    dependencias = Dependencias(ia=lambda: estado["ia"], banco=banco,
                                planilha=lambda: PlanilhaFalsa(), hoje=lambda: HOJE)
    dependencias.estado = estado
    return dependencias


def gastar(deps, valor, dia, categoria="Mercado"):
    with deps.banco() as conexao:
        gastos.salvar(conexao, gastos.Gasto(valor=valor, data=dia, categoria=categoria))


def receber(deps, valor, dia, categoria="Salário"):
    with deps.banco() as conexao:
        receitas.salvar(conexao, receitas.Receita(valor=valor, data=dia, categoria=categoria))


def test_registra_receita_mostra_saldo_e_desfaz(deps):
    gastar(deps, 300, date(2026, 10, 5))
    deps.estado["ia"] = IAFalsa({"tipo": "receita", "valor": 2000, "categoria_receita": "Salário"})
    resposta = processar_texto("ganhei 2000 reais", deps)
    assert resposta.texto.startswith("💵 Receita registrada: R$ 2.000,00")
    assert "💰 Saldo do mês: sobra de R$ 1.700,00" in resposta.texto
    texto_botao, dado = resposta.botoes[0]
    assert texto_botao == "↩️ Desfazer" and dado.startswith("dr:")
    assert len(PlanilhaFalsa.receitas) == 1

    assert processar_botao(dado, deps).texto == "↩️ Receita desfeita."
    assert PlanilhaFalsa.receitas == {}
    assert processar_botao(dado, deps).texto == "Essa receita já tinha sido removida."


def test_planilha_fora_do_ar_nao_perde_a_receita(deps):
    PlanilhaFalsa.quebrada = True
    deps.estado["ia"] = IAFalsa({"tipo": "receita", "valor": 50})
    resposta = processar_texto("recebi 50 reais", deps)
    assert "⚠️ Salvei no banco" in resposta.texto
    with deps.banco() as conexao:
        assert receitas.total(conexao, HOJE, HOJE) == Decimal("50.00")


def test_foto_de_pix_recebido(deps):
    deps.estado["ia"] = IAFalsa({"tipo": "receita", "valor": 120, "pagador": "João",
                                 "categoria_receita": "Reembolso"})
    respostas = processar_foto(b"img", "image/jpeg", "", deps)
    assert respostas[0].texto.startswith("📸 💵 Receita registrada: R$ 120,00")
    with deps.banco() as conexao:
        assert conexao.execute("SELECT source, payer FROM incomes").fetchone() == ("foto", "João")


def test_saldo_vazio_e_com_projecao(deps):
    assert "Ainda não há" in processar_texto("/saldo", deps).texto
    receber(deps, 3000, date(2026, 9, 5))            # setembro: salário
    gastar(deps, 1200, date(2026, 9, 10), "Moradia")  # setembro: aluguel
    receber(deps, 3000, date(2026, 10, 5))
    gastar(deps, 1200, date(2026, 10, 10), "Moradia")
    gastar(deps, 380, date(2026, 10, 12))             # variável: 380 em 19 dias = 20/dia
    texto = processar_texto("/saldo", deps).texto
    assert texto.splitlines()[:4] == [
        "💰 Outubro até agora",
        "Entrou R$ 3.000,00 · Saiu R$ 1.580,00",
        "Saldo: sobra de R$ 1.420,00",
        "Você já gastou 53% do que entrou.",
    ]
    # projeção: 1200 fixo + 20 × 31 dias = 1820 → sobra 1180
    assert "📈 No ritmo atual, outubro fecha com sobra de ~R$ 1.180,00." in texto


def test_projecao_usa_salario_do_mes_anterior_se_ainda_nao_caiu(deps):
    receber(deps, 3000, date(2026, 9, 5))
    gastar(deps, 1200, date(2026, 9, 10), "Moradia")
    gastar(deps, 190, date(2026, 10, 3))  # aluguel de outubro ainda não pago
    with deps.banco() as conexao:
        previsto = financas.projecao(conexao, HOJE)
    assert previsto.entrou == Decimal("3000")
    assert previsto.saiu == Decimal("1510.00")  # 1200 (setembro) + 10/dia × 31


def test_fechamento_positivo_e_negativo(deps):
    receber(deps, 3000, date(2026, 8, 5))
    gastar(deps, 2000, date(2026, 8, 10))
    receber(deps, 3000, date(2026, 9, 5))
    gastar(deps, 2500, date(2026, 9, 10), "Lazer")
    gastar(deps, 1000, date(2026, 9, 11), "Mercado")
    with deps.banco() as conexao:
        agosto = financas.fechamento(conexao, date(2026, 8, 1))
        setembro = financas.fechamento(conexao, date(2026, 9, 30))
        julho = financas.fechamento(conexao, date(2026, 7, 1))
    assert "✅ Sobrou: R$ 1.000,00 (33% da renda)" in agosto
    assert setembro.startswith("📊 Setembro fechou")
    assert "⚠️ Faltou: R$ 500,00 (17% acima da renda)" in setembro
    assert "▼ Saldo R$ 1.500,00 pior que em agosto" in setembro
    assert "• Lazer: R$ 2.500,00" in setembro
    assert julho == ""


def test_dia_1_envia_fechamento_uma_vez(deps):
    receber(deps, 3000, date(2026, 10, 5))
    gastar(deps, 1000, date(2026, 10, 10))
    deps.hoje = lambda: date(2026, 11, 1)  # domingo, 1º de novembro
    enviadas = []
    assert automacoes.executar_manha(deps, enviadas.append) == {"enviados": 2}
    assert enviadas[1].startswith("📊 Outubro fechou")
    assert automacoes.executar_manha(deps, enviadas.append) == {"enviados": 0}
