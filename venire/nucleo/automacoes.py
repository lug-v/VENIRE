"""Mensagens automáticas: aviso 1 hora antes, resumo da manhã, da noite e da semana.

As funções montam o texto e decidem o que enviar; quem chama (a rota /cron) faz o envio.
Cada envio passa pela tabela `envios`, então nada chega duplicado mesmo que o agendador
chame a mesma rota duas vezes.
"""

from datetime import date, datetime, timedelta
import logging
from typing import Callable

from venire import envios
from venire.datas import descrever, inicio_da_semana
from venire.servicos import eventos, financas, gastos

log = logging.getLogger("venire")

LIMITE_CHUVA = 50  # % a partir do qual o Venire avisa sobre chuva num compromisso


# ---------------------------------------------------------------- Peças dos resumos

def _inicio(item: dict) -> datetime | None:
    texto = item.get("start", {}).get("dateTime")
    return datetime.fromisoformat(texto) if texto else None


def _fim(item: dict) -> datetime | None:
    texto = item.get("end", {}).get("dateTime")
    return datetime.fromisoformat(texto) if texto else None


def alertas_de_chuva(itens: list[dict], previsao) -> list[str]:
    """Compromissos com horário cuja chance de chuva passa do limite."""
    alertas = []
    for item in itens:
        inicio, fim = _inicio(item), _fim(item)
        if not inicio or not previsao:
            continue
        ultima_hora = min(23, (fim or inicio).hour + (1 if fim and fim.minute else 0))
        horas = range(inicio.hour, max(inicio.hour, ultima_hora) + 1)
        chance = max((previsao.chuva_por_hora.get(h, 0) for h in horas), default=0)
        if chance >= LIMITE_CHUVA:
            alertas.append(f"☔ {chance}% de chance de chuva às {inicio:%H}h, "
                           f"no horário de “{item.get('summary', 'compromisso')}”. Leve guarda-chuva.")
    return alertas


def _previsao(deps, dia: date):
    if not deps.clima:
        return None
    try:
        return deps.clima().previsao(dias=3).get(dia)
    except Exception:
        log.exception("Falha ao buscar a previsão do tempo")
        return None


def resumo_do_dia(deps, dia: date, titulo: str, com_dolar_e_gastos: bool) -> str:
    """Monta o resumo de um dia. Cada parte que falhar é pulada sem derrubar o resto."""
    linhas = [titulo, ""]

    previsao = _previsao(deps, dia)
    if previsao:
        linhas.append(previsao.resumo(deps.cidade))

    if com_dolar_e_gastos and deps.cambio:
        try:
            cotacao = deps.cambio().ultima(dia)
            if cotacao:
                linhas.append(cotacao.resumo())
        except Exception:
            log.exception("Falha ao buscar a cotação do dólar")

    itens: list[dict] = []
    if deps.calendario:
        try:
            itens = deps.calendario().do_dia(dia)
        except Exception:
            log.exception("Falha ao ler o Google Agenda")
            linhas += ["", "📅 Não consegui ler a agenda agora."]
        else:
            linhas += ["", eventos.lista_do_dia("📅 Compromissos", itens)]
            alertas = alertas_de_chuva(itens, previsao)
            if alertas:
                linhas += [""] + alertas

    if com_dolar_e_gastos:
        try:
            with deps.banco() as conexao:
                bloco = financas.bloco_do_mes(conexao, dia)
            if bloco:
                linhas += ["", bloco]
        except Exception:
            log.exception("Falha ao montar o saldo do mês")

    return "\n".join(linhas).strip()


def titulo_manha(dia: date) -> str:
    return f"☀️ Bom dia! {descrever(dia).capitalize()}"


def titulo_noite(dia: date) -> str:
    return f"🌙 Amanhã é {descrever(dia)}"


def resumo_semana_passada(deps, hoje: date) -> str:
    domingo = inicio_da_semana(hoje) - timedelta(days=7)
    sabado = domingo + timedelta(days=6)
    with deps.banco() as conexao:
        return gastos.resumo(conexao, domingo, sabado,
                             f"Gastos da semana passada ({domingo:%d/%m} a {sabado:%d/%m})")


# ---------------------------------------------------------------- Execuções agendadas

def _enviar_uma_vez(deps, chave: str, texto: str, enviar: Callable[[str], None]) -> bool:
    with deps.banco() as conexao:
        if not envios.reservar(conexao, chave):
            return False
    try:
        enviar(texto)
    except Exception:
        with deps.banco() as conexao:
            envios.liberar(conexao, chave)  # tenta de novo na próxima chamada
        raise
    return True


def executar_manha(deps, enviar: Callable[[str], None]) -> dict:
    hoje = deps.hoje()
    enviados = 0
    texto = resumo_do_dia(deps, hoje, titulo_manha(hoje), com_dolar_e_gastos=True)
    enviados += _enviar_uma_vez(deps, f"manha:{hoje}", texto, enviar)
    if hoje.weekday() == 0:  # segunda-feira
        enviados += _enviar_uma_vez(deps, f"semana:{hoje}", resumo_semana_passada(deps, hoje), enviar)
    if hoje.day == 1:  # fechamento do mês que acabou
        mes_passado = hoje - timedelta(days=1)
        with deps.banco() as conexao:
            texto_fechamento = financas.fechamento(conexao, mes_passado)
        if texto_fechamento:
            enviados += _enviar_uma_vez(deps, f"fechamento:{mes_passado:%Y-%m}", texto_fechamento, enviar)
    return {"enviados": enviados}


def executar_noite(deps, enviar: Callable[[str], None]) -> dict:
    amanha = deps.hoje() + timedelta(days=1)
    texto = resumo_do_dia(deps, amanha, titulo_noite(amanha), com_dolar_e_gastos=False)
    return {"enviados": int(_enviar_uma_vez(deps, f"noite:{amanha}", texto, enviar))}


def lembretes_proximos(itens: list[dict], agora: datetime) -> list[tuple[str, str]]:
    """Compromissos que começam na próxima hora → (chave, texto do aviso)."""
    avisos = []
    for item in itens:
        inicio = _inicio(item)
        if not inicio:
            continue  # dia inteiro não tem aviso de 1 hora
        minutos = (inicio - agora).total_seconds() / 60
        if 0 < minutos <= 60:
            quando = "Em 1 hora" if minutos > 55 else f"Em {round(minutos)} min"
            texto = f"⏰ {quando}: {item.get('summary', 'compromisso')} ({inicio:%H:%M})"
            if item.get("location"):
                texto += f"\n📍 {item['location']}"
            avisos.append((f"lembrete:{item.get('id')}:{inicio.isoformat()}", texto))
    return avisos


def executar_lembretes(deps, enviar: Callable[[str], None]) -> dict:
    if not deps.calendario:
        return {"enviados": 0, "motivo": "agenda não configurada"}
    agora = deps.agora()
    itens = deps.calendario().listar(agora, agora + timedelta(minutes=61))
    enviados = sum(_enviar_uma_vez(deps, chave, texto, enviar) for chave, texto in lembretes_proximos(itens, agora))
    return {"enviados": enviados}
