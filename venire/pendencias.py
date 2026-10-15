"""Pendências: pedidos que esperam um clique (escolher o dia, confirmar apesar de conflito).

Os botões do Telegram só carregam 64 caracteres, então o pedido completo fica
guardado no banco e o botão leva apenas o identificador.
"""

import json
import uuid

SQL_TABELA = """
CREATE TABLE IF NOT EXISTS pendencias (
    id UUID PRIMARY KEY,
    tipo VARCHAR(24) NOT NULL,
    dados JSONB NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def criar_tabela(conexao) -> None:
    conexao.execute(SQL_TABELA)


def guardar(conexao, tipo: str, dados: dict) -> str:
    identificador = str(uuid.uuid4())
    conexao.execute("DELETE FROM pendencias WHERE criado_em < NOW() - INTERVAL '1 day'")
    conexao.execute("INSERT INTO pendencias (id, tipo, dados) VALUES (%s, %s, %s)",
                    (identificador, tipo, json.dumps(dados, default=str)))
    return identificador


def retirar(conexao, identificador: str) -> tuple[str, dict] | None:
    """Lê e apaga a pendência (cada botão só vale uma vez)."""
    linha = conexao.execute("DELETE FROM pendencias WHERE id = %s RETURNING tipo, dados",
                            (identificador,)).fetchone()
    if not linha:
        return None
    tipo, dados = linha
    return tipo, dados if isinstance(dados, dict) else json.loads(dados)
