"""Contas fixas: "aluguel 1450 todo dia 5", "luz vence dia 10".

O Venire avisa no resumo da manhã quando a conta está perto de vencer, manda um
lembrete com o botão "✅ Já paguei" no dia do vencimento e, ao pagar, registra o gasto.
Mandar "paguei o aluguel 1450" também marca a conta do mês como paga.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import uuid

from venire.datas import descrever, normalizar
from venire.servicos.gastos import CATEGORIAS, formatar_reais

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
AVISO_DIAS = 3  # avisa no resumo da manhã a partir de 3 dias antes


@dataclass
class Conta:
    nome: str
    dia: int
    valor: Decimal | None = None
    categoria: str = "Moradia"
    id: str = ""
    desde: date | None = None  # dia do cadastro: vencimentos anteriores não contam como atrasados

    def __post_init__(self):
        self.id = self.id or str(uuid.uuid4())

    def vencimento(self, referencia: date) -> date:
        """Vencimento no mês da referência. Dia 31 em mês de 30 dias vira dia 30."""
        ultimo = monthrange(referencia.year, referencia.month)[1]
        return referencia.replace(day=min(self.dia, ultimo))

    def vale_no_mes(self, referencia: date) -> bool:
        return self.desde is None or self.vencimento(referencia) >= self.desde

    @property
    def valor_texto(self) -> str:
        return formatar_reais(self.valor) if self.valor else "valor variável"


def de_dados(dados: dict) -> Conta | None:
    nome = " ".join(str(dados.get("nome") or dados.get("descricao") or "").split())[:80]
    try:
        dia = int(dados.get("dia_vencimento") or 0)
    except (TypeError, ValueError):
        dia = 0
    if not nome or not 1 <= dia <= 31:
        return None
    try:
        valor = Decimal(str(dados.get("valor")).replace(",", ".")).quantize(Decimal("0.01"))
        valor = valor if valor > 0 else None
    except Exception:
        valor = None
    categoria = dados.get("categoria") if dados.get("categoria") in CATEGORIAS else "Moradia"
    return Conta(nome=nome[:1].upper() + nome[1:], dia=dia, valor=valor, categoria=categoria)


def confirmacao(conta: Conta, hoje: date) -> str:
    proximo = conta.vencimento(hoje)
    if proximo < hoje:
        proximo = conta.vencimento((hoje.replace(day=1) + timedelta(days=32)).replace(day=1))
    return "\n".join([
        f"📌 Conta fixa cadastrada: {conta.nome}",
        f"💲 {conta.valor_texto} · vence todo dia {conta.dia}",
        f"📂 {conta.categoria}",
        f"🗓️ Próximo vencimento: {descrever(proximo)}",
        f"⏰ Aviso no resumo da manhã {AVISO_DIAS} dias antes e um lembrete no dia.",
    ])


# ---------------------------------------------------------------- Banco

SQL_TABELA = """
CREATE TABLE IF NOT EXISTS contas (
    id UUID PRIMARY KEY,
    nome VARCHAR(80) NOT NULL,
    valor NUMERIC(12, 2),
    dia SMALLINT NOT NULL CHECK (dia BETWEEN 1 AND 31),
    categoria VARCHAR(40) NOT NULL DEFAULT 'Moradia',
    ativa BOOLEAN NOT NULL DEFAULT TRUE,
    desde DATE NOT NULL DEFAULT CURRENT_DATE,
    criada_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS contas_pagas (
    conta_id UUID NOT NULL REFERENCES contas(id) ON DELETE CASCADE,
    mes DATE NOT NULL,
    gasto_id UUID,
    pago_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (conta_id, mes)
);
"""


def criar_tabela(conexao) -> None:
    conexao.execute(SQL_TABELA)


def salvar(conexao, conta: Conta, hoje: date) -> None:
    conta.desde = hoje
    conexao.execute("INSERT INTO contas (id, nome, valor, dia, categoria, desde) VALUES (%s, %s, %s, %s, %s, %s)",
                    (conta.id, conta.nome, conta.valor, conta.dia, conta.categoria, hoje))


def apagar(conexao, conta_id: str) -> str | None:
    """Remove a conta. Devolve o nome, ou None se não existia."""
    linha = conexao.execute("DELETE FROM contas WHERE id = %s RETURNING nome", (conta_id,)).fetchone()
    return linha[0] if linha else None


def buscar(conexao, conta_id: str) -> Conta | None:
    linha = conexao.execute("SELECT id, nome, valor, dia, categoria, desde FROM contas WHERE id = %s AND ativa",
                            (conta_id,)).fetchone()
    return _conta(linha) if linha else None


def _conta(linha) -> Conta:
    conta_id, nome, valor, dia, categoria, desde = linha
    return Conta(nome=nome, dia=dia, valor=Decimal(valor) if valor is not None else None,
                 categoria=categoria, id=str(conta_id), desde=desde)


def todas(conexao) -> list[Conta]:
    linhas = conexao.execute("SELECT id, nome, valor, dia, categoria, desde FROM contas WHERE ativa ORDER BY dia, nome").fetchall()
    return [_conta(linha) for linha in linhas]


def pagas_no_mes(conexao, referencia: date) -> set[str]:
    linhas = conexao.execute("SELECT conta_id FROM contas_pagas WHERE mes = %s", (referencia.replace(day=1),)).fetchall()
    return {str(c) for (c,) in linhas}


def marcar_paga(conexao, conta_id: str, referencia: date, gasto_id: str | None = None) -> bool:
    """False se a conta já estava paga neste mês."""
    linha = conexao.execute(
        """INSERT INTO contas_pagas (conta_id, mes, gasto_id) VALUES (%s, %s, %s)
           ON CONFLICT DO NOTHING RETURNING conta_id""",
        (conta_id, referencia.replace(day=1), gasto_id)).fetchone()
    return linha is not None


def desmarcar_pelo_gasto(conexao, gasto_id: str) -> None:
    conexao.execute("DELETE FROM contas_pagas WHERE gasto_id = %s", (gasto_id,))


def conta_do_texto(conexao, texto: str, referencia: date) -> Conta | None:
    """Conta ainda não paga no mês cujo nome aparece no texto ("paguei o aluguel")."""
    t = normalizar(texto)
    pagas = pagas_no_mes(conexao, referencia)
    for conta in sorted(todas(conexao), key=lambda c: -len(c.nome)):
        if conta.id not in pagas and conta.vale_no_mes(referencia) and normalizar(conta.nome) in t:
            return conta
    return None


# ---------------------------------------------------------------- Textos

def pendentes(conexao, hoje: date, dias: int = AVISO_DIAS) -> list[tuple[Conta, date]]:
    """Contas não pagas no mês que vencem até `dias` dias à frente (inclui atrasadas)."""
    pagas = pagas_no_mes(conexao, hoje)
    limite = hoje + timedelta(days=dias)
    lista = [(c, c.vencimento(hoje)) for c in todas(conexao) if c.id not in pagas and c.vale_no_mes(hoje)]
    # perto da virada do mês, olha também as do mês seguinte
    proximo_mes = (hoje.replace(day=1) + timedelta(days=32)).replace(day=1)
    if limite >= proximo_mes:
        pagas_prox = pagas_no_mes(conexao, proximo_mes)
        lista += [(c, c.vencimento(proximo_mes)) for c in todas(conexao) if c.id not in pagas_prox]
    return sorted([(c, v) for c, v in lista if v <= limite], key=lambda item: item[1])


def _prazo(vencimento: date, hoje: date) -> str:
    dias = (vencimento - hoje).days
    if dias < 0:
        return f"⚠️ venceu dia {vencimento.day}"
    if dias == 0:
        return "vence hoje"
    if dias == 1:
        return "vence amanhã"
    return f"vence em {dias} dias ({descrever(vencimento)})"


def bloco_resumo(conexao, hoje: date) -> str:
    itens = pendentes(conexao, hoje)
    if not itens:
        return ""
    linhas = ["📌 Contas a pagar"]
    linhas += [f"• {c.nome} — {_prazo(v, hoje)} · {c.valor_texto}" for c, v in itens]
    return "\n".join(linhas)


def lista_do_mes(conexao, hoje: date) -> tuple[str, list[Conta]]:
    """Texto do /contas e as contas ainda não pagas (para os botões)."""
    contas = todas(conexao)
    if not contas:
        return ("📌 Nenhuma conta fixa cadastrada.\n"
                "Exemplo: “aluguel 1450 todo dia 5” ou “conta de luz vence dia 10”."), []
    pagas = pagas_no_mes(conexao, hoje)
    linhas = [f"📌 Contas fixas de {MESES[hoje.month - 1]}"]
    total = pago = Decimal(0)
    abertas = []
    for conta in contas:
        if not conta.vale_no_mes(hoje):
            linhas.append(f"🗓️ {conta.nome} — dia {conta.dia} · {conta.valor_texto} (começa no mês que vem)")
            continue
        if conta.id in pagas:
            linhas.append(f"✅ {conta.nome} — dia {conta.dia} · {conta.valor_texto}")
            pago += conta.valor or 0
        else:
            linhas.append(f"⏳ {conta.nome} — {_prazo(conta.vencimento(hoje), hoje)} · {conta.valor_texto}")
            abertas.append(conta)
        total += conta.valor or 0
    if total:
        linhas += ["", f"Total fixo: {formatar_reais(total)} · já pago: {formatar_reais(pago)}"]
    if abertas:
        linhas += ["", "Toque para marcar como paga (o gasto é registrado junto)."]
    return "\n".join(linhas), abertas
