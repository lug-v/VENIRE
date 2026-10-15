"""Decide o que fazer com cada mensagem e com cada clique em botão.

Este arquivo não sabe nada de Telegram: recebe texto e devolve uma Resposta
(texto + botões). Isso permite, no futuro, usar o mesmo núcleo num app de celular.
As dependências (banco, IA, planilha, agenda) chegam de fora, o que facilita os testes.
"""

from dataclasses import dataclass, field
from datetime import date, time, timedelta
import logging
from typing import Callable

from venire import pendencias
from venire.datas import descrever, hoje, hora_atual
from venire.nucleo.interpretador import interpretar
from venire.servicos import eventos, gastos
from venire.servicos.eventos import Evento

log = logging.getLogger("venire")

AJUDA = (
    "Sou o Venire, seu assessor pessoal. 👋\n\n"
    "📅 Agenda\n"
    "• marcar reunião às 9 na quinta\n"
    "• agendar mês que vem dia 10 revisão do carro\n"
    "• dentista dia 20 às 14h30\n"
    "Veja o dia com /agenda\n\n"
    "💰 Gastos\n"
    "• GASTEI 250 GASOLINA\n"
    "• gasto 15 reais energético\n"
    "• almoço 32,90 ontem no débito\n"
    "Resumos: /gastos (mês) e /semana\n\n"
    "Em breve: 🎙️ áudio, 📸 foto da nota e ☀️ resumo das 6h30."
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
    hoje: Callable[[], date] = hoje
    hora_atual: Callable[[], time] = hora_atual


# ---------------------------------------------------------------- Mensagens

def processar_texto(texto: str, deps: Dependencias, nome: str = "") -> Resposta:
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
    if comando:
        return Resposta("Não conheço esse comando. Use /ajuda para ver o que eu sei fazer.")
    if not texto:
        return Resposta("Por enquanto eu só entendo texto. Áudio e foto chegam nas próximas fases. 😉")

    dados = interpretar(texto, deps.ia())
    tipo = dados.get("tipo")
    if tipo == "gasto":
        return registrar_gasto(dados, texto, deps)
    if tipo == "evento":
        return agendar_pedido(dados, texto, deps)
    saudacao = f", {nome}" if nome else ""
    return Resposta(
        f"Ainda não sei fazer isso{saudacao}. 🙂\n"
        "Eu registro gastos (“gastei 40 no mercado”) e agendo compromissos (“reunião quinta às 9”). "
        "Perguntas livres chegam numa próxima fase. Use /ajuda para ver exemplos."
    )


def registrar_gasto(dados: dict, texto: str, deps: Dependencias) -> Resposta:
    gasto = gastos.de_dados(dados, texto, deps.hoje())
    if gasto is None:
        return Resposta("Não encontrei o valor do gasto. Tente algo como “gastei 40 no mercado”.")
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
