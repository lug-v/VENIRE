"""Fase 10: filtro do banco de ideias, notas do Obsidian e posts do LinkedIn.

O Claude faz o trabalho de editor (filtrar, explicar, escrever o post). Sem a
ANTHROPIC_API_KEY, o Venire usa a mesma IA do resto (Groq), de graça.
As funções devolvem (texto, botões); quem chama transforma em Resposta.
"""

from dataclasses import dataclass, field
from datetime import timedelta
import json
import logging
from typing import Callable

from venire import envios
from venire.datas import descrever
from venire.servicos import ideias
from venire.servicos.ideias import Ideia

log = logging.getLogger("venire")

LIMITE_FILTRO = 15        # ideias por rodada do filtro (o resto fica para a próxima)
LIMITE_CONTEXTO = 40      # ideias já aprovadas enviadas ao filtro, para achar repetidas

Botoes = list[tuple[str, str]]

INSTRUCOES_FILTRO = f"""Você é o editor do banco de ideias de um profissional brasileiro que trabalha com vendas B2B
e faz MBA em IA e Dados. Ele captura anotações, conceitos e ideias pelo Telegram, e você decide o que vira
nota no Obsidian e, depois, post no LinkedIn.

Você recebe um JSON com:
- "novas": itens para decidir (id, tipo, texto).
- "ja_aprovadas": itens aprovados antes (id, titulo), só para achar repetições.
- "manter": ids que o dono pediu para manter; esses DEVEM ser aprovados.

Responda APENAS um JSON: {{"ideias": [ ... ]}}, com um item para CADA id de "novas":
- "id": o mesmo id.
- "decisao": "aprovada", "juntar" ou "descartada".
  "juntar": é o mesmo assunto de outro item (de "novas" ou "ja_aprovadas"); informe "juntar_com" = id do outro.
  "descartada": vago demais, sem conteúdo, lembrete pessoal ou algo que não renderia uma nota útil.
- "motivo": uma frase curta (obrigatório em "juntar" e "descartada").
Só para "aprovada":
- "titulo": nome curto da nota, até 60 caracteres, sem emoji.
- "tema": exatamente um de: {", ".join(ideias.TEMAS)}.
- "explicacao": explicação breve do conceito em 3 a 5 linhas, linguagem simples. Se o texto já explica,
  organize e complete; se é só um termo ("conceito: overfitting"), explique o que ele significa.
- "por_que_importa": 1 ou 2 frases.
- "exemplo": um exemplo prático, de preferência do mundo comercial ou de dados.
- "potencial": inteiro de 0 a 10, o quanto renderia um bom post no LinkedIn.
- "angulo": o ângulo sugerido para o post, numa frase.
Escreva em português do Brasil. Não invente fatos, números ou fontes."""

INSTRUCOES_POST = """Você escreve posts para o LinkedIn de um profissional brasileiro de vendas B2B que faz MBA em IA e Dados.
O post nasce de uma nota de estudo dele. Tom: próximo, direto, sem jargão de guru, sem exagero.

Estrutura:
1. Gancho na primeira linha (uma frase curta que faça a pessoa parar de rolar a tela).
2. Explicação breve do conceito, em linguagem simples.
3. Um exemplo prático.
4. Uma pergunta no fim, para puxar comentários.
5. De 3 a 5 hashtags na última linha.

Regras: até 1.300 caracteres; parágrafos curtos separados por linha em branco; sem markdown (nada de ** ou #
fora das hashtags); no máximo 2 emojis; não invente números, estudos ou histórias pessoais.
Responda APENAS um JSON: {"post": "texto do post"}."""


# ---------------------------------------------------------------- IA e notas

def _ia_conteudo(deps):
    """Claude, se configurado; senão a IA padrão do Venire (Groq)."""
    return deps.claude() if getattr(deps, "claude", None) else deps.ia()


