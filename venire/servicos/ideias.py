"""Banco de ideias e conceitos (fase 10).

Tudo o que chega pelo Telegram como anotação, conceito ou ideia fica na tabela `ideias`.
O filtro (Claude) aprova, junta ou descarta; as aprovadas viram nota no Obsidian e
ganham uma data no calendário de posts do LinkedIn.

Status: nova → aprovada → agendada → publicada (ou descartada).
"""

from dataclasses import dataclass, fields
from datetime import date, timedelta
import re

from venire.datas import descrever

# tipo → (emoji, nome, "guardada"/"guardado")
TIPOS = {
    "anotacao": ("🗒️", "Anotação", "guardada"),
    "conceito": ("📚", "Conceito", "guardado"),
    "ideia": ("💡", "Ideia", "guardada"),
}
TEMAS = ["IA e Dados", "Comercial B2B", "Carreira", "Automação", "Outros"]
STATUS = ["nova", "aprovada", "agendada", "publicada", "descartada"]
NOMES_STATUS = {
    "nova": "🆕 Esperando o filtro",
    "aprovada": "✅ Aprovadas sem data",
    "agendada": "📅 Agendadas para o LinkedIn",
    "publicada": "📣 Publicadas",
    "descartada": "🗑️ Descartadas",
}
DIAS_DE_POST = (1, 3)  # terça e quinta (segunda = 0)
LIMITE_TEXTO = 4000


def normalizar_tipo(tipo) -> str:
    t = str(tipo or "").strip().lower()
    return t.replace("ç", "c").replace("ã", "a")


def resumo(texto: str, limite: int = 60) -> str:
    texto = " ".join((texto or "").split())
    if len(texto) > limite:
        texto = texto[:limite - 1].rstrip() + "…"
    return texto[:1].upper() + texto[1:]


@dataclass
class Ideia:
    tipo: str = "ideia"
    texto_original: str = ""
    origem: str = "texto"
    criado_em: date | None = None
    status: str = "nova"
    titulo: str = ""
    tema: str = ""
    explicacao: str = ""
    por_que_importa: str = ""
    exemplo: str = ""
    angulo: str = ""
    potencial: int | None = None
    motivo: str = ""
    data_prevista: date | None = None
    caminho_nota: str = ""
    post_texto: str = ""
    publicado_em: date | None = None
    fonte: str = ""             # caminho no GitHub quando veio do vault (ex.: _fontes/MBA/Overfitting.md)
    fonte_sha: str = ""         # muda quando a nota original é editada
    id: int | None = None

    @property
    def nome(self) -> str:
        return self.titulo or resumo(self.texto_original)

    @property
    def emoji(self) -> str:
        return TIPOS.get(self.tipo, TIPOS["ideia"])[0]


COLUNAS = [f.name for f in fields(Ideia) if f.name != "id"]

SQL_TABELA = """
CREATE TABLE IF NOT EXISTS ideias (
    id SERIAL PRIMARY KEY,
    tipo VARCHAR(12) NOT NULL,
    texto_original TEXT NOT NULL,
    origem VARCHAR(10) NOT NULL DEFAULT 'texto',
    criado_em DATE NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'nova',
    titulo VARCHAR(200) NOT NULL DEFAULT '',
    tema VARCHAR(40) NOT NULL DEFAULT '',
    explicacao TEXT NOT NULL DEFAULT '',
    por_que_importa TEXT NOT NULL DEFAULT '',
    exemplo TEXT NOT NULL DEFAULT '',
    angulo TEXT NOT NULL DEFAULT '',
    potencial SMALLINT,
    motivo TEXT NOT NULL DEFAULT '',
    data_prevista DATE,
    caminho_nota VARCHAR(300) NOT NULL DEFAULT '',
    post_texto TEXT NOT NULL DEFAULT '',
    publicado_em DATE,
    fonte VARCHAR(300) NOT NULL DEFAULT '',
    fonte_sha VARCHAR(64) NOT NULL DEFAULT ''
);
"""

