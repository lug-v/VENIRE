"""Tarefas sem horário: "lembrar de comprar ração", "anota: ligar pro contador".

Aparecem no resumo da manhã até serem marcadas como feitas em /tarefas.
"""

from datetime import date
import uuid

LIMITE_RESUMO = 5


def titulo(texto: str) -> str:
    texto = " ".join((texto or "").split())[:200].rstrip(".")
    return texto[:1].upper() + texto[1:]


SQL_TABELA = """
CREATE TABLE IF NOT EXISTS tarefas (
    id UUID PRIMARY KEY,
    texto VARCHAR(200) NOT NULL,
    criada_em DATE NOT NULL,
    feita_em DATE
);
"""


def criar_tabela(conexao) -> None:
    conexao.execute(SQL_TABELA)


def salvar(conexao, texto: str, hoje: date) -> str:
    tarefa_id = str(uuid.uuid4())
    conexao.execute("INSERT INTO tarefas (id, texto, criada_em) VALUES (%s, %s, %s)", (tarefa_id, titulo(texto), hoje))
    return tarefa_id


def apagar(conexao, tarefa_id: str) -> bool:
    return conexao.execute("DELETE FROM tarefas WHERE id = %s RETURNING id", (tarefa_id,)).fetchone() is not None


def concluir(conexao, tarefa_id: str, hoje: date) -> str | None:
    """Marca como feita e devolve o texto (None se já estava feita ou não existe)."""
    linha = conexao.execute(
        "UPDATE tarefas SET feita_em = %s WHERE id = %s AND feita_em IS NULL RETURNING texto",
        (hoje, tarefa_id)).fetchone()
    return linha[0] if linha else None


def abertas(conexao) -> list[tuple[str, str, date]]:
    linhas = conexao.execute(
        "SELECT id, texto, criada_em FROM tarefas WHERE feita_em IS NULL ORDER BY criada_em, texto").fetchall()
    return [(str(i), t, c) for i, t, c in linhas]


def _idade(criada: date, hoje: date) -> str:
    dias = (hoje - criada).days
    return f" (há {dias} dias)" if dias >= 3 else ""


def bloco_resumo(conexao, hoje: date) -> str:
    lista = abertas(conexao)
    if not lista:
        return ""
    linhas = [f"📝 Tarefas ({len(lista)})"]
    linhas += [f"• {texto}{_idade(criada, hoje)}" for _, texto, criada in lista[:LIMITE_RESUMO]]
    if len(lista) > LIMITE_RESUMO:
        linhas.append(f"• … e mais {len(lista) - LIMITE_RESUMO} em /tarefas")
    return "\n".join(linhas)


def lista(conexao, hoje: date) -> tuple[str, list[tuple[str, str]]]:
    """Texto do /tarefas e as tarefas abertas como (id, texto) para os botões."""
    itens = abertas(conexao)
    if not itens:
        return "📝 Nenhuma tarefa aberta. 🙌\nPara anotar: “lembrar de comprar ração”.", []
    texto = "\n".join([f"📝 Tarefas abertas ({len(itens)})"]
                      + [f"• {t}{_idade(c, hoje)}" for _, t, c in itens]
                      + ["", "Toque numa tarefa para marcar como feita."])
    return texto, [(i, t) for i, t, _ in itens[:20]]