def gravar_nota(deps, ideia: Ideia) -> str | None:
    """Cria ou atualiza a nota no repositório do Obsidian. Devolve o erro, se houver."""
    if not getattr(deps, "notas", None):
        return None
    caminho = ideia.caminho_nota or ideias.caminho_da_nota(ideia)
    try:
        deps.notas().gravar(caminho, ideias.nota_markdown(ideia), f"Venire: {ideia.nome} ({ideia.status})")
    except Exception as erro:
        log.exception("Falha ao gravar a nota no GitHub")
        return str(erro)
    ideia.caminho_nota = caminho
    return None


# ---------------------------------------------------------------- Filtro

@dataclass
class ResultadoFiltro:
    aprovadas: list[Ideia] = field(default_factory=list)
    juntadas: list[tuple[Ideia, int]] = field(default_factory=list)
    descartadas: list[Ideia] = field(default_factory=list)
    sem_resposta: int = 0
    notas_gravadas: int = 0
    erro_notas: str = ""


def _inteiro(valor) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _aplicar_aprovacao(ideia: Ideia, decisao: dict) -> None:
    tema = str(decisao.get("tema") or "")
    ideia.status = "aprovada"
    ideia.titulo = ideias.resumo(str(decisao.get("titulo") or ideia.texto_original), 60).rstrip("…")
    ideia.tema = tema if tema in ideias.TEMAS else "Outros"
    ideia.explicacao = str(decisao.get("explicacao") or "").strip()
    ideia.por_que_importa = str(decisao.get("por_que_importa") or "").strip()
    ideia.exemplo = str(decisao.get("exemplo") or "").strip()
    ideia.angulo = str(decisao.get("angulo") or "").strip()
    potencial = _inteiro(decisao.get("potencial"))
    ideia.potencial = max(0, min(10, potencial)) if potencial is not None else None
    ideia.motivo = ""


def decidir(novas: list[Ideia], decisoes: list[dict], ids_existentes: set[int],
            manter: set[int] = frozenset()) -> ResultadoFiltro:
    """Aplica a resposta do filtro nas ideias (sem banco). Itens sem resposta continuam "nova"."""
    resultado = ResultadoFiltro()
    por_id = {_inteiro(d.get("id")): d for d in decisoes if isinstance(d, dict)}
    ids_validos = ids_existentes | {i.id for i in novas}

    for ideia in novas:
        decisao = por_id.get(ideia.id)
        if decisao is None:
            resultado.sem_resposta += 1
            continue
        tipo = str(decisao.get("decisao") or "").lower()
        alvo = _inteiro(decisao.get("juntar_com"))
        if ideia.id in manter or tipo == "aprovada":
            _aplicar_aprovacao(ideia, decisao)
            resultado.aprovadas.append(ideia)
        elif tipo == "juntar" and alvo in ids_validos and alvo != ideia.id:
            ideia.status, ideia.motivo = "descartada", f"juntada à #{alvo}"
            resultado.juntadas.append((ideia, alvo))
        elif tipo in ("descartada", "juntar"):
            ideia.status = "descartada"
            ideia.motivo = str(decisao.get("motivo") or "sem motivo informado").strip()
            resultado.descartadas.append(ideia)
        else:
            resultado.sem_resposta += 1
    return resultado


