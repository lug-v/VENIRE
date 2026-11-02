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

# Groq grátis: no máximo ~1.000 tokens de RESPOSTA por minuto. Cada ideia aprovada gera ~300 tokens,
# então cada chamada leva 2 ideias e uma execução faz 1 chamada. O cron /cron/ideias roda 1 vez por dia
# (2 ideias/dia = 14/semana, bem mais que os 2 posts semanais).
LIMITE_FILTRO = 2         # ideias por chamada
RODADAS_FILTRO = 1        # chamadas por execução (mais de uma estoura o limite por minuto)
MAX_TOKENS_FILTRO = 800   # teto da resposta do filtro (abaixo dos 1.000 por minuto)
MAX_TOKENS_POST = 900     # teto da resposta do post (~1.300 caracteres + JSON)
LIMITE_CONTEXTO = 40      # ideias já aprovadas enviadas ao filtro, para achar repetidas
PASTA_FONTES = "_fontes"  # cópia dos conceitos do vault, enviada pela tarefa do Windows
TEXTO_NO_FILTRO = 800     # caracteres de cada ideia enviados ao filtro (o limite do Groq grátis é por minuto)
TEXTO_NO_POST = 3000

Botoes = list[tuple[str, str]]

INSTRUCOES_FILTRO = f"""Você é o editor do banco de ideias de um profissional brasileiro que trabalha com vendas B2B
e faz MBA em IA e Dados. Ele captura anotações, conceitos e ideias pelo Telegram, e você decide o que vira
nota no Obsidian e, depois, post no LinkedIn.

Você recebe um JSON com:
- "novas": itens para decidir (id, tipo, origem, texto). Itens com "origem": "obsidian" são notas de estudo
  do próprio dono, já revisadas: aprove, a não ser que estejam vazias ou repitam outro item, e baseie a
  explicação no texto dele, sem contradizer.
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
- "explicacao": explicação breve do conceito em 3 ou 4 linhas, linguagem simples. Se o texto já explica,
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
    restantes: int = 0          # ideias que continuam esperando o próximo filtro
    importadas: int = 0         # conceitos novos que vieram do vault
    atualizadas_fonte: int = 0  # conceitos do vault que foram editados
    erro_fontes: str = ""


def _inteiro(valor) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _aplicar_aprovacao(ideia: Ideia, decisao: dict) -> None:
    tema = str(decisao.get("tema") or "")
    ideia.status = "aprovada"
    if ideia.fonte:  # nota do vault: mantém o nome original, para o link [[...]] bater
        ideia.titulo = ideias.nome_da_fonte(ideia.fonte)
    else:
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
        "novas": [{"id": i.id, "tipo": i.tipo, "origem": i.origem, "texto": i.texto_original[:TEXTO_NO_FILTRO]}
                  for i in novas],
        "ja_aprovadas": [{"id": i.id, "titulo": i.nome} for i in anteriores],
        "manter": [i.id for i in novas] if manter else [],
    }
    resposta = _ia_conteudo(deps).json(INSTRUCOES_FILTRO, json.dumps(entrada, ensure_ascii=False),
                                       max_tokens=MAX_TOKENS_FILTRO)
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


LIMITE_IMPORTACAO = 20             # notas do vault lidas por execução (o resto entra na próxima)
IGNORADA_NO_VAULT = "ignorada no vault (venire: false ou vazia)"
TRAVA_FILTRO = "trava:filtro"


class FiltroOcupado(Exception):
    """Outro filtro já está rodando (ex.: o Telegram reenviou o /filtrar)."""


def _ignorar_no_vault(campos: dict, corpo: str) -> bool:
    return campos.get("venire", "").lower() in ("false", "nao", "não", "no") or not corpo


def importar_fontes(deps) -> tuple[int, int, str]:
    """Traz para o banco de ideias os conceitos que a tarefa do Windows copiou do vault.

    Nota nova → ideia "nova" (tipo conceito, origem obsidian). Nota editada → o texto é
    atualizado, sem mudar o status. Notas com "venire: false" (ou vazias) ficam registradas
    como descartadas, com o sha, para não serem baixadas de novo a cada filtro; se a marca
    sair depois, a nota volta como "nova". Usa uma conexão só com o banco.
    Devolve (novas, atualizadas, erro).
    """
    if not getattr(deps, "notas", None):
        return 0, 0, ""
    novas = atualizadas = 0
    try:
        repo = deps.notas()
        arquivos = repo.listar(PASTA_FONTES)
        with deps.banco() as conexao:
            existentes = ideias.por_fonte(conexao)
            mudadas = [(c, s) for c, s in sorted(arquivos.items())
                       if not (c in existentes and existentes[c].fonte_sha == s)]
            for caminho, sha in mudadas[:LIMITE_IMPORTACAO]:
                campos, corpo = ideias.ler_nota_do_vault(repo.ler(caminho))
                ignorar = _ignorar_no_vault(campos, corpo)
                atual = existentes.get(caminho)
                if atual is None:
                    ideia = Ideia(tipo="conceito", texto_original=corpo, origem="obsidian", criado_em=deps.hoje(),
                                  titulo=ideias.nome_da_fonte(caminho), fonte=caminho, fonte_sha=sha)
                    if ignorar:
                        ideia.status, ideia.motivo = "descartada", IGNORADA_NO_VAULT
                    else:
                        novas += 1
                    ideias.salvar(conexao, ideia)
                    continue
                atual.fonte_sha = sha
                if ignorar:
                    if atual.status == "nova":
                        atual.status, atual.motivo = "descartada", IGNORADA_NO_VAULT
                elif atual.status == "descartada" and atual.motivo == IGNORADA_NO_VAULT:
                    atual.texto_original, atual.status, atual.motivo = corpo[:ideias.LIMITE_TEXTO], "nova", ""
                    novas += 1
                else:
                    atual.texto_original = corpo[:ideias.LIMITE_TEXTO]
                    if atual.caminho_nota:
                        gravar_nota(deps, atual)
                    atualizadas += 1
                ideias.atualizar(conexao, atual)
    except Exception as erro:
        log.exception("Falha ao importar os conceitos do vault")
        return novas, atualizadas, str(erro)
    return novas, atualizadas, ""


def filtrar_tudo(deps, rodadas: int | None = None) -> ResultadoFiltro | None:
    """Importa os conceitos do vault e roda o filtro com trava (uma execução por vez)."""
    with deps.banco() as conexao:
        if not envios.reservar_trava(conexao, TRAVA_FILTRO):
            raise FiltroOcupado()
    try:
        return _filtrar_tudo(deps, RODADAS_FILTRO if rodadas is None else rodadas)
    finally:
        with deps.banco() as conexao:
            envios.liberar(conexao, TRAVA_FILTRO)


def _filtrar_tudo(deps, rodadas: int) -> ResultadoFiltro | None:
    """Rodadas de LIMITE_FILTRO ideias.

    Se uma rodada depois da primeira falhar (ex.: limite por minuto do Groq grátis),
    guarda o que já foi feito; as ideias restantes continuam "nova" para a próxima vez.
    """
    importadas, atualizadas, erro_fontes = importar_fontes(deps)
    total = None
    for _ in range(rodadas):
        try:
            rodada = filtrar(deps)
        except Exception:
            if total is None:
                raise
            log.exception("Rodada extra do filtro falhou; o resto fica para a próxima")
            break
        if rodada is None:
            break
        if total is None:
            total = rodada
        else:
            total.aprovadas += rodada.aprovadas
            total.juntadas += rodada.juntadas
            total.descartadas += rodada.descartadas
            total.sem_resposta += rodada.sem_resposta
            total.notas_gravadas += rodada.notas_gravadas
            total.erro_notas = rodada.erro_notas or total.erro_notas
        feitas = len(rodada.aprovadas) + len(rodada.juntadas) + len(rodada.descartadas)
        if rodada.sem_resposta or feitas < LIMITE_FILTRO:
            break
    if total is None and (atualizadas or erro_fontes):
        total = ResultadoFiltro()
    if total:
        total.importadas, total.atualizadas_fonte, total.erro_fontes = importadas, atualizadas, erro_fontes
        with deps.banco() as conexao:
            total.restantes = ideias.contagem(conexao).get("nova", 0)
    return total


def texto_filtro(resultado: ResultadoFiltro | None, deps) -> tuple[str, Botoes]:
    if resultado is None:
        return "🧹 Nada novo para filtrar. Mande uma ideia, um conceito ou uma anotação.", []
    linhas = ["🧹 Filtro do banco de ideias"]
    if resultado.importadas or resultado.atualizadas_fonte:
        partes = []
        if resultado.importadas:
            partes.append(f"📥 {resultado.importadas} {'conceito importado' if resultado.importadas == 1 else 'conceitos importados'}")
        if resultado.atualizadas_fonte:
            partes.append(f"🔄 {resultado.atualizadas_fonte} {'atualizado' if resultado.atualizadas_fonte == 1 else 'atualizados'}")
        linhas += ["", " · ".join(partes) + " do Obsidian."]
    if resultado.erro_fontes:
        linhas += ["", f"⚠️ Não consegui ler os conceitos do Obsidian: {resultado.erro_fontes}"]
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
    if resultado.restantes:
        linhas += ["", f"⏳ {resultado.restantes} esperando: o filtro faz 2 por vez (limite do Groq grátis) e roda de novo amanhã às 20h, ou use /filtrar."]
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
    try:
        resultado = filtrar_tudo(deps)
    except FiltroOcupado:
        return {"enviados": 0, "filtradas": 0, "motivo": "outro filtro rodando"}
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
        "angulo": ideia.angulo, "texto_original": ideia.texto_original[:TEXTO_NO_POST],
    }
    if anterior:
        entrada["versao_anterior"] = anterior
        entrada["pedido"] = "Escreva uma versão diferente: outro gancho e outra estrutura."
    resposta = _ia_conteudo(deps).json(INSTRUCOES_POST, json.dumps(entrada, ensure_ascii=False),
                                       max_tokens=MAX_TOKENS_POST)
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

    1. Entrega o rascunho dos posts de hoje e dos atrasados que ainda não saíram (ex.: o Groq
       caiu ou o cron falhou no dia). Escreve na hora se a véspera falhou. Um post que falha
       não impede os outros; ele tenta de novo no dia seguinte.
    2. Escreve com antecedência os posts de amanhã (a "véspera").
    """
    hoje = deps.hoje()
    enviados = escritos = falhas = 0
    with deps.banco() as conexao:
        pendentes = ideias.pendentes_ate(conexao, hoje)
        de_amanha = ideias.do_dia(conexao, hoje + timedelta(days=1))

    for ideia in pendentes:
        try:
            if not ideia.post_texto:
                preparar_post(deps, ideia)
                escritos += 1
            titulo = "📣 Post de hoje" if ideia.data_prevista == hoje else \
                f"📣 Post atrasado (era de {descrever(ideia.data_prevista)})"
            # A chave usa a data prevista: o atrasado sai uma vez só, e um post adiado sai de novo na nova data.
            enviados += _enviar_uma_vez(deps, f"linkedin:{ideia.id}:{ideia.data_prevista}",
                                        texto_rascunho(ideia, titulo), botoes_post(ideia), enviar)
        except Exception:
            falhas += 1
            log.exception("Falha ao entregar o post %s; tenta de novo amanhã", ideia.id)

    for ideia in de_amanha:
        if ideia.post_texto:
            continue
        try:
            preparar_post(deps, ideia)
            escritos += 1
        except Exception:
            log.exception("Falha ao escrever o post de amanhã")  # amanhã tenta de novo
    return {"enviados": enviados, "escritos": escritos, "falhas": falhas}


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
    aviso = ""
    if nova_data is None:
        base = (ideia.data_prevista or hoje) + timedelta(days=adiar_dias)
        nova_data = ideias.proxima_data_livre(ocupadas, hoje, a_partir=base)
    elif nova_data in ocupadas or nova_data <= hoje:  # botão antigo: o dia foi ocupado depois
        escolhida, nova_data = nova_data, ideias.data_pedida_livre(nova_data, ocupadas, hoje)
        aviso = f"\n({descrever(escolhida).capitalize()} já não estava livre.)"
    ideia.data_prevista = nova_data
    _salvar(deps, ideia)
    return f"📅 “{ideia.nome}” agora sai {descrever(nova_data)}.{aviso}", []


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
