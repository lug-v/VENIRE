"""Testes da fase 2: gastos.

A IA e a planilha são simuladas. O banco é um PostgreSQL de verdade quando a
variável TEST_DATABASE_URL existe; sem ela, os testes de banco são pulados.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import os

import psycopg
import pytest

from venire.nucleo.respostas import Dependencias, processar_botao, processar_texto
from venire.servicos import gastos

HOJE = date(2026, 10, 13)
URL_TESTE = os.getenv("TEST_DATABASE_URL", "")


class IAFalsa:
    """Devolve respostas prontas, como se fosse o Groq."""

    def __init__(self, resposta: dict):
        self.resposta = resposta
        self.recebido = None

    def json(self, instrucoes, mensagem):
        self.recebido = mensagem
        return self.resposta


class PlanilhaFalsa:
    linhas: dict = {}
    quebrada = False

    def adicionar(self, gasto):
        if PlanilhaFalsa.quebrada:
            raise RuntimeError("planilha fora do ar")
        PlanilhaFalsa.linhas[gasto.id] = gasto

    def remover(self, gasto_id):
        return PlanilhaFalsa.linhas.pop(gasto_id, None) is not None


# ---------------------------------------------------------------- Sem banco

def test_interpreta_gasolina():
    ia = IAFalsa({"eh_gasto": True, "valor": 250, "descricao": "gasolina", "categoria": "Transporte",
                  "forma_pagamento": "não informado", "quando": "", "confianca": 0.95})
    gasto = gastos.interpretar("GASTEI 250 GASOLINA", ia, HOJE)
    assert gasto.valor == Decimal("250.00")
    assert gasto.categoria == "Transporte"
    assert gasto.data == HOJE


def test_interpreta_energetico_ontem_no_debito():
    ia = IAFalsa({"eh_gasto": True, "valor": "15,50", "descricao": "energético", "categoria": "Alimentação",
                  "forma_pagamento": "débito", "quando": "ontem"})
    gasto = gastos.interpretar("energético 15,50 ontem no débito", ia, HOJE)
    assert gasto.valor == Decimal("15.50")
    assert gasto.data == date(2026, 10, 12)
    assert gasto.forma_pagamento == "débito"


def test_categoria_invalida_vira_outros():
    ia = IAFalsa({"eh_gasto": True, "valor": 10, "categoria": "Viagem espacial", "forma_pagamento": "cheque"})
    gasto = gastos.interpretar("gastei 10", ia, HOJE)
    assert gasto.categoria == "Outros"
    assert gasto.forma_pagamento == "não informado"


@pytest.mark.parametrize("resposta", [
    {"eh_gasto": False},
    {"eh_gasto": True, "valor": 0},
    {"eh_gasto": True, "valor": "abc"},
])
def test_nao_e_gasto(resposta):
    assert gastos.interpretar("marcar reunião quinta", IAFalsa(resposta), HOJE) is None


def test_formatar_reais():
    assert gastos.formatar_reais(Decimal("1234.5")) == "R$ 1.234,50"
    assert gastos.formatar_reais(15) == "R$ 15,00"


def test_comandos_sem_banco():
    deps = Dependencias(ia=lambda: None, banco=None, hoje=lambda: HOJE)
    assert "GASTEI 250 GASOLINA" in processar_texto("/start", deps).texto
    assert "Não conheço" in processar_texto("/xyz", deps).texto


# ---------------------------------------------------------------- Com banco

@pytest.fixture
def deps():
    if not URL_TESTE:
        pytest.skip("TEST_DATABASE_URL não configurada")
    with psycopg.connect(URL_TESTE, autocommit=True) as conexao:
        conexao.execute("DROP TABLE IF EXISTS expenses")
        gastos.criar_tabela(conexao)

    @contextmanager
    def banco():
        with psycopg.connect(URL_TESTE) as conexao:
            yield conexao
            conexao.commit()

    PlanilhaFalsa.linhas = {}
    PlanilhaFalsa.quebrada = False
    estado = {"ia": IAFalsa({})}
    dependencias = Dependencias(ia=lambda: estado["ia"], banco=banco,
                                planilha=lambda: PlanilhaFalsa(), hoje=lambda: HOJE)
    dependencias.estado = estado
    return dependencias


def registrar(deps, texto, **campos):
    deps.estado["ia"] = IAFalsa({"eh_gasto": True, "confianca": 0.9, **campos})
    return processar_texto(texto, deps)


def test_registra_salva_e_copia_para_planilha(deps):
    resposta = registrar(deps, "GASTEI 250 GASOLINA", valor=250, descricao="gasolina", categoria="Transporte")
    assert "R$ 250,00" in resposta.texto
    assert resposta.botoes[0][0] == "↩️ Desfazer"
    assert len(PlanilhaFalsa.linhas) == 1
    with deps.banco() as conexao:
        linha = conexao.execute("SELECT amount, category, spent_on FROM expenses").fetchone()
    assert linha == (Decimal("250.00"), "Transporte", HOJE)


def test_desfazer_remove_do_banco_e_da_planilha(deps):
    resposta = registrar(deps, "gasto 15 reais energético", valor=15, categoria="Alimentação")
    dado = resposta.botoes[0][1]
    assert "desfeito" in processar_botao(dado, deps).texto
    assert PlanilhaFalsa.linhas == {}
    assert "já tinha sido removido" in processar_botao(dado, deps).texto


def test_planilha_fora_do_ar_nao_perde_o_gasto(deps):
    PlanilhaFalsa.quebrada = True
    resposta = registrar(deps, "gastei 40 no mercado", valor=40, categoria="Mercado")
    assert "não consegui copiar para a planilha" in resposta.texto
    with deps.banco() as conexao:
        assert conexao.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 1


def test_resumos(deps):
    registrar(deps, "gasolina 250", valor=250, categoria="Transporte")
    registrar(deps, "mercado 100", valor=100, categoria="Mercado")
    registrar(deps, "lanche 50 dia 5", valor=50, categoria="Alimentação", quando="05/10")
    registrar(deps, "setembro", valor=999, categoria="Lazer", quando="20/09")

    mes = processar_texto("/gastos", deps).texto
    assert "Total: R$ 400,00 em 3 gastos" in mes
    assert "Transporte: R$ 250,00 (62%)" in mes

    semana = processar_texto("/semana", deps).texto
    assert "Total: R$ 350,00 em 2 gastos" in semana
    assert "11/10 a 17/10" in semana


def test_mensagem_que_nao_e_gasto(deps):
    deps.estado["ia"] = IAFalsa({"eh_gasto": False})
    resposta = processar_texto("marcar reunião às 9 na quinta", deps)
    assert "Agenda chega na próxima fase" in resposta.texto
    assert resposta.botoes == []
