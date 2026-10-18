"""Testes da fase 8: compromissos recorrentes, contas fixas e tarefas.

Referência: segunda-feira, 19/10/2026, 10h da manhã.
"""

from contextlib import contextmanager
from datetime import date, time
from decimal import Decimal
import os

import psycopg
import pytest

from tests.banco_teste import recriar_tabelas

from tests.test_calendario import GoogleFalso, criar
from venire import repeticao
from venire.canais.telegram import teclado
from venire.nucleo import automacoes
from venire.nucleo.respostas import Dependencias, processar_botao, processar_texto
from venire.servicos import contas, eventos, tarefas

HOJE = date(2026, 10, 19)  # segunda
DEZ_HORAS = time(10, 0)
URL_TESTE = os.getenv("TEST_DATABASE_URL", "")


# ---------------------------------------------------------------- Repetição (sem banco)

@pytest.mark.parametrize("texto, regra, descricao", [
    ("toda terça e quinta", "FREQ=WEEKLY;BYDAY=TU,TH", "toda terça e quinta"),
    ("todas as terças-feiras", "FREQ=WEEKLY;BYDAY=TU", "toda terça"),
    ("todo sábado", "FREQ=WEEKLY;BYDAY=SA", "todo sábado"),
    ("de segunda a sexta", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "de segunda a sexta"),
    ("dias úteis", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "de segunda a sexta"),
    ("todo dia 10", "FREQ=MONTHLY;BYMONTHDAY=10", "todo dia 10"),
    ("todo mês no dia 5", "FREQ=MONTHLY;BYMONTHDAY=5", "todo dia 5"),
    ("todos os dias", "FREQ=DAILY", "todos os dias"),
    ("a cada 15 dias", "FREQ=DAILY;INTERVAL=15", "a cada 15 dias"),
    ("quinzenal na quarta", "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE", "a cada 2 semanas, toda quarta"),
    ("toda semana", "FREQ=WEEKLY;BYDAY=MO", "toda segunda"),
    ("todo mês", "FREQ=MONTHLY;BYMONTHDAY=19", "todo dia 19"),
    ("todo ano", "FREQ=YEARLY", "todo ano em 19/10"),
])
def test_interpreta_repeticao(texto, regra, descricao):
    resultado = repeticao.interpretar(texto, HOJE)
    assert (resultado.regra, resultado.descricao) == (regra, descricao)


def test_sem_repeticao():
    assert repeticao.interpretar("", HOJE) is None
    assert repeticao.interpretar("amanhã", HOJE) is None


def test_primeira_ocorrencia():
    terca_quinta = repeticao.interpretar("toda terça e quinta", HOJE)
    assert terca_quinta.primeira_data(HOJE) == date(2026, 10, 20)
    segunda = repeticao.interpretar("toda segunda", HOJE)
    assert segunda.primeira_data(HOJE, time(19), DEZ_HORAS) == HOJE                   # ainda dá hoje
    assert segunda.primeira_data(HOJE, time(9), DEZ_HORAS) == date(2026, 10, 26)      # 9h já passou
    assert repeticao.interpretar("todo dia 5", HOJE).primeira_data(HOJE) == date(2026, 11, 5)
    assert repeticao.interpretar("todo dia 31", HOJE).primeira_data(date(2026, 11, 1)) == date(2026, 12, 31)


def test_evento_recorrente():
    dados = {"titulo": "muay thai", "hora_texto": "19h", "repeticao_texto": "toda terça e quinta"}
    evento = eventos.de_dados(dados, "toda terça e quinta muay thai às 19h", HOJE, DEZ_HORAS).evento
    assert evento.repeticao == "FREQ=WEEKLY;BYDAY=TU,TH"
    assert evento.data == date(2026, 10, 20)
    assert eventos.confirmacao(evento) == (
        "🔁 Agendado: Muay thai\n🗓️ Toda terça e quinta · 19:00–20:00\n▶️ Começa terça, 20/10")


def test_evento_recorrente_hoje_nao_pergunta_o_dia():
    dados = {"titulo": "inglês", "hora_texto": "20h", "data_texto": "segunda", "repeticao_texto": "toda segunda"}
    resultado = eventos.de_dados(dados, "", HOJE, DEZ_HORAS)
    assert resultado.opcoes_de_data == [] and resultado.evento.data == HOJE


