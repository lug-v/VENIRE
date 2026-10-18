# Venire — Plano técnico

Assessor pessoal no Telegram para agenda e gastos, com resumo diário de clima e dólar. Uso individual, custo zero.

## 1. Requisitos (a partir do README do Lucas)

### Essencial (MVP)
- **Agendar compromissos** em linguagem natural, por texto ou áudio.
  - "Agende dia 15 desse mês às 9 horas reunião": sem horário final, dura **1 hora**.
  - "Agendar mês que vem dia 10 revisão carro": sem horário, vira **evento de dia inteiro**.
  - "Marcar reunião às 9 na quinta": entende **dias da semana** (regras na seção 3.1).
- **Aviso 1 hora antes** de cada compromisso, no Telegram.
- **Avisar conflitos** de horário antes de agendar.
- **Registrar gastos** por texto, áudio ou foto da nota.
  - "gasto 15 reais energético" e "GASTEI 250 GASOLINA" devem funcionar, sem depender de maiúsculas, acentos ou ordem das palavras.
- **Resumos de gastos** semanal e mensal, por categoria.
- **Planilha do Google** com todos os gastos, atualizada a cada registro, para consultar em tabela.
- **Corrigir e cancelar conversando**: "na verdade foram 25", "desmarca a revisão do carro", "apaga o último gasto".
- **Perguntas simples**: "o que tenho amanhã?", "quanto gastei com gasolina este mês?".

### Importante
- **Resumo da manhã às 6h30**: previsão do tempo em Bento Gonçalves, dólar (PTAX), compromissos do dia e gastos do mês.
- **Orçamento por categoria** com alerta ao chegar em 80%.
- **Alerta de gasto fora do padrão** (mesma regra IQR do clari-analytics).

### Sonho
- Funcionar como assessor completo: cruzar clima com agenda ("vai chover no horário do treino"), sugerir horários livres, metas de economia.
- Aplicativo para iOS e Android.

### Regras e limites
- Custo zero, uso individual.
- Canal principal: Telegram.
- Horários fixos: resumo da manhã às **6h30**; aviso de compromisso **1 hora antes**.
- **Salva direto, com botão "Desfazer"**, quando a IA tem certeza. **Pergunta antes** quando falta informação ou há ambiguidade. É mais rápido que confirmar tudo, como a Clari faz hoje.

## 2. Decisões de arquitetura

| Decisão | Escolha | Por quê |
|---|---|---|
| Linguagem | **Python 3.12** | Mesma do MBA e do clari-analytics; foco na carreira em dados |
| Servidor | **FastAPI** na **Vercel** (plano grátis) | Webhook do Telegram sem servidor ligado o tempo todo; deploy automático pelo GitHub |
| Banco | **Neon PostgreSQL** (grátis) | Já conhecido; os gastos ficam prontos para o pandas |
| IA | **Groq** (grátis): LLM com *function calling*, Whisper e visão | Entende a mensagem, transcreve áudio e lê notas |
| Calendário | **Google Agenda** via **conta de serviço** | Grátis, aparece no Android e no iPhone e é instantâneo |
| Tabela de gastos | **Google Planilhas** (mesma conta de serviço) | Grátis; cada gasto vira uma linha que você abre no celular ou no PC |
| Clima | **Open-Meteo** | Grátis e sem chave |
| Dólar | **API PTAX do Banco Central** | Oficial, grátis e sem chave |
| Agendamentos automáticos | **cron-job.org** | Grátis e com precisão de minuto. O cron grátis da Vercel só roda uma vez por dia e pode atrasar até 59 minutos, o que não atende às 6h30 nem ao aviso de 1 hora antes |
| Validação de dados | **Pydantic** | Garante que a resposta da IA tem o formato certo antes de salvar |

### Por que Google Agenda com conta de serviço
Uma conta de serviço é um "robô" do Google. Você compartilha a sua agenda com o e-mail dele, dando permissão para "fazer alterações nos eventos". A partir daí, o Venire cria eventos direto na sua agenda. Não precisa de tela de login nem de tokens que expiram. A configuração leva uns 15 minutos, uma vez só.

