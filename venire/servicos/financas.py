"""Saldo do mês, projeção do fechamento e fechamento do mês anterior.

Projeção (mesma lógica da Aula 6 do clari-analytics):
- Gastos fixos (Moradia, Educação, Assinaturas) se repetem: usa o maior valor entre o
  que já foi pago neste mês e o total do mês anterior.
- Gastos variáveis seguem o ritmo diário do mês atual × dias do mês.
- Receitas: usa o maior valor entre o que já entrou e o total do mês anterior
  (o salário pode ainda não ter caído).
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from venire.servicos import receitas
from venire.servicos.gastos import formatar_reais

FIXAS = ("Moradia", "Educação", "Assinaturas")
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def reais(valor: Decimal) -> str:
    """R$ sem sinal; o sentido (sobra ou falta) vai no texto."""
    return formatar_reais(abs(valor))


def _gastos(conexao, inicio: date, fim: date) -> tuple[Decimal, Decimal]:
    fixos, variaveis = conexao.execute(
        """SELECT COALESCE(SUM(amount) FILTER (WHERE category = ANY(%s)), 0),
                  COALESCE(SUM(amount) FILTER (WHERE NOT (category = ANY(%s))), 0)
           FROM expenses WHERE spent_on BETWEEN %s AND %s""",
        (list(FIXAS), list(FIXAS), inicio, fim),
    ).fetchone()
    return Decimal(fixos), Decimal(variaveis)


def _mes_anterior(dia: date) -> tuple[date, date]:
    fim = dia.replace(day=1) - timedelta(days=1)
    return fim.replace(day=1), fim


@dataclass
class Mes:
    entrou: Decimal
    saiu: Decimal

    @property
    def saldo(self) -> Decimal:
        return self.entrou - self.saiu


def mes(conexao, inicio: date, fim: date) -> Mes:
    fixos, variaveis = _gastos(conexao, inicio, fim)
    return Mes(receitas.total(conexao, inicio, fim), fixos + variaveis)


def projecao(conexao, hoje: date) -> Mes:
    inicio = hoje.replace(day=1)
    dias_no_mes = monthrange(hoje.year, hoje.month)[1]
    fixos, variaveis = _gastos(conexao, inicio, hoje)
    ant_inicio, ant_fim = _mes_anterior(hoje)
    fixos_ant, _ = _gastos(conexao, ant_inicio, ant_fim)
    gastos_previstos = max(fixos, fixos_ant) + variaveis / hoje.day * dias_no_mes
    receitas_previstas = max(receitas.total(conexao, inicio, hoje), receitas.total(conexao, ant_inicio, ant_fim))
    return Mes(receitas_previstas, gastos_previstos.quantize(Decimal("0.01")))


def bloco_do_mes(conexao, hoje: date, com_projecao: bool = True) -> str:
    """Texto com entradas, saídas, saldo e (a partir do dia 3) a projeção do fechamento."""
    atual = mes(conexao, hoje.replace(day=1), hoje)
    if not atual.entrou and not atual.saiu:
        return ""
    nome = MESES[hoje.month - 1].capitalize()
    sinal = "sobra" if atual.saldo >= 0 else "falta"
    linhas = [
        f"💰 {nome} até agora",
        f"Entrou {reais(atual.entrou)} · Saiu {reais(atual.saiu)}",
        f"Saldo: {sinal} de {reais(atual.saldo)}",
    ]
    if atual.entrou:
        linhas.append(f"Você já gastou {atual.saiu / atual.entrou * 100:.0f}% do que entrou.")
    if com_projecao and hoje.day >= 3:
        previsto = projecao(conexao, hoje)
        if previsto.entrou:
            sentido = "sobra" if previsto.saldo >= 0 else "falta"
            linhas.append(f"📈 No ritmo atual, {nome.lower()} fecha com {sentido} de ~{reais(previsto.saldo)}.")
    return "\n".join(linhas)


def fechamento(conexao, mes_referencia: date) -> str:
    """Resumo de um mês já encerrado (mes_referencia = qualquer dia daquele mês)."""
    inicio = mes_referencia.replace(day=1)
    fim = inicio.replace(day=monthrange(inicio.year, inicio.month)[1])
    atual = mes(conexao, inicio, fim)
    nome = MESES[inicio.month - 1]
    if not atual.entrou and not atual.saiu:
        return ""
    linhas = [f"📊 {nome.capitalize()} fechou", "",
              f"Entrou: {reais(atual.entrou)}", f"Saiu: {reais(atual.saiu)}"]
    if atual.entrou:
        percentual = abs(atual.saldo) / atual.entrou * 100
        if atual.saldo >= 0:
            linhas.append(f"✅ Sobrou: {reais(atual.saldo)} ({percentual:.0f}% da renda)")
        else:
            linhas.append(f"⚠️ Faltou: {reais(atual.saldo)} ({percentual:.0f}% acima da renda)")
    else:
        linhas.append("Nenhuma receita registrada no mês.")

    ant_inicio, ant_fim = _mes_anterior(inicio)
    anterior = mes(conexao, ant_inicio, ant_fim)
    if anterior.entrou or anterior.saiu:
        diferenca = atual.saldo - anterior.saldo
        seta = "▲" if diferenca > 0 else "▼" if diferenca < 0 else "="
        linhas.append(f"{seta} Saldo {reais(diferenca)} {'melhor' if diferenca >= 0 else 'pior'} "
                      f"que em {MESES[ant_inicio.month - 1]}")

    if atual.saldo < 0:
        maiores = conexao.execute(
            """SELECT category, SUM(amount) FROM expenses WHERE spent_on BETWEEN %s AND %s
               GROUP BY category ORDER BY SUM(amount) DESC LIMIT 3""", (inicio, fim)).fetchall()
        linhas += ["", "O que mais pesou:"] + [f"• {c}: {reais(Decimal(v))}" for c, v in maiores]
    return "\n".join(linhas)