# Colunas que chegaram depois da primeira versão (a tabela já existe em produção).
SQL_NOVAS_COLUNAS = [
    "ALTER TABLE ideias ADD COLUMN IF NOT EXISTS fonte VARCHAR(300) NOT NULL DEFAULT ''",
    "ALTER TABLE ideias ADD COLUMN IF NOT EXISTS fonte_sha VARCHAR(64) NOT NULL DEFAULT ''",
]


# ---------------------------------------------------------------- Banco

def criar_tabela(conexao) -> None:
    conexao.execute(SQL_TABELA)
    for sql in SQL_NOVAS_COLUNAS:
        conexao.execute(sql)


def por_fonte(conexao) -> dict[str, Ideia]:
    """Ideias que vieram do vault, indexadas pelo caminho da nota original."""
    linhas = conexao.execute(f"SELECT id, {', '.join(COLUNAS)} FROM ideias WHERE fonte <> ''").fetchall()
    return {i.fonte: i for i in map(_da_linha, linhas)}


def _da_linha(linha) -> Ideia:
    valores = dict(zip(["id"] + COLUNAS, linha))
    return Ideia(**valores)


def salvar(conexao, ideia: Ideia) -> int:
    ideia.texto_original = ideia.texto_original[:LIMITE_TEXTO]
    valores = [getattr(ideia, c) for c in COLUNAS]
    marcadores = ", ".join(["%s"] * len(COLUNAS))
    ideia.id = conexao.execute(
        f"INSERT INTO ideias ({', '.join(COLUNAS)}) VALUES ({marcadores}) RETURNING id", valores).fetchone()[0]
    return ideia.id


def atualizar(conexao, ideia: Ideia) -> None:
    ideia.titulo = ideia.titulo[:200]
    trechos = ", ".join(f"{c} = %s" for c in COLUNAS)
    conexao.execute(f"UPDATE ideias SET {trechos} WHERE id = %s",
                    [getattr(ideia, c) for c in COLUNAS] + [ideia.id])


def apagar(conexao, ideia_id: int) -> bool:
    return conexao.execute("DELETE FROM ideias WHERE id = %s RETURNING id", (ideia_id,)).fetchone() is not None


def buscar(conexao, ideia_id: int) -> Ideia | None:
    linha = conexao.execute(f"SELECT id, {', '.join(COLUNAS)} FROM ideias WHERE id = %s", (ideia_id,)).fetchone()
    return _da_linha(linha) if linha else None


def listar(conexao, status: str | tuple[str, ...], limite: int = 50, recentes_primeiro: bool = False) -> list[Ideia]:
    lista_status = [status] if isinstance(status, str) else list(status)
    ordem = "id DESC" if recentes_primeiro else "id"
    linhas = conexao.execute(
        f"SELECT id, {', '.join(COLUNAS)} FROM ideias WHERE status = ANY(%s) ORDER BY {ordem} LIMIT %s",
        (lista_status, limite)).fetchall()
    return [_da_linha(l) for l in linhas]


def contagem(conexao) -> dict[str, int]:
    return dict(conexao.execute("SELECT status, COUNT(*) FROM ideias GROUP BY status").fetchall())


def datas_ocupadas(conexao) -> set[date]:
    linhas = conexao.execute(
        "SELECT data_prevista FROM ideias WHERE status IN ('agendada', 'publicada') AND data_prevista IS NOT NULL"
    ).fetchall()
    return {l[0] for l in linhas}


def do_dia(conexao, dia: date) -> list[Ideia]:
    linhas = conexao.execute(
        f"SELECT id, {', '.join(COLUNAS)} FROM ideias WHERE status = 'agendada' AND data_prevista = %s ORDER BY id",
        (dia,)).fetchall()
    return [_da_linha(l) for l in linhas]


def proximas(conexao, hoje: date, limite: int = 10) -> list[Ideia]:
    linhas = conexao.execute(
        f"SELECT id, {', '.join(COLUNAS)} FROM ideias WHERE status = 'agendada' AND data_prevista >= %s "
        "ORDER BY data_prevista, id LIMIT %s", (hoje, limite)).fetchall()
    return [_da_linha(l) for l in linhas]


