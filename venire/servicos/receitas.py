"""Receitas (ganhos): salário, comissão, extras, reembolsos...

Ficam na tabela `incomes`, separada de `expenses`, para não mudar o formato que o
clari-analytics já lê.
"""

from datetime import date
from decimal import Decimal
from typing import Literal
import uuid

from pydantic import BaseModel, Field, field_validator

from venire.datas import data_do_gasto, formatar

CATEGORIAS_RECEITA = ("Salário", "Comissão", "Extra", "Reembolso", "Presente", "Investimentos", "Outros")


class Receita(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    valor: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    data: date
    descricao: str = ""
    pagador: str = ""
    categoria: Literal[CATEGORIAS_RECEITA] = "Outros"  # type: ignore[valid-type]
    origem: str = "texto"
    texto_original: str = ""
    confianca: float = 0.8

    @field_validator("valor", mode="before")
    @classmethod
    def arredondar(cls, valor):
        return Decimal(str(valor)).quantize(Decimal("0.01"))

    @field_validator("categoria", mode="before")
    @classmethod
    def categoria_valida(cls, valor):
        return valor if valor in CATEGORIAS_RECEITA else "Outros"


def de_dados(dados: dict, texto: str, referencia: date | None = None) -> Receita | None:
    """Valida os campos extraídos pela IA. Receitas, como gastos, são no passado ou hoje."""
    try:
        valor = float(str(dados.get("valor", 0)).replace(",", "."))
    except ValueError:
        return None
    if valor <= 0:
        return None
    return Receita(
        valor=valor,
        data=data_do_gasto(str(dados.get("quando") or ""), referencia),
        descricao=str(dados.get("descricao") or "")[:200],
        pagador=str(dados.get("pagador") or "")[:160],
        categoria=dados.get("categoria_receita") or dados.get("categoria"),
        texto_original=texto[:1000],
        confianca=float(dados.get("confianca") or 0.8),
    )


def confirmacao(receita: Receita) -> str:
    from venire.servicos.gastos import formatar_reais

    linhas = [
        f"💵 Receita registrada: {formatar_reais(receita.valor)}",
        f"📂 {receita.categoria}" + (f" · {receita.descricao}" if receita.descricao else ""),
        f"📅 {formatar(receita.data)}",
    ]
    if receita.pagador:
        linhas.append(f"👤 {receita.pagador}")
    return "\n".join(linhas)


SQL_TABELA = """
CREATE TABLE IF NOT EXISTS incomes (
    id UUID PRIMARY KEY,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    received_on DATE NOT NULL,
    payer VARCHAR(160) NOT NULL DEFAULT '',
    description VARCHAR(240) NOT NULL DEFAULT '',
    category VARCHAR(40) NOT NULL DEFAULT 'Outros',
    source VARCHAR(16) NOT NULL DEFAULT 'texto',
    source_text TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS incomes_received_on_idx ON incomes (received_on);
"""


def criar_tabela(conexao) -> None:
    conexao.execute(SQL_TABELA)


def salvar(conexao, receita: Receita) -> None:
    conexao.execute(
        """INSERT INTO incomes (id, amount, received_on, payer, description, category,
                                source, source_text, confidence)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (receita.id, receita.valor, receita.data, receita.pagador, receita.descricao,
         receita.categoria, receita.origem, receita.texto_original, receita.confianca),
    )


def apagar(conexao, receita_id: str) -> bool:
    return conexao.execute("DELETE FROM incomes WHERE id = %s RETURNING id", (receita_id,)).fetchone() is not None


def total(conexao, inicio: date, fim: date) -> Decimal:
    (soma,) = conexao.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM incomes WHERE received_on BETWEEN %s AND %s", (inicio, fim)
    ).fetchone()
    return Decimal(soma)