A mesma conta de serviço escreve na **Planilha do Google**: você cria uma planilha "Venire – Gastos" e a compartilha com o e-mail do robô como editor.

Alternativa mais simples, mas inferior: um link `.ics` assinado no celular. Ele é só leitura e o Google demora horas para atualizar.

### Lembretes de 1 hora antes
O Venire manda o aviso pelo **Telegram**. O cron-job.org chama a rota `/cron/lembretes` a cada 5 minutos, e ela envia os avisos dos compromissos que começam entre 55 e 60 minutos depois. Cada aviso é marcado como enviado, para nunca chegar duplicado. Como reforço opcional, você pode definir no app do Google Agenda uma notificação padrão de 1 hora para essa agenda.

## 3. Arquitetura

```
Telegram ──► /webhook (FastAPI)
                │
                ▼
         Entrada: texto | áudio → Whisper | foto → visão
                │
                ▼
         Roteador de intenções (LLM com function calling)
          ├─ criar_evento      ├─ registrar_gasto
          ├─ consultar_agenda  ├─ consultar_gastos
          ├─ alterar / cancelar (usa o contexto da conversa)
          └─ conversa / ajuda
                │
                ▼
         Serviços:  calendario.py (Google)   gastos.py (Neon)
                    clima.py (Open-Meteo)    cambio.py (BCB)
                │
                ▼
         Resposta no Telegram (com botão Desfazer)

cron-job.org ──► /cron/manha      (6h30: clima + dólar + agenda + gastos)
             ├─► /cron/lembretes  (a cada 5 min: avisos 1 hora antes)
             └─► /cron/semana     (segunda 6h30: resumo semanal de gastos)
Cada registro de gasto ──► também vira uma linha na Planilha do Google
```

**A grande diferença para a Clari é o roteador.** A Clari usa regras fixas, como "se tem 'gastei', é gasto", e por isso é pouco flexível. No Venire, a IA recebe a lista de **ferramentas** disponíveis e escolhe qual usar, já preenchendo os dados. Assim:
- "GASTEI 250 GASOLINA" → `registrar_gasto(valor=250, categoria="Transporte", descricao="gasolina")`
- "na verdade foram 230" → `alterar_ultimo(valor=230)`
- "estou livre quinta à tarde?" → `consultar_agenda(inicio=..., fim=...)`

O código Python **valida** tudo com Pydantic antes de executar. A IA sugere, e o código decide.

### 3.1 Datas: a IA extrai, o código calcula
Modelos de linguagem erram contas de calendário com frequência ("que dia é a próxima quinta?"). Por isso, no Venire a IA só **extrai** o que foi dito (por exemplo, `dia_semana="quinta"` e `hora="09:00"`), e uma função Python **calcula a data** com o fuso America/Sao_Paulo. Essa função é testada com pytest.

Regras:
| Expressão | Interpretação |
|---|---|
| "na quinta", "quinta", "quinta que vem" | Próxima quinta-feira a partir de hoje |
| "quinta da semana que vem", "quinta da próxima semana" | Quinta-feira da semana seguinte (semana de domingo a sábado) |
| "hoje", "amanhã", "depois de amanhã" | Relativo à data atual |
| "dia 15", "dia 15 desse mês" | Dia 15 do mês atual; se já passou, pergunta se é o do mês que vem |
| "mês que vem dia 10" | Dia 10 do próximo mês |
| Hoje é quinta e a mensagem diz "na quinta" | Se o horário ainda não passou, pergunta com botões: "Hoje" ou "Próxima quinta" |
| "às 9" sem manhã ou noite | 09:00 (entre 1 e 6 sem indicação, assume a tarde: "às 3" = 15:00) |

**Preparado para virar app:** o núcleo (roteador e serviços) não sabe que o Telegram existe. Um app de celular no futuro só precisa de outra "porta de entrada".

