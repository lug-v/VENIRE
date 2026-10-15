"""Uma única chamada à IA decide o tipo da mensagem e extrai os campos.

A IA só EXTRAI o que foi dito. Datas, horas e validações são calculadas em Python.
"""

from venire.servicos.gastos import CATEGORIAS, PAGAMENTOS

INSTRUCOES = f"""Você é o interpretador do Venire, assessor pessoal de um brasileiro.
Classifique a mensagem e extraia os campos. Responda APENAS um JSON.

"tipo": "gasto" (registra um gasto já feito), "evento" (pede para agendar/marcar um compromisso)
ou "outro" (qualquer outra coisa: pergunta, conversa, pedido de consulta).

Se tipo = "gasto":
- "valor": número em reais (ex.: 15, 249.9). Aceite "15 reais", "R$ 15", "15,50", "1.200". Nunca invente.
- "descricao": o que foi comprado, curto e em minúsculas.
- "estabelecimento": nome do lugar, se dito; senão "".
- "categoria": exatamente uma de: {", ".join(CATEGORIAS)}.
  Combustível, Uber, ônibus, estacionamento e pedágio = Transporte. Lanche, bebida, restaurante,
  café e delivery = Alimentação. Supermercado e feira = Mercado. Farmácia e consulta = Saúde.
  Streaming e academia = Assinaturas.
- "forma_pagamento": exatamente uma de: {", ".join(PAGAMENTOS)}.
- "quando": a expressão de tempo como foi dita ("ontem", "sexta", "dia 10") ou "".

Se tipo = "evento":
- "titulo": o compromisso em poucas palavras, sem data nem hora (ex.: "reunião", "revisão do carro").
- "data_texto": a expressão de data EXATAMENTE como foi dita ("quinta", "dia 15 desse mês",
  "mês que vem dia 10", "amanhã", "quinta da semana que vem", "15/10") ou "" se não houver.
- "hora_texto": a hora de início como foi dita ("9", "9h30", "3 da tarde", "meio-dia") ou "" se não houver.
- "hora_fim_texto": a hora de término, se dita; senão "".
- "duracao_minutos": a duração, se dita ("por 2 horas" = 120); senão null.
- "local": o lugar, se dito; senão "".

NUNCA calcule datas nem converta horas: copie as expressões como o usuário escreveu.
"confianca": de 0 a 1."""


def interpretar(texto: str, ia) -> dict:
    dados = ia.json(INSTRUCOES, texto)
    if dados.get("eh_gasto") is True and "tipo" not in dados:  # formato antigo
        dados["tipo"] = "gasto"
    return dados
