# Venire

Assessor pessoal no Telegram: agenda no Google Agenda, registro e análise de gastos, e resumo diário com clima e dólar. Feito em Python, com custo zero.

Plano completo: [docs/PLANO.md](docs/PLANO.md)

## Andamento

- [x] Fase 0 — Contas e repositório
- [x] Fase 1 — Bot respondendo no Telegram, rodando na Vercel
- [ ] Fase 2 — Gastos por texto (Neon + Google Planilha)
- [ ] Fase 3 — Agenda no Google Agenda, dias da semana e conflitos
- [ ] Fase 4 — Áudio e foto
- [ ] Fase 5 — Corrigir, cancelar e perguntar
- [ ] Fase 6 — Aviso 1 hora antes, resumo das 6h30 e resumo semanal
- [ ] Fase 7 — Orçamentos, alertas e mais testes
- [ ] Fase 8 — Integração com o clari-analytics

## Estrutura

```
venire/
├── api/index.py            # rotas: /, /webhook, /telegram/configurar
├── venire/
│   ├── config.py           # lê as variáveis de ambiente
│   ├── canais/telegram.py  # fala com a API do Telegram
│   └── nucleo/respostas.py # decide o que responder (não depende do Telegram)
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

## Rodar no seu computador

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (Mac/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
python -m pytest                  # roda os testes
uvicorn api.index:app --reload    # abre em http://localhost:8000
```

Para testar com dados reais localmente, copie `.env.example` para `.env` e preencha.