def filtrar(deps, ids: list[int] | None = None, manter: bool = False) -> ResultadoFiltro | None:
    """Roda o filtro nas ideias novas (ou só nos ids pedidos). None = nada para filtrar."""
    hoje = deps.hoje()
    with deps.banco() as conexao:
        if ids:
            novas = [i for i in (ideias.buscar(conexao, x) for x in ids) if i]
        else:
            novas = ideias.listar(conexao, "nova", limite=LIMITE_FILTRO)
        anteriores = ideias.listar(conexao, ("aprovada", "agendada", "publicada"),
                                   limite=LIMITE_CONTEXTO, recentes_primeiro=True)
    if not novas:
        return None

    entrada = {
        "novas": [{"id": i.id, "tipo": i.tipo, "texto": i.texto_original} for i in novas],
        "ja_aprovadas": [{"id": i.id, "titulo": i.nome} for i in anteriores],
        "manter": [i.id for i in novas] if manter else [],
    }
    resposta = _ia_conteudo(deps).json(INSTRUCOES_FILTRO, json.dumps(entrada, ensure_ascii=False))
    resultado = decidir(novas, resposta.get("ideias") or [], {i.id for i in anteriores},
                        {i.id for i in novas} if manter else set())

    antigas_com_nota: list[Ideia] = []
    with deps.banco() as conexao:
        # Juntar: o texto vai para a outra ideia, que fica mais completa.
        for ideia, alvo_id in resultado.juntadas:
            alvo = next((i for i in novas if i.id == alvo_id), None) or ideias.buscar(conexao, alvo_id)
            if alvo:
                alvo.texto_original = f"{alvo.texto_original}\n\n+ {ideia.texto_original}"[:ideias.LIMITE_TEXTO]
                if all(alvo.id != i.id for i in novas):
                    ideias.atualizar(conexao, alvo)
                    if alvo.caminho_nota:
                        antigas_com_nota.append(alvo)
        ideias.distribuir(resultado.aprovadas, ideias.datas_ocupadas(conexao), hoje)
        for ideia in novas:
            ideias.atualizar(conexao, ideia)

    # A nota é gravada depois do banco, para uma falha no GitHub não perder o filtro.
    for ideia in resultado.aprovadas:
        erro = gravar_nota(deps, ideia)
        if erro:
            resultado.erro_notas = erro
        elif ideia.caminho_nota:
            resultado.notas_gravadas += 1
    if resultado.notas_gravadas:
        with deps.banco() as conexao:
            for ideia in resultado.aprovadas:
                ideias.atualizar(conexao, ideia)
    for alvo in antigas_com_nota:  # a nota antiga ganha o texto juntado
        resultado.erro_notas = gravar_nota(deps, alvo) or resultado.erro_notas
    return resultado


def texto_filtro(resultado: ResultadoFiltro | None, deps) -> tuple[str, Botoes]:
    if resultado is None:
        return "🧹 Nada novo para filtrar. Mande uma ideia, um conceito ou uma anotação.", []
    linhas = ["🧹 Filtro do banco de ideias"]
    if resultado.aprovadas:
        linhas += ["", f"✅ Aprovadas ({len(resultado.aprovadas)}):"]
        for i in sorted(resultado.aprovadas, key=lambda i: i.data_prevista or deps.hoje()):
            estrela = f" · ⭐ {i.potencial}" if i.potencial is not None else ""
            data = f" → 📣 {descrever(i.data_prevista)}" if i.data_prevista else ""
            linhas.append(f"• {i.emoji} {i.nome} · {i.tema}{estrela}{data}")
    if resultado.juntadas:
        linhas += ["", f"🔗 Juntadas ({len(resultado.juntadas)}):"]
        linhas += [f"• “{ideias.resumo(i.texto_original, 50)}” → #{alvo}" for i, alvo in resultado.juntadas]
    if resultado.descartadas:
        linhas += ["", f"🗑️ Descartadas ({len(resultado.descartadas)}):"]
        linhas += [f"• “{ideias.resumo(i.texto_original, 50)}” — {i.motivo}" for i in resultado.descartadas]
    if resultado.sem_resposta:
        linhas += ["", f"⏳ {resultado.sem_resposta} ficaram para o próximo filtro."]
    if resultado.notas_gravadas:
        plural = "nota gravada" if resultado.notas_gravadas == 1 else "notas gravadas"
        linhas += ["", f"🗂️ {resultado.notas_gravadas} {plural} no Obsidian."]
    if resultado.erro_notas:
        linhas += ["", f"⚠️ Não consegui gravar as notas no Obsidian: {resultado.erro_notas}"]
    elif resultado.aprovadas and not getattr(deps, "notas", None):
        linhas += ["", "🗂️ Notas do Obsidian desligadas: falta GITHUB_TOKEN e OBSIDIAN_REPO na Vercel."]
    botoes = [(f"♻️ Manter: {ideias.resumo(i.texto_original, 28)}", f"ir:{i.id}")
              for i in resultado.descartadas[:5]]
    return "\n".join(linhas), botoes


