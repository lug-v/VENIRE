"""Controle de envios automáticos: garante que cada aviso saia uma única vez.

Antes de enviar, o Venire "reserva" uma chave (ex.: "manha:2026-10-19"). Se ela já
existir, o envio é pulado. Se o envio falhar, a reserva é desfeita para tentar de novo.
"""

SQL_TABELA = """
CREATE TABLE IF NOT EXISTS envios (
    chave VARCHAR(200) PRIMARY KEY,
    enviado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def criar_tabela(conexao) -> None:
    conexao.execute(SQL_TABELA)


def reservar(conexao, chave: str) -> bool:
    conexao.execute("DELETE FROM envios WHERE enviado_em < NOW() - INTERVAL '30 days'")
    linha = conexao.execute(
        "INSERT INTO envios (chave) VALUES (%s) ON CONFLICT (chave) DO NOTHING RETURNING chave", (chave,)
    ).fetchone()
    return linha is not None


def liberar(conexao, chave: str) -> None:
    conexao.execute("DELETE FROM envios WHERE chave = %s", (chave,))
