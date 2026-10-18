# Venire

Assessor pessoal no Telegram: agenda no Google Agenda, gastos e receitas com saldo do mês, e resumo diário com clima e dólar. Feito em Python, com custo zero.

Plano completo: [docs/PLANO.md](docs/PLANO.md)

## Andamento

- [x] Fase 0 — Contas e repositório
- [x] Fase 1 — Bot respondendo no Telegram, rodando na Vercel
- [x] Fase 2 — Gastos por texto (Neon + Google Planilha)
- [x] Fase 3 — Agenda no Google Agenda, dias da semana e conflitos
- [x] Fase 4 — Áudio e foto
- [ ] Fase 5 — Corrigir, cancelar e perguntar
- [x] Fase 6 — Aviso 1 hora antes, resumo das 6h30, da noite e semanal
- [x] Fase 7 — Receitas e saldo do mês (com projeção do fechamento)
- [x] Fase 8 — Rotina e contas: recorrentes, contas fixas e tarefas
- [ ] Fase 9 — Orçamentos e alertas
- [ ] Fase 10 — Painel web e integração com o clari-analytics

## Estrutura

```
venire/
├── api/index.py              # rotas: /, /webhook, /telegram/configurar, /telegram/diagnostico
├── venire/
│   ├── config.py             # lê as variáveis de ambiente
│   ├── datas.py              # "ontem", "sexta", "dia 10" → data (a IA extrai, o código calcula)
│   ├── banco.py              # conexão com o Neon
│   ├── canais/telegram.py    # fala com a API do Telegram
│   ├── ia/groq.py            # chamada à IA, sempre devolvendo JSON
│   ├── pendencias.py         # pedidos que esperam um clique (conflito, escolher o dia)
│   ├── repeticao.py          # "toda terça e quinta", "todo dia 10" → regra do Google Agenda
│   ├── nucleo/
│   │   ├── interpretador.py  # uma chamada à IA: gasto, evento ou outro + campos extraídos
│   │   └── respostas.py      # decide o que fazer com cada mensagem (não depende do Telegram)
│   └── servicos/
│       ├── google.py         # login do robô no Google e erros explicados
│       ├── gastos.py         # interpretar, salvar, desfazer e resumir gastos
│       ├── receitas.py       # interpretar, salvar e desfazer receitas (tabela incomes)
│       ├── financas.py       # saldo do mês, projeção do fechamento e fechamento mensal
│       ├── contas.py         # contas fixas, vencimentos e "já paguei"
│       ├── tarefas.py        # tarefas sem horário
│       ├── planilha.py       # cópia na Google Planilha (aba de gastos + aba Receitas)
│       ├── eventos.py        # monta o compromisso e formata as respostas
│       └── calendario.py     # Google Agenda: criar, apagar, listar e conflitos
├── tests/                  # python -m pytest
├── vercel.json
└── requirements.txt
```

## Deploy da fase 1 (Vercel)

1. Na Vercel: **Add New → Project → Import** o repositório `venire`. Em *Framework Preset* deixe **Other** e clique em **Deploy**.
2. Em **Settings → Environment Variables**, cadastre:

   | Nome | Valor |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | token do @BotFather |
   | `VENIRE_SECRET` | uma senha longa, só com letras, números, `-` e `_` |

3. Em **Deployments**, clique em **⋯ → Redeploy** no último deploy, para ele ler as variáveis.
4. Abra no navegador, trocando pelos seus valores:
   `https://SEU-PROJETO.vercel.app/telegram/configurar?chave=SEU_VENIRE_SECRET`
   Deve aparecer `"ok": true` com o nome do seu bot.
5. Mande qualquer mensagem para o bot. Ele responde com o **seu ID do Telegram**.
6. Cadastre esse número como `TELEGRAM_CHAT_ID` na Vercel e faça **Redeploy** de novo.
7. Mande "oi". O Venire responde. 🎉

> 🔒 Só o seu chat (`TELEGRAM_CHAT_ID`) é atendido; mensagens de outras pessoas são ignoradas. Cada mensagem recebida também precisa trazer a senha do webhook, que é derivada do `VENIRE_SECRET`.

## Variáveis da fase 2

| Nome | De onde vem |
|---|---|
| `DATABASE_URL` | Criada pela Vercel ao conectar o banco Neon (prefixo `DATABASE`) |
| `GROQ_API_KEY` | console.groq.com → API Keys |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Conteúdo inteiro do arquivo JSON da conta de serviço |
| `GOOGLE_SHEET_ID` | Trecho entre `/d/` e `/edit` no endereço da planilha |
| `GROQ_MODEL` (opcional) | Troca o modelo da IA sem mexer no código (padrão `qwen/qwen3.8-27b`) |

Os gastos ficam na tabela `expenses`, com as mesmas colunas usadas pelo clari-analytics.

## Variável da fase 3

| Nome | Valor |
|---|---|
| `GOOGLE_CALENDAR_ID` | Seu Gmail (ID da agenda principal), compartilhada com o robô com “Fazer alterações nos eventos” |

Diagnóstico: `/agenda/diagnostico?chave=SUA_SENHA`.

