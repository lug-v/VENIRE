"""Decide o que fazer com cada mensagem.

Este arquivo não sabe nada de Telegram: recebe texto e devolve uma Resposta
(texto + botões). Isso permite, no futuro, usar o mesmo núcleo num app de celular.
As dependências (banco, IA, planilha) chegam de fora, o que facilita os testes.
"""

from dataclasses import dataclass, field
from datetime import date
import logging
from typing import Callable

from venire.datas import hoje
from venire.servicos import gastos

log = logging.getLogger("venire")

AJUDA = (
    "Sou o Venire, seu assessor pessoal. 👋\n\n"
    "💰 Gastos (já funciona!)\n"
    "Me mande algo como:\n"
    "• gasto 15 reais energético\n"
    "• GASTEI 250 GASOLINA\n"
    "• almoço 32,90 ontem no débito\n\n"
    "Resumos: /gastos (mês) e /semana\n\n"
    "Em breve: 📅 agenda no Google Agenda, 🎙️ áudio, 📸 foto da nota e ☀️ resumo das 6h30."
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
    hoje: Callable[[], date] = hoje


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
    if comando:
        return Resposta("Não conheço esse comando. Use /ajuda para ver o que eu sei fazer.")
    if not texto:
        return Resposta("Por enquanto eu só entendo texto. Áudio e foto chegam nas próximas fases. 😉")

    gasto = gastos.interpretar(texto, deps.ia(), deps.hoje())
    if gasto is None:
        saudacao = f", {nome}" if nome else ""
        return Resposta(
            f"Ainda não sei fazer isso{saudacao}. 🙂\n"
            "Por enquanto eu registro gastos, como “gastei 40 no mercado”. Agenda chega na próxima fase."
        )

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


def processar_botao(dado: str, deps: Dependencias) -> Resposta:
    acao, _, gasto_id = dado.partition(":")
    if acao != "desfazer" or not gasto_id:
        return Resposta("Botão desconhecido.")
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