def recuperar(deps, ideia_id: int) -> tuple[str, Botoes]:
    """Botão "♻️ Manter": a ideia descartada volta e é aprovada pelo filtro."""
    with deps.banco() as conexao:
        ideia = ideias.buscar(conexao, ideia_id)
        if ideia is None or ideia.status != "descartada":
            return "Essa ideia não está mais entre as descartadas.", []
        ideia.status, ideia.motivo = "nova", ""
        ideias.atualizar(conexao, ideia)
    return texto_filtro(filtrar(deps, ids=[ideia_id], manter=True), deps)


def executar_filtro(deps, enviar: Callable[..., None]) -> dict:
    """Rota /cron/ideias (domingo às 20h). Não manda nada quando não há ideia nova."""
    resultado = filtrar(deps)
    if resultado is None:
        return {"enviados": 0, "filtradas": 0}
    texto, botoes = texto_filtro(resultado, deps)
    enviar(texto, botoes) if botoes else enviar(texto)
    total = len(resultado.aprovadas) + len(resultado.juntadas) + len(resultado.descartadas)
    return {"enviados": 1, "filtradas": total}


# ---------------------------------------------------------------- Posts do LinkedIn

def escrever_post(deps, ideia: Ideia, anterior: str = "") -> str:
    entrada = {
        "titulo": ideia.nome, "tema": ideia.tema, "explicacao": ideia.explicacao,
        "por_que_importa": ideia.por_que_importa, "exemplo": ideia.exemplo,
        "angulo": ideia.angulo, "texto_original": ideia.texto_original,
    }
    if anterior:
        entrada["versao_anterior"] = anterior
        entrada["pedido"] = "Escreva uma versão diferente: outro gancho e outra estrutura."
    resposta = _ia_conteudo(deps).json(INSTRUCOES_POST, json.dumps(entrada, ensure_ascii=False))
    post = str(resposta.get("post") or "").strip()
    if not post:
        raise ValueError("A IA não devolveu o texto do post.")
    return post


def preparar_post(deps, ideia: Ideia, anterior: str = "") -> None:
    """Escreve o post, salva no banco e atualiza a nota."""
    ideia.post_texto = escrever_post(deps, ideia, anterior)
    gravar_nota(deps, ideia)
    with deps.banco() as conexao:
        ideias.atualizar(conexao, ideia)


def botoes_post(ideia: Ideia) -> Botoes:
    return [("✅ Publiquei", f"lp:{ideia.id}"), ("✏️ Reescrever", f"lr:{ideia.id}"),
            ("⏭️ Adiar uma semana", f"la:{ideia.id}"), ("🗑️ Descartar", f"ld:{ideia.id}")]


def texto_rascunho(ideia: Ideia, titulo: str = "📣 Post de hoje") -> str:
    return (f"{titulo}: {ideia.nome}\n\n{ideia.post_texto}\n\n"
            "Copie, cole no LinkedIn e depois toque em ✅ Publiquei.")


def executar_linkedin(deps, enviar: Callable[..., None]) -> dict:
    """Rota /cron/linkedin (todo dia às 8h).

    1. Entrega o rascunho dos posts de hoje (escreve na hora se a véspera falhou).
    2. Escreve com antecedência os posts de amanhã (a "véspera").
    """
    hoje = deps.hoje()
    enviados = escritos = 0
    with deps.banco() as conexao:
        de_hoje = ideias.do_dia(conexao, hoje)
        de_amanha = ideias.do_dia(conexao, hoje + timedelta(days=1))

    for ideia in de_hoje:
        if not ideia.post_texto:
            preparar_post(deps, ideia)
            escritos += 1
        enviados += _enviar_uma_vez(deps, f"linkedin:{ideia.id}:{hoje}", texto_rascunho(ideia),
                                    botoes_post(ideia), enviar)

    for ideia in de_amanha:
        if ideia.post_texto:
            continue
        try:
            preparar_post(deps, ideia)
            escritos += 1
        except Exception:
            log.exception("Falha ao escrever o post de amanhã")  # amanhã tenta de novo
    return {"enviados": enviados, "escritos": escritos}