def test_google_recebe_a_regra():
    google = GoogleFalso()
    evento = eventos.Evento(titulo="Muay Thai", data=date(2026, 10, 20), inicio=time(19), fim=time(20),
                            repeticao="FREQ=WEEKLY;BYDAY=TU,TH")
    criar(google).criar(evento)
    assert google.criados[evento.id]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=TU,TH"]


def test_teclado_um_por_linha_quando_ha_muitos_botoes():
    assert len(teclado([("a", "1"), ("b", "2")])["inline_keyboard"]) == 1
    assert len(teclado([("a", "1"), ("b", "2"), ("c", "3")])["inline_keyboard"]) == 3


def test_conta_de_dados():
    conta = contas.de_dados({"nome": "aluguel", "valor": 1450, "dia_vencimento": 5, "categoria": "Moradia"})
    assert (conta.nome, conta.valor, conta.dia) == ("Aluguel", Decimal("1450.00"), 5)
    assert contas.de_dados({"nome": "luz", "valor": None, "dia_vencimento": "10"}).valor_texto == "valor variável"
    assert contas.de_dados({"nome": "luz", "dia_vencimento": 0}) is None
    assert contas.Conta(nome="X", dia=31).vencimento(date(2026, 11, 3)) == date(2026, 11, 30)


# ---------------------------------------------------------------- Com banco

class IAFalsa:
    def __init__(self, resposta):
        self.resposta = resposta

    def json(self, instrucoes, mensagem):
        return self.resposta


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

    estado = {"ia": IAFalsa({})}
    dependencias = Dependencias(ia=lambda: estado["ia"], banco=banco, hoje=lambda: HOJE,
                                hora_atual=lambda: DEZ_HORAS)
    dependencias.estado = estado
    return dependencias


def dizer(deps, mensagem, **dados):
    deps.estado["ia"] = IAFalsa(dados)
    return processar_texto(mensagem, deps)


def cadastrar(deps, nome, dia, valor=None, desde=date(2026, 10, 1), categoria="Moradia"):
    conta = contas.Conta(nome=nome, dia=dia, valor=Decimal(valor) if valor else None, categoria=categoria)
    with deps.banco() as conexao:
        contas.salvar(conexao, conta, desde)
    return conta


def test_cadastra_conta_e_desfaz(deps):
    resposta = dizer(deps, "aluguel 1450 todo dia 5", tipo="conta", nome="aluguel", valor=1450,
                     dia_vencimento=5, categoria="Moradia")
    assert resposta.texto.splitlines()[:4] == [
        "📌 Conta fixa cadastrada: Aluguel", "💲 R$ 1.450,00 · vence todo dia 5",
        "📂 Moradia", "🗓️ Próximo vencimento: quinta, 05/11"]
    assert processar_botao(resposta.botoes[0][1], deps).texto == "↩️ Conta fixa “Aluguel” removida."
    assert "Nenhuma conta fixa" in processar_texto("/contas", deps).texto


def test_conta_cadastrada_depois_do_vencimento_nao_fica_atrasada(deps):
    cadastrar(deps, "Academia", 5, 120, desde=HOJE)
    with deps.banco() as conexao:
        assert contas.pendentes(conexao, HOJE) == []
    assert "(começa no mês que vem)" in processar_texto("/contas", deps).texto