# ---------------------------------------------------------------- Calendário editorial

def proxima_data_livre(ocupadas: set[date], hoje: date, a_partir: date | None = None) -> date:
    """Próxima terça ou quinta livre, sempre depois de hoje (o post é escrito na véspera)."""
    dia = max(hoje + timedelta(days=1), a_partir or hoje)
    for _ in range(800):
        if dia.weekday() in DIAS_DE_POST and dia not in ocupadas:
            return dia
        dia += timedelta(days=1)
    return dia


def datas_livres(ocupadas: set[date], hoje: date, quantas: int = 4) -> list[date]:
    livres, usadas = [], set(ocupadas)
    for _ in range(quantas):
        dia = proxima_data_livre(usadas, hoje)
        livres.append(dia)
        usadas.add(dia)
    return livres


def distribuir(aprovadas: list[Ideia], ocupadas: set[date], hoje: date) -> None:
    """Dá uma data_prevista para cada ideia aprovada.

    Quem já pediu uma data ("ideia para postar dia 20") fica com ela, se ainda estiver no futuro.
    O resto ocupa as próximas terças e quintas livres, maior potencial primeiro.
    """
    usadas = set(ocupadas)
    com_data = [i for i in aprovadas if i.data_prevista and i.data_prevista > hoje]
    for ideia in com_data:
        usadas.add(ideia.data_prevista)
    resto = sorted((i for i in aprovadas if i not in com_data), key=lambda i: (-(i.potencial or 0), i.id or 0))
    for ideia in resto:
        ideia.data_prevista = proxima_data_livre(usadas, hoje)
        usadas.add(ideia.data_prevista)
    for ideia in aprovadas:
        ideia.status = "agendada"


# ---------------------------------------------------------------- Nota do Obsidian

def _seguro(texto: str) -> str:
    """Tira os caracteres que não podem ir no nome de um arquivo do Obsidian."""
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|#^\[\]]', "", texto)).strip()[:80] or "Sem título"


def caminho_da_nota(ideia: Ideia) -> str:
    return f"{_seguro(ideia.tema or 'Outros')}/{_seguro(ideia.nome)} ({ideia.id}).md"


def nome_da_fonte(caminho: str) -> str:
    """"_fontes/MBA/API, RAG e MCP.md" → "API, RAG e MCP" (o nome que o Obsidian usa nos links)."""
    return caminho.rsplit("/", 1)[-1].removesuffix(".md")


def ler_nota_do_vault(conteudo: str) -> tuple[dict[str, str], str]:
    """Separa o frontmatter (só as linhas "chave: valor") do corpo da nota."""
    texto = conteudo.lstrip("﻿")
    if not texto.startswith("---"):
        return {}, texto.strip()
    fim = texto.find("\n---", 3)
    if fim == -1:
        return {}, texto.strip()
    campos = {}
    for linha in texto[3:fim].splitlines():
        if ":" in linha and not linha.startswith((" ", "-")):
            chave, valor = linha.split(":", 1)
            campos[chave.strip().lower()] = valor.strip().strip('"').strip("'")
    return campos, texto[fim + 4:].strip()


def _etiqueta(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", texto.lower().replace("ç", "c").replace("ã", "a").replace("é", "e")
                  .replace("á", "a").replace("ó", "o").replace("í", "i").replace("ú", "u")).strip("-")


def _aspas(texto: str) -> str:
    return '"' + str(texto).replace("\\", "\\\\").replace('"', '\\"') + '"'