Regras de data (a IA só copia a expressão; o Python calcula): “quinta” = próxima quinta; “quinta da semana que vem” = quinta da semana seguinte (domingo a sábado); se hoje já é o dia citado, o Venire pergunta com botões; sem horário = evento de dia inteiro; sem horário final = 1 hora; de 1 a 6 sem “manhã” = tarde.

## Áudio e foto (fase 4)

- **Áudio:** transcrito pelo Whisper (`whisper-large-v3-turbo`, no Groq) e tratado exatamente como uma mensagem escrita. A resposta mostra o que foi entendido.
- **Foto:** a IA de visão classifica a imagem. Nota fiscal ou comprovante vira gasto (valor total, loja, data impressa); convite, cartaz ou agenda viram até 5 compromissos. Se não reconhecer nada, usa a legenda como mensagem.
- Não há variável nova: usa a mesma `GROQ_API_KEY`. Limites: 20 MB para baixar do Telegram e 4 MB para imagens enviadas à IA (o Venire escolhe o maior tamanho de foto que cabe).

## Avisos e resumos (fase 6)

| O quê | Quando | Rota |
|---|---|---|
| Aviso de compromisso | 1 hora antes (checagem a cada 5 min) | `/cron/lembretes` |
| Resumo da manhã: clima, dólar (PTAX), agenda, chuva × compromissos, gastos do mês | 6h30 | `/cron/manha` |
| Resumo de gastos da semana passada | Segundas, junto com o da manhã | `/cron/manha` |
| Previsão e compromissos de amanhã | 21h | `/cron/noite` |

Comandos: `/hoje` (resumo da manhã a qualquer hora) e `/amanha`.

### Configurar no cron-job.org

Crie 3 tarefas em **Create cronjob**, todas com **Time zone: America/Sao_Paulo**:

| Título | URL | Agendamento |
|---|---|---|
| Venire – lembretes | `https://SEU-PROJETO.vercel.app/cron/lembretes?chave=SUA_SENHA` | Every 5 minutes |
| Venire – manhã | `https://SEU-PROJETO.vercel.app/cron/manha?chave=SUA_SENHA` | Every day at 06:30 |
| Venire – noite | `https://SEU-PROJETO.vercel.app/cron/noite?chave=SUA_SENHA` | Every day at 21:00 |

Cada envio é registrado na tabela `envios`, então o mesmo aviso nunca chega duas vezes. Se o Telegram falhar, a próxima chamada tenta de novo.

Variáveis opcionais: `CIDADE`, `LATITUDE` e `LONGITUDE` (padrão: Bento Gonçalves). Clima pelo [Open-Meteo](https://open-meteo.com) e dólar pela API de dados abertos do Banco Central, ambos gratuitos e sem chave.

## Receitas e saldo (fase 7)

- Mande “ganhei 2000 reais”, “recebi 50 do João”, “comissão de 800” ou a foto de um Pix recebido. A receita vai para a tabela `incomes` e para a aba **Receitas** da planilha (criada sozinha). Tem botão ↩️ Desfazer.
- `/saldo`: quanto entrou, quanto saiu, o saldo e quanto da renda já foi gasto. A partir do dia 3, mostra a **projeção do fechamento**:
  - gastos fixos (Moradia, Educação, Assinaturas): o maior valor entre este mês e o anterior;
  - gastos variáveis: o ritmo diário × dias do mês;
  - receitas: o maior valor entre este mês e o anterior (o salário pode ainda não ter caído).
- O resumo das 6h30 traz o mesmo bloco de saldo.
- No dia 1, chega o **fechamento do mês anterior**: quanto sobrou ou faltou, a comparação com o mês anterior e, se faltou, as categorias que mais pesaram.
- Depois do deploy, abra de novo `/telegram/configurar?chave=SUA_SENHA` para o `/saldo` aparecer no menu.

## Rotina e contas (fase 8)

- **Compromissos que se repetem:** “toda terça e quinta Muay Thai às 19h”, “inglês todo dia 10 às 20h”, “academia de segunda a sexta às 7h”, “reunião a cada 2 semanas na quarta”. Vira uma série no Google Agenda; o ↩️ Desfazer apaga a série inteira, e o aviso de 1 hora antes vale para cada ocorrência.
- **Contas fixas:** “aluguel 1450 todo dia 5”, “luz vence dia 10” (sem valor = valor variável). O resumo das 6h30 mostra as contas que vencem nos próximos 3 dias e as atrasadas; no dia do vencimento chega uma mensagem com o botão **✅ Já paguei**, que registra o gasto. Mandar “paguei o aluguel 1450” também marca a conta como paga. `/contas` mostra o mês, com botões.
- **Tarefas:** “lembrar de comprar ração”, “anota: ligar pro contador”. Aparecem no resumo da manhã até você tocar nelas em `/tarefas`.
- Depois do deploy, abra de novo `/telegram/configurar?chave=SUA_SENHA` para /contas e /tarefas entrarem no menu.

## Rodar no seu computador

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (Mac/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
python -m pytest                  # roda os testes (os de banco precisam de TEST_DATABASE_URL)
uvicorn api.index:app --reload    # abre em http://localhost:8000
```

Para testar com dados reais localmente, copie `.env.example` para `.env` e preencha.