def _enviar_uma_vez(deps, chave: str, texto: str, botoes: Botoes, enviar) -> int:
    with deps.banco() as conexao:
        if not envios.reservar(conexao, chave):
            return 0
    try:
        enviar(texto, botoes)
    except Exception:
        with deps.banco() as conexao:
            envios.liberar(conexao, chave)
        raise
    return 1


# ---------------------------------------------------------------- Botões do rascunho

def _carregar(deps, ideia_id: int) -> Ideia | None:
    with deps.banco() as conexao:
        return ideias.buscar(conexao, ideia_id)


def _salvar(deps, ideia: Ideia, nota: bool = True) -> None:
    if nota:
        gravar_nota(deps, ideia)
    with deps.banco() as conexao:
        ideias.atualizar(conexao, ideia)


def publicar(deps, ideia_id: int) -> tuple[str, Botoes]:
    ideia = _carregar(deps, ideia_id)
    if ideia is None:
        return "Essa ideia não existe mais.", []
    if ideia.status == "publicada":
        return f"✅ “{ideia.nome}” já estava marcado como publicado.", []
    ideia.status, ideia.publicado_em = "publicada", deps.hoje()
    _salvar(deps, ideia)
    return f"✅ Publicado: {ideia.nome}\nBoa! A nota no Obsidian foi marcada como publicada.", []


def reescrever(deps, ideia_id: int) -> tuple[str, Botoes]:
    ideia = _carregar(deps, ideia_id)
    if ideia is None or ideia.status != "agendada":
        return "Esse post não está mais agendado.", []
    try:
        preparar_post(deps, ideia, anterior=ideia.post_texto)
    except Exception as erro:
        log.exception("Falha ao reescrever o post")
        return f"😕 Não consegui reescrever agora.\nMotivo: {erro}", botoes_post(ideia)
    return texto_rascunho(ideia, "✏️ Nova versão"), botoes_post(ideia)


def mudar_data(deps, ideia_id: int, nova_data=None, adiar_dias: int = 0) -> tuple[str, Botoes]:
    """Adiar uma semana (adiar_dias=7) ou trocar para uma data escolhida."""
    hoje = deps.hoje()
    with deps.banco() as conexao:
        ideia = ideias.buscar(conexao, ideia_id)
        if ideia is None or ideia.status != "agendada":
            return "Esse post não está mais agendado.", []
        ocupadas = ideias.datas_ocupadas(conexao) - {ideia.data_prevista}
    if nova_data is None:
        base = (ideia.data_prevista or hoje) + timedelta(days=adiar_dias)
        nova_data = ideias.proxima_data_livre(ocupadas, hoje, a_partir=base)
    ideia.data_prevista = nova_data
    _salvar(deps, ideia)
    return f"📅 “{ideia.nome}” agora sai {descrever(nova_data)}.", []


def descartar(deps, ideia_id: int) -> tuple[str, Botoes]:
    ideia = _carregar(deps, ideia_id)
    if ideia is None:
        return "Essa ideia não existe mais.", []
    ideia.status, ideia.motivo, ideia.data_prevista = "descartada", "descartada no rascunho do LinkedIn", None
    _salvar(deps, ideia)
    return f"🗑️ Post descartado: {ideia.nome}\nA nota continua no Obsidian.", []


def opcoes_de_data(deps, ideia_id: int) -> tuple[str, Botoes]:
    """Botão do /linkedin: mostra as próximas terças e quintas livres."""
    with deps.banco() as conexao:
        ideia = ideias.buscar(conexao, ideia_id)
        if ideia is None or ideia.status != "agendada":
            return "Esse post não está mais agendado.", []
        ocupadas = ideias.datas_ocupadas(conexao) - {ideia.data_prevista}
    livres = ideias.datas_livres(ocupadas, deps.hoje())
    botoes = [(descrever(d).capitalize(), f"lq:{ideia.id}:{d.isoformat()}") for d in livres]
    atual = descrever(ideia.data_prevista) if ideia.data_prevista else "sem data"
    return f"📅 “{ideia.nome}” está para {atual}. Escolha a nova data:", botoes
