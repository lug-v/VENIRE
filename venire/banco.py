"""Conexão com o banco Neon (PostgreSQL)."""

from contextlib import contextmanager

import psycopg

from venire import pendencias
from venire.servicos import gastos

_tabelas_prontas = False


@contextmanager
def conectar(database_url: str):
    """Abre uma conexão, garante as tabelas e confirma (commit) tudo no final."""
    global _tabelas_prontas
    if not database_url:
        raise RuntimeError("DATABASE_URL não configurada.")
    with psycopg.connect(database_url, autocommit=False, connect_timeout=10) as conexao:
        if not _tabelas_prontas:
            gastos.criar_tabela(conexao)
            pendencias.criar_tabela(conexao)
            _tabelas_prontas = True
        yield conexao
        conexao.commit()