def nota_markdown(ideia: Ideia) -> str:
    tags = ["venire", ideia.tipo] + ([_etiqueta(ideia.tema)] if ideia.tema else [])
    frontmatter = [
        "---",
        f"id: {ideia.id}",
        f"titulo: {_aspas(ideia.nome)}",
        f"tipo: {ideia.tipo}",
        f"tema: {_aspas(ideia.tema or 'Outros')}",
        f"status: {ideia.status}",
        f"criado: {ideia.criado_em.isoformat() if ideia.criado_em else ''}",
        f"post_previsto: {ideia.data_prevista.isoformat() if ideia.data_prevista else ''}",
        f"publicado: {ideia.publicado_em.isoformat() if ideia.publicado_em else ''}",
        f"potencial: {ideia.potencial if ideia.potencial is not None else ''}",
        f"origem: {ideia.origem}",
        f"tags: [{', '.join(tags)}]",
        "---",
    ]
    original = "\n".join(f"> {linha}" if linha else ">" for linha in ideia.texto_original.splitlines())
    corpo = [f"# {ideia.nome}", ""]
    if ideia.fonte:
        corpo += [f"Fonte: [[{nome_da_fonte(ideia.fonte)}]]", ""]
    corpo += [
        "## Explicação breve",
        ideia.explicacao or "_(sem explicação)_",
        "",
        "## Por que importa",
        ideia.por_que_importa or "_(não informado)_",
        "",
        "## Exemplo",
        ideia.exemplo or "_(não informado)_",
        "",
        "## Texto original",
        original or "> (vazio)",
        "",
        "## Post do LinkedIn",
    ]
    if ideia.angulo:
        corpo.append(f"Ângulo sugerido: {ideia.angulo}")
        corpo.append("")
    corpo.append(ideia.post_texto or "_(será escrito na véspera da data prevista)_")
    return "\n".join(frontmatter + [""] + corpo) + "\n"


# ---------------------------------------------------------------- Textos

def confirmacao_captura(ideia: Ideia, esperando: int, hoje: date) -> str:
    emoji, nome, verbo = TIPOS.get(ideia.tipo, TIPOS["ideia"])
    linhas = [f"{emoji} {nome} {verbo}: {resumo(ideia.texto_original, 120)}"]
    if ideia.data_prevista:
        linhas.append(f"📣 Post previsto para {descrever(ideia.data_prevista)} (se passar no filtro).")
    plural = "item esperando" if esperando == 1 else "itens esperando"
    linhas.append(f"🧹 {esperando} {plural} o filtro de domingo (ou use /filtrar).")
    return "\n".join(linhas)


def bloco_resumo(conexao, dia: date) -> str:
    """Linha do resumo das 6h30 quando há post marcado para hoje."""
    posts = do_dia(conexao, dia)
    if not posts:
        return ""
    return "\n".join(f"📣 Post de hoje: {i.nome}" for i in posts)


def texto_ideias(conexao) -> str:
    totais = contagem(conexao)
    if not totais:
        return ("💡 O banco de ideias está vazio.\n"
                "Mande “ideia: post sobre IA no comercial”, “conceito: overfitting” ou “nota: hoje aprendi…”.")
    linhas = [f"💡 Banco de ideias ({sum(totais.values())})"]
    linhas += [f"{NOMES_STATUS[s]}: {totais[s]}" for s in STATUS if totais.get(s)]
    recentes = listar(conexao, tuple(STATUS), limite=5, recentes_primeiro=True)
    if recentes:
        linhas += ["", "Últimas capturadas:"]
        linhas += [f"{i.emoji} #{i.id} {i.nome} · {i.status}" for i in recentes]
    linhas += ["", "Comandos: /filtrar (rodar o filtro agora) e /linkedin (próximos posts)."]
    return "\n".join(linhas)


def texto_linkedin(proximos: list[Ideia]) -> str:
    if not proximos:
        return ("📣 Nenhum post agendado.\n"
                "As ideias aprovadas no filtro ganham uma data (terças e quintas). Use /filtrar.")
    linhas = ["📣 Próximos posts no LinkedIn"]
    linhas += [f"• {descrever(i.data_prevista)} — {i.nome}" + (" ✍️" if i.post_texto else "") for i in proximos]
    linhas += ["", "✍️ = rascunho já escrito. Toque num post para trocar a data."]
    return "\n".join(linhas)