def test_lista_paga_pelo_botao_e_desfaz(deps):
    aluguel = cadastrar(deps, "Aluguel", 5, 1450)
    internet = cadastrar(deps, "Internet", 21, 99.9, categoria="Assinaturas")
    luz = cadastrar(deps, "Luz", 25)
    resposta = processar_texto("/contas", deps)
    assert "⏳ Aluguel — ⚠️ venceu dia 5 · R$ 1.450,00" in resposta.texto
    assert "⏳ Internet — vence em 2 dias (quarta, 21/10) · R$ 99,90" in resposta.texto
    assert "⏳ Luz — vence em 6 dias (domingo, 25/10) · valor variável" in resposta.texto
    assert [b[1] for b in resposta.botoes] == [f"cp:{aluguel.id}", f"cp:{internet.id}", f"cp:{luz.id}"]

    pago = processar_botao(f"cp:{aluguel.id}", deps)
    assert pago.texto.startswith("💸 ") or "R$ 1.450,00" in pago.texto
    assert "📌 Conta “Aluguel” de outubro marcada como paga." in pago.texto
    assert processar_botao(f"cp:{aluguel.id}", deps).texto == "✅ Aluguel já está paga neste mês."
    with deps.banco() as conexao:
        assert conexao.execute("SELECT amount, category, source FROM expenses").fetchone() == (
            Decimal("1450.00"), "Moradia", "conta")

    assert processar_botao(pago.botoes[0][1], deps).texto == "↩️ Gasto desfeito."  # desfaz o gasto
    assert "⏳ Aluguel" in processar_texto("/contas", deps).texto                    # volta a ficar aberta

    assert "registre o gasto" in processar_botao(f"cp:{luz.id}", deps).texto         # valor variável


def test_gasto_por_texto_marca_a_conta(deps):
    cadastrar(deps, "Aluguel", 5, 1450)
    resposta = dizer(deps, "paguei o aluguel 1450", tipo="gasto", valor=1450, descricao="aluguel",
                     categoria="Moradia")
    assert "📌 Conta “Aluguel” de outubro marcada como paga." in resposta.texto
    assert "✅ Aluguel — dia 5" in processar_texto("/contas", deps).texto
    resposta = dizer(deps, "gastei 30 no mercado", tipo="gasto", valor=30, categoria="Mercado")
    assert "Conta" not in resposta.texto


def test_pendentes_na_virada_do_mes(deps):
    cadastrar(deps, "Faculdade", 1, 900, desde=HOJE, categoria="Educação")
    with deps.banco() as conexao:
        assert [(c.nome, v) for c, v in contas.pendentes(conexao, date(2026, 10, 30))] == [
            ("Faculdade", date(2026, 11, 1))]
        assert contas.bloco_resumo(conexao, date(2026, 10, 30)) == (
            "📌 Contas a pagar\n• Faculdade — vence em 2 dias (domingo, 01/11) · R$ 900,00")


def test_tarefas(deps):
    resposta = dizer(deps, "lembrar de comprar ração", tipo="tarefa", texto="comprar ração")
    assert resposta.texto == "📝 Anotado: Comprar ração\nVocê tem 1 tarefa aberta. Veja com /tarefas."
    dizer(deps, "anota ligar pro contador", tipo="tarefa", texto="ligar pro contador")
    lista = processar_texto("/tarefas", deps)
    assert lista.texto.startswith("📝 Tarefas abertas (2)")
    assert [b[0] for b in lista.botoes] == ["✅ Comprar ração", "✅ Ligar pro contador"]

    depois = processar_botao(lista.botoes[0][1], deps)
    assert depois.texto.startswith("✅ Feito: Comprar ração\n\n📝 Tarefas abertas (1)")
    assert len(depois.botoes) == 1
    assert processar_botao(resposta.botoes[0][1], deps).texto == "↩️ Tarefa removida."


def test_manha_traz_contas_e_tarefas_e_lembra_no_dia(deps):
    cadastrar(deps, "Aluguel", 19, 1450)          # vence hoje
    cadastrar(deps, "Internet", 21, 99.9)
    with deps.banco() as conexao:
        tarefas.salvar(conexao, "comprar ração", date(2026, 10, 14))
    enviadas = []
    automacoes.executar_manha(deps, lambda texto, botoes=None: enviadas.append((texto, botoes)))
    resumo = enviadas[0][0]
    assert "📝 Tarefas (1)\n• Comprar ração (há 5 dias)" in resumo
    assert "📌 Contas a pagar\n• Aluguel — vence hoje · R$ 1.450,00\n• Internet — vence em 2 dias" in resumo
    texto, botoes = enviadas[-1]
    assert texto == "📌 Hoje vence: Aluguel · R$ 1.450,00" and botoes[0][0] == "✅ Já paguei"
    total = len(enviadas)
    automacoes.executar_manha(deps, lambda texto, botoes=None: enviadas.append((texto, botoes)))
    assert len(enviadas) == total  # nada duplicado
