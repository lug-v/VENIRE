# Venire

Assessor pessoal no Telegram: agenda no Google Agenda, registro e análise de gastos, e resumo diário com clima e dólar. Feito em Python, com custo zero.

Plano completo: [docs/PLANO.md](docs/PLANO.md)

## Andamento

- [x] Fase 0 — Contas e repositório
- [x] Fase 1 — Bot respondendo no Telegram, rodando na Vercel
- [x] Fase 2 — Gastos por texto (Neon + Google Planilha)
- [x] Fase 3 — Agenda no Google Agenda, dias da semana e conflitos
- [ ] Fase 4 — Áudio e foto
- [ ] Fase 5 — Corrigir, cancelar e perguntar
- [ ] Fase 6 — Aviso 1 hora antes, resumo das 6h30 e resumo semanal
- [ ] Fase 7 — Orçamentos, alertas e mais testes
- [ ] Fase 8 — Integração com o clari-analytics

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
│   ├── nucleo/
│   │   ├── interpretador.py  # uma chamada à IA: gasto, evento ou outro + campos extraídos
│   │   └── respostas.py      # decide o que fazer com cada mensagem (não depende do Telegram)
│   └── servicos/
│       ├── google.py         # login do robô no Google e erros explicados
│       ├── gastos.py         # interpretar, salvar, desfazer e resumir gastos
│       ├── planilha.py       # cópia dos gastos na Google Planilha
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

## Rodar no seu computador

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (Mac/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
python -m pytest                  # roda os testes (os de banco precisam de TEST_DATABASE_URL)
uvicorn api.index:app --reload    # abre em http://localhost:8000
```

Para testar com dados reais localmente, copie `.env.example` para `.env` e preencha.
