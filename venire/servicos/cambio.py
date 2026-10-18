"""Cotação do dólar (PTAX) pela API de dados abertos do Banco Central (gratuita, sem chave).

A PTAX do dia sai no início da tarde e não existe em fins de semana e feriados,
então o resumo da manhã mostra a do último dia útil.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import httpx

API = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
       "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)")


@dataclass
class Cotacao:
    valor: float
    dia: date
    variacao: float | None  # % em relação à cotação anterior

    def resumo(self) -> str:
        valor = f"R$ {self.valor:.2f}".replace(".", ",")
        texto = f"💵 Dólar (PTAX {self.dia:%d/%m}): {valor}"
        if self.variacao is not None:
            seta = "▲" if self.variacao > 0 else "▼" if self.variacao < 0 else "="
            texto += f" {seta} {abs(self.variacao):.1f}%".replace(".", ",")
        return texto


class Cambio:
    def __init__(self, cliente: httpx.Client | None = None):
        self._cliente = cliente or httpx.Client(timeout=15)

    def ultima(self, hoje: date) -> Cotacao | None:
        inicio = hoje - timedelta(days=10)  # cobre fins de semana e feriados
        resposta = self._cliente.get(API, params={
            "@dataInicial": f"'{inicio:%m-%d-%Y}'",
            "@dataFinalCotacao": f"'{hoje:%m-%d-%Y}'",
            "$format": "json",
            "$select": "cotacaoVenda,dataHoraCotacao",
        })
        resposta.raise_for_status()
        cotacoes = sorted(resposta.json().get("value", []), key=lambda c: c["dataHoraCotacao"])
        if not cotacoes:
            return None
        atual = cotacoes[-1]
        anterior = cotacoes[-2]["cotacaoVenda"] if len(cotacoes) > 1 else None
        variacao = (atual["cotacaoVenda"] / anterior - 1) * 100 if anterior else None
        return Cotacao(float(atual["cotacaoVenda"]), date.fromisoformat(atual["dataHoraCotacao"][:10]), variacao)