## 4. Estrutura do repositório

```
venire/
├── api/index.py              # FastAPI: /webhook, /cron/manha, /cron/semana
├── venire/
│   ├── canais/telegram.py    # receber e enviar mensagens
│   ├── nucleo/
│   │   ├── roteador.py       # LLM + ferramentas
│   │   ├── ferramentas.py    # definição das ferramentas (schemas Pydantic)
│   │   └── contexto.py       # última ação, para "corrigir" e "desfazer"
│   ├── servicos/
│   │   ├── calendario.py     # Google Agenda
│   │   ├── gastos.py         # registro, resumos, orçamento
│   │   ├── planilha.py       # Google Planilhas
│   │   ├── lembretes.py      # avisos de 1 hora antes
│   │   ├── clima.py
│   │   └── cambio.py
│   ├── datas.py              # dias da semana e datas relativas
│   ├── ia/groq.py            # LLM, Whisper, visão
│   ├── banco.py              # conexão Neon
│   └── config.py             # variáveis de ambiente
├── tests/                    # pytest: seus exemplos reais viram testes
├── docs/
├── requirements.txt
└── vercel.json
```

## 5. Banco de dados

- `gastos`: mesmas colunas da tabela `expenses` da Clari, para o clari-analytics funcionar sem mudanças.
- `eventos`: espelho dos compromissos (id do Google, título, início, fim). Serve para consultas rápidas e para o histórico.
- `acoes`: registro de cada ação do Venire (criou, alterou, apagou). É a base do "desfazer" e do "corrigir".
- `orcamentos`: limite mensal por categoria.
- `lembretes_enviados`: evita aviso duplicado de 1 hora antes.

## 6. Fases de construção

| Fase | Entrega | O que você aprende |
|---|---|---|
| 0 | Contas criadas (BotFather, Groq, Neon, Google Cloud, Vercel), repositório e ambiente local | Ambiente virtual, Git, variáveis de ambiente |
| 1 | Bot que responde "olá" no Telegram, rodando na Vercel | HTTP, APIs, webhooks, FastAPI, deploy |
| 2 | Gastos por texto, salvos no Neon **e na Planilha do Google**, e comando de resumo | LLM com saída estruturada, Pydantic, SQL, Google Sheets API |
| 3 | Agenda no Google Agenda, **dias da semana** e aviso de conflitos | APIs do Google, datas e fusos horários, testes com pytest |
| 4 | Áudio e foto da nota | Whisper e modelos de visão |
| 5 | Roteador completo: corrigir, cancelar, perguntar | Function calling e contexto de conversa |
| 6 | **Aviso 1 hora antes**, resumo das 6h30 e resumo semanal | Agendadores (cron-job.org), idempotência, APIs públicas (Open-Meteo, BCB) |
| 7 | Orçamentos, alertas de anomalia e testes com pytest | Regras de negócio, testes automatizados |
| 8 | Ligar o clari-analytics aos dados do Venire | Integração entre sistemas |

Cada fase termina com algo **funcionando no seu Telegram**. Isso mantém a motivação e permite usar o Venire desde a fase 2.

## 7. Casos de teste (exemplos reais)

Considerando hoje = terça, 13/10/2026:

| Mensagem | Resultado esperado |
|---|---|
| "marcar reunião às 9 na quinta" | Evento "Reunião", quinta 15/10, 09:00–10:00 |
| "dentista sexta da semana que vem às 14h" | Evento "Dentista", sexta 23/10, 14:00–15:00 |
| "almoço com cliente amanhã meio-dia" | Evento quarta 14/10, 12:00–13:00 |
| "Agende dia 15 desse mês às 9 horas reunião" | Evento "Reunião", dia 15, 09:00–10:00 |
| "Agendar mês que vem dia 10 revisão carro" | Evento de dia inteiro "Revisão do carro", dia 10 do próximo mês |
| "gasto 15 reais energético" | Gasto de R$ 15,00, Alimentação, "energético" |
| "GASTEI 250 GASOLINA" | Gasto de R$ 250,00, Transporte, "gasolina" |
| "na verdade foram 230" (logo depois) | Último gasto alterado para R$ 230,00 |
| "desmarca a revisão do carro" | Evento removido do Google Agenda |
| "passa a reunião de quinta para as 10h" | Evento remarcado para 10:00–11:00 |
| (59 min antes da reunião de quinta) | Aviso no Telegram: "⏰ Em 1 hora: Reunião (09:00)" |

