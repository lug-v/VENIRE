"""Uma única chamada à IA decide o tipo da mensagem (ou da foto) e extrai os campos.

A IA só EXTRAI o que foi dito. Datas, horas e validações são calculadas em Python.
"""

from venire.servicos.gastos import CATEGORIAS, PAGAMENTOS
from venire.servicos.receitas import CATEGORIAS_RECEITA

INSTRUCOES = f"""Você é o interpretador do Venire, assessor pessoal de um brasileiro.
Classifique a mensagem e extraia os campos. Responda APENAS um JSON.

"tipo": "gasto" (registra um gasto já feito), "receita" (registra um dinheiro que ENTROU: "ganhei",
"recebi", "caiu o salário", "comissão de 800", "me pagaram"), "evento" (pede para agendar/marcar um
compromisso, inclusive os que se repetem: "toda terça e quinta Muay Thai às 19h"), "conta" (cadastra uma
CONTA FIXA que vence todo mês: "aluguel 1450 todo dia 5", "conta de luz vence dia 10", "internet 99,90 todo
mês dia 15"), "tarefa" (algo a fazer SEM data nem hora: "lembrar de comprar ração", "anota: ligar pro
contador", "preciso renovar a CNH") ou "outro" (qualquer outra coisa: pergunta, conversa, pedido de consulta).
"paguei o aluguel 1450" é gasto (já pagou), não conta. "me lembra de ligar pro João amanhã às 9" é evento
(tem data/hora), não tarefa.

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

Se tipo = "receita":
- "valor": número em reais. Nunca invente.
- "descricao": de onde veio, curto e em minúsculas (ex.: "salário", "venda do celular").
- "pagador": quem pagou, se dito (ex.: "João", "De Toni"); senão "".
- "categoria_receita": exatamente uma de: {", ".join(CATEGORIAS_RECEITA)}.
  Bônus e premiação de vendas = Comissão. Bico, freela e venda de algo usado = Extra.
  Dinheiro devolvido ou ressarcido = Reembolso. Rendimento, dividendos e juros = Investimentos.
- "quando": a expressão de tempo como foi dita ou "".

Se tipo = "evento":
- "titulo": o compromisso em poucas palavras, sem data nem hora (ex.: "reunião", "revisão do carro").
- "data_texto": a expressão de data EXATAMENTE como foi dita ("quinta", "dia 15 desse mês",
  "mês que vem dia 10", "amanhã", "quinta da semana que vem", "15/10") ou "" se não houver.
- "hora_texto": a hora de início como foi dita ("9", "9h30", "3 da tarde", "meio-dia") ou "" se não houver.
- "hora_fim_texto": a hora de término, se dita; senão "".
- "duracao_minutos": a duração, se dita ("por 2 horas" = 120); senão null.
- "local": o lugar, se dito; senão "".
- "repeticao_texto": se o compromisso se repete, a expressão EXATAMENTE como foi dita ("toda terça e
  quinta", "todo dia 10", "todos os dias", "de segunda a sexta", "toda semana", "a cada 15 dias");
  senão "". Quando houver repetição, "data_texto" só recebe a data de início se ela for dita
  ("a partir de novembro", "começando dia 3"); os dias da semana da repetição NÃO vão em "data_texto".

Se tipo = "conta":
- "nome": a conta em poucas palavras, sem valor nem data (ex.: "aluguel", "luz", "internet", "faculdade").
- "valor": número em reais, se dito; senão null (contas de valor variável, como luz).
- "dia_vencimento": o dia do mês do vencimento, como número (1 a 31).
- "categoria": exatamente uma das categorias de gasto acima (aluguel, condomínio, luz e água = Moradia;
  faculdade e curso = Educação; internet, celular, streaming e academia = Assinaturas).

Se tipo = "tarefa":
- "texto": o que precisa ser feito, curto, começando por verbo ("comprar ração", "ligar pro contador").

NUNCA calcule datas nem converta horas: copie as expressões como o usuário escreveu.
"confianca": de 0 a 1."""


def interpretar(texto: str, ia) -> dict:
    dados = ia.json(INSTRUCOES, texto)
    if dados.get("eh_gasto") is True and "tipo" not in dados:  # formato antigo
        dados["tipo"] = "gasto"
    return dados


INSTRUCOES_FOTO = f"""Você analisa uma foto enviada ao Venire, assessor pessoal de um brasileiro. Responda APENAS um JSON.

"tipo":
- "gasto": nota fiscal, cupom, recibo ou comprovante de um pagamento FEITO (Pix enviado, cartão, boleto pago).
- "receita": comprovante de dinheiro RECEBIDO (Pix recebido, transferência recebida, crédito de salário).
  Num comprovante de Pix, se a legenda ou o texto indicar "recebido"/"você recebeu", é receita; se indicar
  "enviado"/"você pagou", é gasto. Na dúvida, use a legenda; sem legenda, considere gasto.
- "eventos": a foto mostra compromissos (convite, cartaz de evento, agenda escrita, print de conversa marcando algo).
- "outro": qualquer outra coisa.

Se tipo = "gasto":
- "valor": o VALOR TOTAL PAGO (depois de descontos), como número. Não use subtotais nem impostos.
- "descricao": resumo curto do que foi comprado (ex.: "compras de mercado", "combustível").
- "estabelecimento": nome do lugar (nome fantasia, se houver).
- "categoria": exatamente uma de: {", ".join(CATEGORIAS)}.
- "forma_pagamento": exatamente uma de: {", ".join(PAGAMENTOS)}.
- "quando": a data impressa no documento no formato DD/MM/AAAA, ou "" se não houver.

Se tipo = "receita":
- "valor", "descricao", "pagador" (quem enviou o dinheiro), "quando" (DD/MM/AAAA impresso) e
  "categoria_receita": exatamente uma de: {", ".join(CATEGORIAS_RECEITA)}.

Se tipo = "eventos":
- "eventos": lista com no máximo 5 compromissos, cada um com "titulo", "data_texto" (como aparece:
  "15/10", "sexta", "20 de novembro"), "hora_texto", "hora_fim_texto" e "local". Use "" quando faltar algo.

Use a legenda, se houver, como contexto. NUNCA invente valores, datas ou horários que não estejam visíveis."""


def interpretar_foto(imagem: bytes, tipo: str, legenda: str, ia) -> dict:
    return ia.json_imagem(INSTRUCOES_FOTO, imagem, tipo, legenda)
