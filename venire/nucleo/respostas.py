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
from venire.datas import descrever, hoje, hora_atual
from venire.nucleo import automacoes
from venire.nucleo.interpretador import interpretar, interpretar_foto
from venire.servicos import eventos, gastos
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
    "🎙️ Áudio: fale do mesmo jeito que escreveria.\n"
    "📸 Foto: mande a nota fiscal, o comprovante do Pix ou um convite.\n\n"
    "⏰ Automático: aviso 1 hora antes de cada compromisso, resumo às 6h30 (clima, dólar, agenda e gastos), "
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
    if comando == "/semana":
        with deps.banco() as conexao:
            return Resposta(gastos.resumo_semana(conexao, deps.hoje()))
    if comando == "/agenda":
        return mostrar_agenda(deps)
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
    if tipo == "evento":
        return agendar_pedido(dados, texto, deps)
    saudacao = f", {nome}" if nome else ""
    return Resposta(
        f"Ainda não sei fazer isso{saudacao}. 🙂\n"
        "Eu registro gastos (“gastei 40 no mercado”) e agendo compromissos (“reunião quinta às 9”). "
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
    if dados.get("tipo") == "eventos":
        lista = [e for e in (dados.get("eventos") or []) if isinstance(e, dict)][:5]
        if lista:
            respostas = [agendar_pedido(e, origem_texto, deps) for e in lista]
            if len(lista) > 1:
                respostas.insert(0, Resposta(f"📸 Encontrei {len(lista)} compromissos na foto:"))
            return respostas
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

    texto_resposta = gastos.confirmacao(gasto)
    if deps.planilha:
        try:
            deps.planilha().adicionar(gasto)
        except Exception as erro:  # a planilha é uma cópia; o gasto já está salvo no banco
            log.exception("Falha ao copiar o gasto para a planilha")
            texto_resposta += f"\n\n⚠️ Salvei no banco, mas não consegui copiar para a planilha.\nMotivo: {erro}"
    return Resposta(texto_resposta, [("↩️ Desfazer", f"desfazer:{gasto.id}")])


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

    if acao == "dev" and resto:
        try:
            apagado = deps.calendario().apagar(resto)
        except Exception as erro:
            log.exception("Falha ao desfazer o evento")
            return Resposta(f"😕 Não consegui remover do Google Agenda.\nMotivo: {erro}")
        return Resposta("↩️ Compromisso removido da agenda." if apagado else "Esse compromisso já tinha sido removido.")

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
