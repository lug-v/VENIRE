"""Decide o que fazer com cada mensagem e com cada clique em botão.

Este arquivo não sabe nada de Telegram: recebe texto e devolve uma Resposta
(texto + botões). Isso permite, no futuro, usar o mesmo núcleo num app de celular.
As dependências (banco, IA, planilha, agenda) chegam de fora, o que facilita os testes.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import logging
from typing import Callable

from venire import pendencias
from venire.config import FUSO
from venire.datas import data_ja_passou, descrever, hoje, hora_atual
from venire.nucleo import automacoes
from venire.nucleo.interpretador import interpretar, interpretar_foto
from venire.servicos import contas, eventos, financas, gastos, receitas, tarefas
from venire.servicos.eventos import Evento

log = logging.getLogger("venire")

AJUDA = (
    "Sou o Venire, seu assessor pessoal. 👋\n\n"
    "📅 Agenda\n"
    "• marcar reunião às 9 na quinta\n"
    "• agendar mês que vem dia 10 revisão do carro\n"
    "• dentista dia 20 às 14h30\n"
    "Veja o dia com /agenda, /hoje (resumo completo) e /amanha\n\n"
    "💰 Gastos\n"
    "• GASTEI 250 GASOLINA\n"
    "• gasto 15 reais energético\n"
    "• almoço 32,90 ontem no débito\n"
    "Resumos: /gastos (mês) e /semana\n\n"
    "💵 Receitas\n"
    "• recebi 2000 de salário\n"
    "• ganhei 50 reais\n"
    "• comissão de 800\n"
    "Saldo do mês: /saldo\n\n"
    "🔁 Rotina e contas\n"
    "• toda terça e quinta Muay Thai às 19h\n"
    "• aluguel 1450 todo dia 5 (conta fixa)\n"
    "• lembrar de comprar ração (tarefa)\n"
    "Veja com /contas e /tarefas\n\n"
    "🎙️ Áudio: fale do mesmo jeito que escreveria.\n"
    "📸 Foto: mande a nota fiscal, o comprovante do Pix ou um convite.\n\n"
    "⏰ Automático: aviso 1 hora antes de cada compromisso, resumo às 6h30 (clima, dólar, agenda, contas, tarefas e saldo), "
    "resumo da semana às segundas e a agenda de amanhã às 21h."
)


@dataclass
class Resposta:
    texto: str
    botoes: list[tuple[str, str]] = field(default_factory=list)  # (rótulo, dado do clique)


@dataclass
class Dependencias:
    ia: Callable[[], object]          # cria o cliente da IA quando precisar
    banco: Callable[[], object]       # context manager de conexão
    planilha: Callable[[], object] | None = None
    calendario: Callable[[], object] | None = None
    clima: Callable[[], object] | None = None
    cambio: Callable[[], object] | None = None
    cidade: str = "Bento Gonçalves"
    hoje: Callable[[], date] = hoje
    hora_atual: Callable[[], time] = hora_atual
    agora: Callable[[], datetime] = lambda: datetime.now(ZoneInfo(FUSO))


# ---------------------------------------------------------------- Mensagens

def processar_texto(texto: str, deps: Dependencias, nome: str = "", origem: str = "texto") -> Resposta:
    texto = (texto or "").strip()
    comando = texto.split()[0].lower().split("@")[0] if texto.startswith("/") else ""

    if comando in ("/start", "/ajuda"):
        return Resposta(AJUDA)
    if comando == "/gastos":
        with deps.banco() as conexao:
            return Resposta(gastos.resumo_mes(conexao, deps.hoje()))
    if comando == "/saldo":
        with deps.banco() as conexao:
            bloco = financas.bloco_do_mes(conexao, deps.hoje())
        return Resposta(bloco or "💰 Ainda não há gastos nem receitas registrados neste mês.")
    if comando == "/semana":
        with deps.banco() as conexao:
            return Resposta(gastos.resumo_semana(conexao, deps.hoje()))
    if comando == "/agenda":
        return mostrar_agenda(deps)
    if comando == "/contas":
        return mostrar_contas(deps)
    if comando == "/tarefas":
        return mostrar_tarefas(deps)
    if comando == "/hoje":
        dia = deps.hoje()
        return Resposta(automacoes.resumo_do_dia(deps, dia, automacoes.titulo_manha(dia), com_dolar_e_gastos=True))
    if comando == "/amanha":
        dia = deps.hoje() + timedelta(days=1)
        return Resposta(automacoes.resumo_do_dia(deps, dia, automacoes.titulo_noite(dia), com_dolar_e_gastos=False))
    if comando:
        return Resposta("Não conheço esse comando. Use /ajuda para ver o que eu sei fazer.")
    if not texto:
        return Resposta("Me mande um texto, um áudio ou uma foto. 🙂")

    dados = interpretar(texto, deps.ia())
    tipo = dados.get("tipo")
    if tipo == "gasto":
        return registrar_gasto(dados, texto, deps, origem)
    if tipo == "receita":
        return registrar_receita(dados, texto, deps, origem)
    if tipo == "evento":
        return agendar_pedido(dados, texto, deps)
    if tipo == "eventos":
        lista = [e for e in (dados.get("eventos") or []) if isinstance(e, dict)]
        if len(lista) == 1:
            return agendar_pedido(lista[0], texto, deps)
        if lista:
            return agendar_lote(lista, texto, deps)
    if tipo == "conta":
        return cadastrar_conta(dados, deps)
    if tipo == "tarefa":
        return anotar_tarefa(dados, texto, deps)
    saudacao = f", {nome}" if nome else ""
    return Resposta(
        f"Ainda não sei fazer isso{saudacao}. 🙂\n"
        "Eu registro gastos e receitas, agendo compromissos, cadastro contas fixas e anoto tarefas. "
        "Perguntas livres chegam numa próxima fase. Use /ajuda para ver exemplos."
    )


def processar_audio(audio: bytes, nome_arquivo: str, deps: Dependencias, nome: str = "") -> Resposta:
    """O áudio vira texto (Whisper) e segue exatamente o mesmo caminho de uma mensagem escrita."""
    transcricao = deps.ia().transcrever(audio, nome_arquivo)
    if not transcricao:
        return Resposta("🎙️ Não consegui entender o áudio. Pode tentar de novo, falando um pouco mais perto?")
    resposta = processar_texto(transcricao, deps, nome, origem="audio")
    resposta.texto = f"🎙️ “{transcricao}”\n\n{resposta.texto}"
    return resposta


def processar_foto(imagem: bytes, tipo: str, legenda: str, deps: Dependencias) -> list[Resposta]:
    """Nota fiscal ou comprovante vira gasto; convite ou agenda vira compromissos."""
    dados = interpretar_foto(imagem, tipo, legenda, deps.ia())
    origem_texto = f"Foto: {legenda}" if legenda else "Foto"
    if dados.get("tipo") == "gasto":
        resposta = registrar_gasto(dados, origem_texto, deps, origem="foto")
        resposta.texto = "📸 " + resposta.texto
        return [resposta]
    if dados.get("tipo") == "receita":
        resposta = registrar_receita(dados, origem_texto, deps, origem="foto")
        resposta.texto = "📸 " + resposta.texto
        return [resposta]
    if dados.get("tipo") == "eventos":
        lista = [e for e in (dados.get("eventos") or []) if isinstance(e, dict)]
        if len(lista) == 1:
            return [agendar_pedido(lista[0], origem_texto, deps)]
        if lista:
            resposta = agendar_lote(lista, origem_texto, deps)
            resposta.texto = "📸 " + resposta.texto
            return [resposta]
    if legenda.strip():  # foto sem nota nem compromisso: a legenda vale como mensagem
        return [processar_texto(legenda, deps, origem="foto")]
    return [Resposta(
        "📸 Não encontrei uma nota fiscal, comprovante ou compromisso nessa foto.\n"
        "Se for um gasto, mande uma legenda junto, como “gasolina 250”."
    )]


def registrar_gasto(dados: dict, texto: str, deps: Dependencias, origem: str = "texto") -> Resposta:
    gasto = gastos.de_dados(dados, texto, deps.hoje())
    if gasto is None:
        return Resposta("Não encontrei o valor do gasto. Tente algo como “gastei 40 no mercado”.")
    gasto.origem = origem
    with deps.banco() as conexao:
        gastos.salvar(conexao, gasto)
        conta = contas.conta_do_texto(conexao, texto, gasto.data)
        if conta:
            contas.marcar_paga(conexao, conta.id, gasto.data, gasto.id)

    texto_resposta = gastos.confirmacao(gasto)
    if conta:
        texto_resposta += f"\n📌 Conta “{conta.nome}” de {contas.MESES[gasto.data.month - 1]} marcada como paga."
    if deps.planilha:
        try:
            deps.planilha().adicionar(gasto)
        except Exception as erro:  # a planilha é uma cópia; o gasto já está salvo no banco
            log.exception("Falha ao copiar o gasto para a planilha")
            texto_resposta += f"\n\n⚠️ Salvei no banco, mas não consegui copiar para a planilha.\nMotivo: {erro}"
    return Resposta(texto_resposta, [("↩️ Desfazer", f"desfazer:{gasto.id}")])


def registrar_receita(dados: dict, texto: str, deps: Dependencias, origem: str = "texto") -> Resposta:
    receita = receitas.de_dados(dados, texto, deps.hoje())
    if receita is None:
        return Resposta("Não encontrei o valor recebido. Tente algo como “recebi 2000 de salário”.")
    receita.origem = origem
    with deps.banco() as conexao:
        receitas.salvar(conexao, receita)
        saldo = financas.mes(conexao, deps.hoje().replace(day=1), deps.hoje()).saldo

    texto_resposta = receitas.confirmacao(receita)
    sentido = "sobra" if saldo >= 0 else "falta"
    texto_resposta += f"\n\n💰 Saldo do mês: {sentido} de {financas.reais(saldo)}"
    if deps.planilha:
        try:
            deps.planilha().adicionar_receita(receita)
        except Exception as erro:
            log.exception("Falha ao copiar a receita para a planilha")
            texto_resposta += f"\n\n⚠️ Salvei no banco, mas não consegui copiar para a planilha.\nMotivo: {erro}"
    return Resposta(texto_resposta, [("↩️ Desfazer", f"dr:{receita.id}")])


def cadastrar_conta(dados: dict, deps: Dependencias) -> Resposta:
    conta = contas.de_dados(dados)
    if conta is None:
        return Resposta("📌 Para cadastrar uma conta fixa, diga o nome e o dia do vencimento, "
                        "como “aluguel 1450 todo dia 5” ou “luz vence dia 10”.")
    with deps.banco() as conexao:
        contas.salvar(conexao, conta, deps.hoje())
    return Resposta(contas.confirmacao(conta, deps.hoje()), [("↩️ Desfazer", f"cx:{conta.id}")])


def mostrar_contas(deps: Dependencias) -> Resposta:
    with deps.banco() as conexao:
        texto, abertas = contas.lista_do_mes(conexao, deps.hoje())
    return Resposta(texto, [(f"✅ Paguei {c.nome}", f"cp:{c.id}") for c in abertas])


def pagar_conta(conta_id: str, deps: Dependencias) -> Resposta:
    """Botão "Paguei": marca a conta do mês e registra o gasto com o valor cadastrado."""
    hoje = deps.hoje()
    with deps.banco() as conexao:
        conta = contas.buscar(conexao, conta_id)
        if conta is None:
            return Resposta("Essa conta não existe mais.")
        if conta.id in contas.pagas_no_mes(conexao, hoje):
            return Resposta(f"✅ {conta.nome} já está paga neste mês.")
        if conta.valor is None:
            contas.marcar_paga(conexao, conta.id, hoje)
            return Resposta(f"✅ {conta.nome} marcada como paga.\n"
                            f"Como o valor varia, registre o gasto: “paguei {conta.nome.lower()} 180”.")
    resposta = registrar_gasto({"valor": str(conta.valor), "descricao": conta.nome.lower(),
                                "categoria": conta.categoria}, f"Conta fixa: {conta.nome}", deps, origem="conta")
    return resposta


def anotar_tarefa(dados: dict, texto: str, deps: Dependencias) -> Resposta:
    descricao = str(dados.get("texto") or texto).strip()
    with deps.banco() as conexao:
        tarefa_id = tarefas.salvar(conexao, descricao, deps.hoje())
        total = len(tarefas.abertas(conexao))
    plural = "tarefa aberta" if total == 1 else "tarefas abertas"
    return Resposta(f"📝 Anotado: {tarefas.titulo(descricao)}\nVocê tem {total} {plural}. Veja com /tarefas.",
                    [("↩️ Desfazer", f"tx:{tarefa_id}")])


def mostrar_tarefas(deps: Dependencias, aviso: str = "") -> Resposta:
    with deps.banco() as conexao:
        texto, abertas = tarefas.lista(conexao, deps.hoje())
    if aviso:
        texto = f"{aviso}\n\n{texto}"
    return Resposta(texto, [(f"✅ {t[:40]}", f"tf:{i}") for i, t in abertas])


def agendar_pedido(dados: dict, texto: str, deps: Dependencias) -> Resposta:
    if not deps.calendario:
        return Resposta("📅 A agenda ainda não está configurada: falta cadastrar o GOOGLE_CALENDAR_ID na Vercel.")
    resultado = eventos.de_dados(dados, texto, deps.hoje(), deps.hora_atual())
    if resultado.problema:
        return Resposta(resultado.problema)
    evento = resultado.evento

    if resultado.opcoes_de_data:  # ex.: hoje é quinta e o pedido diz "na quinta"
        with deps.banco() as conexao:
            pid = pendencias.guardar(conexao, "escolher_dia", {
                "evento": evento.model_dump(mode="json"),
                "opcoes": [d.isoformat() for d in resultado.opcoes_de_data],
            })
        botoes = []
        for indice, dia in enumerate(resultado.opcoes_de_data):
            rotulo = f"Hoje ({dia:%d/%m})" if dia == deps.hoje() else descrever(dia).capitalize()
            botoes.append((rotulo, f"dia:{pid}:{indice}"))
        return Resposta(f"📅 Para qual dia é “{evento.titulo}”?", botoes)

    return agendar(evento, deps)


LIMITE_LOTE = 25


def agendar_lote(lista: list[dict], texto: str, deps: Dependencias) -> Resposta:
    """Vários compromissos de uma vez (cronograma colado, foto de agenda).

    Datas que já passaram são puladas; conflitos não travam o lote, só aparecem como aviso.
    Uma única resposta, com um botão para desfazer tudo.
    """
    if not deps.calendario:
        return Resposta("📅 A agenda ainda não está configurada: falta cadastrar o GOOGLE_CALENDAR_ID na Vercel.")
    calendario = deps.calendario()
    criados: list[Evento] = []
    passados, problemas, conflitos, falhas = [], [], [], []

    for item in lista[:LIMITE_LOTE]:
        data_texto = str(item.get("data_texto") or "")
        titulo = eventos.titulo_bonito(item.get("titulo"))
        if data_ja_passou(data_texto, deps.hoje()):
            passados.append(data_texto)
            continue
        resultado = eventos.de_dados(item, texto[:200], deps.hoje(), deps.hora_atual())
        if resultado.problema or not resultado.evento:
            problemas.append(f"{titulo} ({data_texto or 'sem data'})")
            continue
        evento = resultado.evento
        evento.nota = ""
        try:
            choques = calendario.conflitos(evento)
            calendario.criar(evento)
        except Exception as erro:
            log.exception("Falha ao agendar item do lote")
            falhas.append(f"{titulo}: {erro}")
            continue
        criados.append(evento)
        if choques:
            conflitos.append(f"{evento.titulo} ({evento.data:%d/%m}) × {choques[0].get('summary', 'outro compromisso')}")

    linhas = []
    if criados:
        plural = "compromisso" if len(criados) == 1 else "compromissos"
        linhas.append(f"📅 Agendei {len(criados)} {plural}:")
        linhas += [f"• {eventos.quando(e)} — {e.titulo}" for e in sorted(criados, key=lambda e: (e.data, e.inicio or datetime.min.time()))]
    else:
        linhas.append("📅 Não agendei nada.")
    if passados:
        linhas += ["", f"⏭️ Pulei {len(passados)} que já passaram ({passados[0]} a {passados[-1]})."
                   if len(passados) > 1 else f"⏭️ Pulei 1 que já passou ({passados[0]})."]
    if len(lista) > LIMITE_LOTE:
        linhas += ["", f"✂️ A lista tinha {len(lista)} itens; agendei só os {LIMITE_LOTE} primeiros. Mande o resto em outra mensagem."]
    if conflitos:
        linhas += ["", "⚠️ Coincidem com outros compromissos:"] + [f"• {c}" for c in conflitos]
    if problemas:
        linhas += ["", "❓ Não entendi a data de:"] + [f"• {p}" for p in problemas]
    if falhas:
        linhas += ["", "😕 Falharam no Google Agenda:"] + [f"• {f}" for f in falhas[:3]]

    botoes = []
    if criados:
        with deps.banco() as conexao:
            pid = pendencias.guardar(conexao, "lote", {"ids": [e.id for e in criados]})
        botoes = [("↩️ Desfazer tudo", f"dl:{pid}")]
    return Resposta("\n".join(linhas), botoes)


def agendar(evento: Evento, deps: Dependencias, forcar: bool = False) -> Resposta:
    calendario = deps.calendario()
    try:
        if not forcar:
            conflitos = calendario.conflitos(evento)
            if conflitos:
                with deps.banco() as conexao:
                    pid = pendencias.guardar(conexao, "evento", evento.model_dump(mode="json"))
                return Resposta(eventos.aviso_conflito(evento, conflitos), [
                    ("✅ Agendar mesmo assim", f"forcar:{pid}"),
                    ("❌ Cancelar", f"cancelar:{pid}"),
                ])
        calendario.criar(evento)
    except Exception as erro:
        log.exception("Falha ao agendar no Google Agenda")
        return Resposta(f"😕 Não consegui agendar no Google Agenda.\nMotivo: {erro}")
    return Resposta(eventos.confirmacao(evento), [("↩️ Desfazer", f"dev:{evento.id}")])


def mostrar_agenda(deps: Dependencias) -> Resposta:
    if not deps.calendario:
        return Resposta("📅 A agenda ainda não está configurada: falta cadastrar o GOOGLE_CALENDAR_ID na Vercel.")
    calendario = deps.calendario()
    dia = deps.hoje()
    try:
        hoje_itens = calendario.do_dia(dia)
        amanha_itens = calendario.do_dia(dia + timedelta(days=1))
    except Exception as erro:
        log.exception("Falha ao ler o Google Agenda")
        return Resposta(f"😕 Não consegui ler o Google Agenda.\nMotivo: {erro}")
    return Resposta(
        eventos.lista_do_dia(f"📅 Hoje ({descrever(dia)})", hoje_itens) + "\n\n"
        + eventos.lista_do_dia(f"📅 Amanhã ({descrever(dia + timedelta(days=1))})", amanha_itens)
    )


# ---------------------------------------------------------------- Botões

def processar_botao(dado: str, deps: Dependencias) -> Resposta:
    acao, _, resto = dado.partition(":")

    if acao == "desfazer" and resto:
        return desfazer_gasto(resto, deps)

    if acao == "cp" and resto:
        return pagar_conta(resto, deps)

    if acao == "cx" and resto:
        with deps.banco() as conexao:
            nome = contas.apagar(conexao, resto)
        return Resposta(f"↩️ Conta fixa “{nome}” removida." if nome else "Essa conta já tinha sido removida.")

    if acao == "tf" and resto:
        with deps.banco() as conexao:
            feita = tarefas.concluir(conexao, resto, deps.hoje())
        return mostrar_tarefas(deps, f"✅ Feito: {feita}" if feita else "")

    if acao == "tx" and resto:
        with deps.banco() as conexao:
            apagada = tarefas.apagar(conexao, resto)
        return Resposta("↩️ Tarefa removida." if apagada else "Essa tarefa já tinha sido removida.")

    if acao == "dr" and resto:
        with deps.banco() as conexao:
            apagada = receitas.apagar(conexao, resto)
        if not apagada:
            return Resposta("Essa receita já tinha sido removida.")
        if deps.planilha:
            try:
                deps.planilha().remover_receita(resto)
            except Exception:
                log.exception("Falha ao remover a receita da planilha")
                return Resposta("↩️ Receita removida do banco. ⚠️ Apague a linha na aba Receitas manualmente.")
        return Resposta("↩️ Receita desfeita.")

    if acao == "dev" and resto:
        try:
            apagado = deps.calendario().apagar(resto)
        except Exception as erro:
            log.exception("Falha ao desfazer o evento")
            return Resposta(f"😕 Não consegui remover do Google Agenda.\nMotivo: {erro}")
        return Resposta("↩️ Compromisso removido da agenda." if apagado else "Esse compromisso já tinha sido removido.")

    if acao == "dl" and resto:
        with deps.banco() as conexao:
            pendencia = pendencias.retirar(conexao, resto)
        if pendencia is None:
            return Resposta("Esse lote já foi desfeito (ou o botão expirou: ele vale por 1 dia).")
        calendario = deps.calendario()
        removidos = 0
        for evento_id in pendencia[1].get("ids", []):
            try:
                removidos += bool(calendario.apagar(evento_id))
            except Exception:
                log.exception("Falha ao remover item do lote")
        return Resposta(f"↩️ Removi {removidos} compromissos da agenda.")

    if acao in ("forcar", "cancelar", "dia"):
        pid, _, indice = resto.partition(":")
        with deps.banco() as conexao:
            pendencia = pendencias.retirar(conexao, pid)
        if pendencia is None:
            return Resposta("Esse pedido expirou ou já foi respondido.")
        tipo, dados = pendencia
        if acao == "cancelar":
            return Resposta("❌ Ok, não agendei.")
        if acao == "forcar" and tipo == "evento":
            return agendar(Evento.model_validate(dados), deps, forcar=True)
        if acao == "dia" and tipo == "escolher_dia":
            evento = Evento.model_validate(dados["evento"])
            evento.data = date.fromisoformat(dados["opcoes"][int(indice or 0)])
            return agendar(evento, deps)

    return Resposta("Botão desconhecido.")


def desfazer_gasto(gasto_id: str, deps: Dependencias) -> Resposta:
    with deps.banco() as conexao:
        contas.desmarcar_pelo_gasto(conexao, gasto_id)
        apagado = gastos.apagar(conexao, gasto_id)
    if not apagado:
        return Resposta("Esse gasto já tinha sido removido.")
    if deps.planilha:
        try:
            deps.planilha().remover(gasto_id)
        except Exception:
            log.exception("Falha ao remover o gasto da planilha")
            return Resposta("↩️ Gasto removido do banco. ⚠️ Apague a linha na planilha manualmente.")
    return Resposta("↩️ Gasto desfeito.")
