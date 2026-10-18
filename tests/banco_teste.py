"""Recria todas as tabelas do Venire no banco de testes (mesma lista do banco.py)."""

import psycopg

from venire import envios, pendencias
from venire.servicos import contas, gastos, receitas, tarefas

TABELAS = ["contas_pagas", "contas", "tarefas", "incomes", "expenses", "pendencias", "envios"]


def recriar_tabelas(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conexao:
        for tabela in TABELAS:
            conexao.execute(f"DROP TABLE IF EXISTS {tabela}")
        for modulo in (gastos, pendencias, envios, receitas, contas, tarefas):
            modulo.criar_tabela(conexao)