## 8. Pontos em aberto
- Migrar ou não o histórico da Clari para o Venire.
- Categorias definitivas de gastos (proposta inicial: as mesmas da Clari).
- Antecedência extra para eventos de dia inteiro (proposta: avisar no resumo das 6h30 do próprio dia).

## 9. Andamento
- **Fase 0 — concluída (14/10/2026):** contas criadas (BotFather @Venyre_bot, Groq, Neon pela Vercel, Google Cloud com conta de serviço, cron-job.org), agenda principal e planilha "Venire – Gastos" compartilhadas com o robô.
- **Fase 1 — concluída (14/10/2026):** bot respondendo no Telegram, rodando na Vercel em `venire-kappa.vercel.app`, repositório `lug-v/VENIRE`. Variáveis na Vercel: `TELEGRAM_BOT_TOKEN`, `VENIRE_SECRET`, `TELEGRAM_CHAT_ID`.
  - Rotas úteis: `/telegram/configurar?chave=...` (registra o webhook) e `/telegram/diagnostico?chave=...` (mostra erros do webhook).
  - Lição: o webhook deve usar sempre o endereço oficial de produção; endereços de deploy específico ficam protegidos por login da Vercel.
- **Fase 2 — concluída (14/10/2026):** gastos por texto salvos no Neon (tabela `expenses`, compatível com o clari-analytics) e copiados para a Google Planilha; botão Desfazer; `/gastos` e `/semana`.
  - Variáveis novas: `DATABASE_URL` (conexão Neon com prefixo `DATABASE`, sem branch por deploy), `GROQ_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID` (aceita o link inteiro da planilha).
  - Rota útil: `/planilha/diagnostico?chave=...`.
  - Lições: ao colar o JSON do robô, a última `}` costuma ficar de fora (o código agora completa); trocar o `VENIRE_SECRET` exige abrir de novo o link de configurar.
- **Fase 3 — concluída (18/10/2026):** compromissos direto no Google Agenda, com dias da semana, "semana que vem", "mês que vem", dia inteiro quando não há horário e 1 hora quando não há fim; pergunta com botões quando hoje é o dia citado; aviso de conflito com "Agendar mesmo assim"; Desfazer; `/agenda` (hoje e amanhã).
  - Variável nova: `GOOGLE_CALENDAR_ID` (o Gmail). Rota útil: `/agenda/diagnostico?chave=...`.
  - Decisões: uma única chamada à IA classifica (gasto, evento ou outro) e só copia as expressões de data e hora; o Python calcula. O Google Agenda é a fonte oficial (sem tabela espelho); pedidos que esperam clique ficam na tabela `pendencias`.
  - Lição: depois de cadastrar uma variável, só o Redeploy faz a versão no ar enxergá-la.
- **Fase 4 — concluída (18/10/2026):** áudio transcrito pelo Whisper (`whisper-large-v3-turbo`) segue o caminho do texto, mostrando o que foi entendido; foto lida pela IA de visão (`qwen/qwen3.8-27b`): nota fiscal ou comprovante vira gasto (valor total, loja, data impressa), convite ou agenda viram até 5 compromissos, e sem reconhecimento a legenda vale como mensagem. Sem variável nova.
  - Limites: 20 MB para baixar do Telegram, 4 MB para imagem enviada à IA; função da Vercel com até 60 s.
- **Próxima:** fase 5 — corrigir, cancelar e perguntar ("na verdade foram 25", "desmarca a revisão do carro", "quanto gastei com gasolina?").
