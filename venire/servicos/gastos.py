"""Gastos: entender a mensagem, salvar no banco e montar resumos.

A tabela se chama `expenses` e tem as mesmas colunas usadas pelo clari-analytics,
então o notebook de análise funciona com os dados do Venire sem mudanças.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
import uuid

from pydantic import BaseModel, Field, field_validator

from venire.datas import data_do_gasto, formatar, inicio_da_semana

CATEGORIAS = (
    "Alimentação", "Mercado", "Transporte", "Moradia", "Saúde",
    "Lazer", "Compras", "Educação", "Assinaturas", "Outros",
)
PAGAMENTOS = ("pix", "crédito", "débito", "dinheiro", "boleto", "não informado")

INSTRUCOES = f"""Você recebe mensagens curtas de um brasileiro registrando gastos pessoais.
Responda APENAS um JSON com estes campos:
- "eh_gasto": true se a mensagem registra um gasto já feito; false se for outra coisa (pergunta, compromisso, conversa).
- "valor": número em reais (ex.: 15, 249.9). Aceite "15 reais", "R$ 15", "15,50", "1.200". Nunca invente.
- "descricao": o que foi comprado, curto e em minúsculas (ex.: "energético", "gasolina").
- "estabelecimento": nome do lugar se for dito; senão "".
- "categoria": exatamente uma de: {", ".join(CATEGORIAS)}.
  Combustível, Uber, ônibus, estacionamento e pedágio = Transporte. Lanche, bebida, restaurante, café e delivery = Alimentação.
  Supermercado e feira = Mercado. Farmácia e consulta = Saúde. Streaming e academia = Assinaturas.
- "forma_pagamento": exatamente uma de: {", ".join(PAGAMENTOS)}. Use "não informado" se não for dito.
- "quando": a expressão de tempo exatamente como foi dita ("ontem", "sexta", "dia 10", "05/10") ou "" se não houver.
  NÃO calcule datas.
- "confianca": de 0 a 1."""


class Gasto(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    valor: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    data: date
    descricao: str = ""
    estabelecimento: str = ""
    categoria: Literal[CATEGORIAS] = "Outros"  # type: ignore[valid-type]
    forma_pagamento: Literal[PAGAMENTOS] = "não informado"  # type: ignore[valid-type]
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
        return valor if valor in CATEGORIAS else "Outros"

    @field_validator("forma_pagamento", mode="before")
    @classmethod
    def pagamento_valido(cls, valor):
        return valor if valor in PAGAMENTOS else "não informado"


def formatar_reais(valor) -> str:
    texto = f"{Decimal(valor):,.2f}"
    return "R$ " + texto.replace(",", "_").replace(".", ",").replace("_", ".")


def de_dados(dados: dict, texto: str, referencia: date | None = None) -> Gasto | None:
    """Valida os campos extraídos pela IA e calcula a data do gasto."""
    try:
        valor = float(str(dados.get("valor", 0)).replace(",", "."))
    except ValueError:
        return None
    if valor <= 0:
        return None
    return Gasto(
        valor=valor,
        data=data_do_gasto(str(dados.get("quando") or ""), referencia),
        descricao=str(dados.get("descricao") or "")[:200],
        estabelecimento=str(dados.get("estabelecimento") or "")[:160],
        categoria=dados.get("categoria"),
        forma_pagamento=dados.get("forma_pagamento"),
        texto_original=texto[:1000],
        confianca=float(dados.get("confianca") or 0.8),
    )


def interpretar(texto: str, ia, referencia: date | None = None) -> Gasto | None:
    """Atalho usado nos testes: pergunta só sobre gasto."""
    dados = ia.json(INSTRUCOES, texto)
    if not (dados.get("eh_gasto") or dados.get("tipo") == "gasto"):
        return None
    return de_dados(dados, texto, referencia)


def confirmacao(gasto: Gasto) -> str:
    linhas = [
        f"✅ Gasto registrado: {formatar_reais(gasto.valor)}",
        f"📂 {gasto.categoria}" + (f" · {gasto.descricao}" if gasto.descricao else ""),
        f"📅 {formatar(gasto.data)}",
    ]
    if gasto.estabelecimento:
        linhas.append(f"📍 {gasto.estabelecimento}")
    if gasto.forma_pagamento != "não informado":
        linhas.append(f"💳 {gasto.forma_pagamento}")
    return "\n".join(linhas)


# ---------------------------------------------------------------- Banco

SQL_TABELA = """
CREATE TABLE IF NOT EXISTS expenses (
    id UUID PRIMARY KEY,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    spent_on DATE NOT NULL,
    merchant VARCHAR(160) NOT NULL DEFAULT '',
    description VARCHAR(240) NOT NULL DEFAULT '',
    category VARCHAR(40) NOT NULL DEFAULT 'Outros',
    payment_method VARCHAR(24) NOT NULL DEFAULT 'não informado',
    source VARCHAR(16) NOT NULL DEFAULT 'texto',
    source_text TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS expenses_spent_on_idx ON expenses (spent_on);
"""


def criar_tabela(conexao) -> None:
    conexao.execute(SQL_TABELA)


def salvar(conexao, gasto: Gasto) -> None:
    conexao.execute(
        """INSERT INTO expenses (id, amount, spent_on, merchant, description, category,
                                 payment_method, source, source_text, confidence)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (gasto.id, gasto.valor, gasto.data, gasto.estabelecimento, gasto.descricao,
         gasto.categoria, gasto.forma_pagamento, gasto.origem, gasto.texto_original, gasto.confianca),
    )


def apagar(conexao, gasto_id: str) -> bool:
    cursor = conexao.execute("DELETE FROM expenses WHERE id = %s RETURNING id", (gasto_id,))
    return cursor.fetchone() is not None


def resumo(conexao, inicio: date, fim: date, titulo: str) -> str:
    linhas = conexao.execute(
        """SELECT category, SUM(amount), COUNT(*) FROM expenses
           WHERE spent_on BETWEEN %s AND %s
           GROUP BY category ORDER BY SUM(amount) DESC""",
        (inicio, fim),
    ).fetchall()
    if not linhas:
        return f"💰 {titulo}\n\nNenhum gasto registrado ainda."
    total = sum(linha[1] for linha in linhas)
    quantidade = sum(linha[2] for linha in linhas)
    texto = [
        f"💰 {titulo}",
        f"Total: {formatar_reais(total)} em {quantidade} {'gasto' if quantidade == 1 else 'gastos'}",
        "",
    ]
    for categoria, soma, _ in linhas:
        texto.append(f"• {categoria}: {formatar_reais(soma)} ({soma / total * 100:.0f}%)")
    return "\n".join(texto)


def resumo_mes(conexao, referencia: date) -> str:
    inicio = referencia.replace(day=1)
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]
    return resumo(conexao, inicio, referencia, f"Gastos de {meses[referencia.month - 1]} até hoje")


def resumo_semana(conexao, referencia: date) -> str:
    inicio = inicio_da_semana(referencia)
    return resumo(conexao, inicio, inicio + timedelta(days=6),
                  f"Gastos da semana ({formatar(inicio)[:5]} a {formatar(inicio + timedelta(days=6))[:5]})")
