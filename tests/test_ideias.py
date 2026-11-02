"""Testes da fase 10: banco de ideias, filtro, notas do Obsidian e posts do LinkedIn.

Referência: segunda-feira, 02/11/2026. Dias de post: terça e quinta.

Os fluxos rodam com um "banco em memória" (as funções de banco do módulo ideias são
trocadas por versões em memória), então não precisam de PostgreSQL. O SQL de verdade
é testado no fim, quando TEST_DATABASE_URL estiver configurada.
"""

import base64
from contextlib import contextmanager
import copy
from datetime import date, timedelta
import json
import os

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from api import index
from tests.banco_teste import recriar_tabelas
from venire import envios, pendencias
from venire.ia.claude import Claude, extrair_json
from venire.ia.groq import ErroIA
from venire.nucleo import automacoes, conteudo
from venire.nucleo.interpretador import prefixo_de_ideia
from venire.nucleo.respostas import Dependencias, processar_botao, processar_foto, processar_texto
from venire.servicos import ideias, tarefas
from venire.servicos.ideias import Ideia
from venire.servicos.notas import ErroNotas, RepositorioNotas

HOJE = date(2026, 11, 2)       # segunda
TERCA, QUINTA = date(2026, 11, 3), date(2026, 11, 5)
URL_TESTE = os.getenv("TEST_DATABASE_URL", "")


# ---------------------------------------------------------------- Sem banco

@pytest.mark.parametrize("texto, tipo, conteudo_esperado", [
    ("ideia: post sobre IA no comercial B2B", "ideia", "post sobre IA no comercial B2B"),
    ("Conceito: overfitting", "conceito", "overfitting"),
    ("nota: hoje entendi RAG", "anotacao", "hoje entendi RAG"),
    ("Anotação - aula de estatística", "anotacao", "aula de estatística"),
])
def test_prefixo(texto, tipo, conteudo_esperado):
    assert prefixo_de_ideia(texto) == {"tipo": tipo, "texto": conteudo_esperado, "data_post_texto": ""}


@pytest.mark.parametrize("texto", ["anota: ligar pro contador", "ideia para postar dia 20: algo",
                                   "gastei 40 no mercado", "ideia:", "minha ideia: x"])
def test_sem_prefixo(texto):
    assert prefixo_de_ideia(texto) is None


def test_proxima_data_livre():
    assert ideias.proxima_data_livre(set(), HOJE) == TERCA
    assert ideias.proxima_data_livre({TERCA}, HOJE) == QUINTA
    assert ideias.proxima_data_livre(set(), TERCA) == QUINTA                       # nunca o próprio dia
    assert ideias.proxima_data_livre(set(), HOJE, a_partir=date(2026, 11, 10)) == date(2026, 11, 10)
    assert ideias.datas_livres({QUINTA}, HOJE, 3) == [TERCA, date(2026, 11, 10), date(2026, 11, 12)]


def test_distribuir_maior_potencial_primeiro_e_respeita_data_pedida():
    a = Ideia(id=1, potencial=5)
    b = Ideia(id=2, potencial=9)
    c = Ideia(id=3, potencial=7, data_prevista=date(2026, 11, 20))   # pediu sexta, dia 20
    ideias.distribuir([a, b, c], {TERCA}, HOJE)
    assert (b.data_prevista, a.data_prevista, c.data_prevista) == (QUINTA, date(2026, 11, 10), date(2026, 11, 20))
    assert {a.status, b.status, c.status} == {"agendada"}


def test_nota_markdown():
    ideia = Ideia(id=7, tipo="conceito", texto_original="overfitting\nvi na aula", criado_em=HOJE,
                  status="agendada", titulo="Overfitting: quando o modelo decora", tema="IA e Dados",
                  explicacao="O modelo aprende o ruído.", por_que_importa="Previsões ruins.",
                  exemplo="Previsão de vendas.", potencial=8, angulo="Vendedor que decora o script",
                  data_prevista=QUINTA)
    nota = ideias.nota_markdown(ideia)
    assert nota.startswith("---\nid: 7\ntitulo: \"Overfitting: quando o modelo decora\"\ntipo: conceito\n")
    assert "post_previsto: 2026-11-05\n" in nota and "tags: [venire, conceito, ia-e-dados]" in nota
    assert "## Explicação breve\nO modelo aprende o ruído." in nota
    assert "## Texto original\n> overfitting\n> vi na aula" in nota
    assert "Ângulo sugerido: Vendedor que decora o script" in nota
    assert ideias.caminho_da_nota(ideia) == "IA e Dados/Overfitting quando o modelo decora (7).md"


def test_decidir():
    novas = [Ideia(id=1, texto_original="overfitting"), Ideia(id=2, texto_original="overfitting de novo"),
             Ideia(id=3, texto_original="comprar pão"), Ideia(id=4, texto_original="sem resposta"),
             Ideia(id=5, texto_original="juntar com fantasma")]
    resultado = conteudo.decidir(novas, [
        {"id": 1, "decisao": "aprovada", "titulo": "Overfitting", "tema": "Inventado", "potencial": 15},
        {"id": "2", "decisao": "juntar", "juntar_com": 1, "motivo": "mesmo assunto"},
        {"id": 3, "decisao": "descartada", "motivo": "lembrete pessoal"},
        {"id": 5, "decisao": "juntar", "juntar_com": 99, "motivo": "parecida"},
    ], ids_existentes=set())
    assert [i.id for i in resultado.aprovadas] == [1]
    assert (novas[0].tema, novas[0].potencial) == ("Outros", 10)        # tema inválido e nota limitada
    assert resultado.juntadas == [(novas[1], 1)] and novas[1].motivo == "juntada à #1"
    assert [i.id for i in resultado.descartadas] == [3, 5]             # juntar com id que não existe
    assert resultado.sem_resposta == 1 and novas[3].status == "nova"


def test_manter_forca_a_aprovacao():
    novas = [Ideia(id=8, texto_original="funil de vendas com IA")]
    resultado = conteudo.decidir(novas, [{"id": 8, "decisao": "descartada", "motivo": "vago"}], set(), {8})
    assert resultado.aprovadas == novas and novas[0].titulo == "Funil de vendas com IA"


def test_claude_envia_chave_e_le_json():
    recebido = {}

    def anthropic(request: httpx.Request):
        recebido["cabecalho"] = dict(request.headers)
        recebido["corpo"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [
            {"type": "text", "text": "```json\n{\"ideias\": [{\"id\": 1}]}\n```"}]})

    ia = Claude("sk-ant-x", "claude-sonnet-5-5", cliente=httpx.Client(transport=httpx.MockTransport(anthropic)))
    assert ia.json("instruções", "mensagem") == {"ideias": [{"id": 1}]}
    assert recebido["cabecalho"]["x-api-key"] == "sk-ant-x"
    assert recebido["cabecalho"]["anthropic-version"] == "2023-06-01"
    assert recebido["corpo"]["system"] == "instruções" and recebido["corpo"]["model"] == "claude-sonnet-5-5"
    assert recebido["corpo"]["messages"] == [{"role": "user", "content": "mensagem"}]


def test_claude_erros():
    with pytest.raises(ErroIA):
        Claude("", "modelo")
    ia = Claude("x", "m", cliente=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(529, text="overloaded"))))
    with pytest.raises(ErroIA, match="529"):
        ia.json("a", "b")
    assert extrair_json('Aqui está: {"post": "oi"} pronto') == {"post": "oi"}
    with pytest.raises(ErroIA):
        extrair_json("sem json")


def test_repositorio_cria_e_atualiza_nota():
    chamadas = []

    def github(request: httpx.Request):
        chamadas.append((request.method, request.url.raw_path.decode()))
        if request.method == "GET":
            return httpx.Response(200 if "Existente" in request.url.path else 404, json={"sha": "abc123"})
        corpo = json.loads(request.content)
        chamadas.append(corpo)
        return httpx.Response(201)

    repo = RepositorioNotas("ghp_x", "lug-v/venire-notas",
                            cliente=httpx.Client(transport=httpx.MockTransport(github)))
    repo.gravar("IA e Dados/Nova (1).md", "# Olá", "Venire: nova")
    assert chamadas[0] == ("GET", "/repos/lug-v/venire-notas/contents/IA%20e%20Dados/Nova%20%281%29.md")
    assert base64.b64decode(chamadas[2]["content"]).decode() == "# Olá" and "sha" not in chamadas[2]
    repo.gravar("Outros/Existente (2).md", "# Nova versão", "Venire: atualiza")
    assert chamadas[-1]["sha"] == "abc123"


def test_repositorio_explica_erro_de_permissao():
    repo = RepositorioNotas("ghp_x", "lug-v/venire-notas", cliente=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(404 if r.method == "GET" else 403))))
    with pytest.raises(ErroNotas, match="acesso de escrita"):
        repo.gravar("a.md", "x", "y")


def test_rotas_cron_novas(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("VENIRE_SECRET", "senha")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(index.conteudo, "executar_filtro", lambda deps, enviar: {"enviados": 1, "filtradas": 3})
    monkeypatch.setattr(index.conteudo, "executar_linkedin", lambda deps, enviar: {"enviados": 1, "escritos": 2})
    cliente = TestClient(index.app)
    assert cliente.get("/cron/ideias").status_code == 401
    assert cliente.get("/cron/ideias?chave=senha").json() == {"ok": True, "enviados": 1, "filtradas": 3}
    assert cliente.get("/cron/linkedin?chave=senha").json() == {"ok": True, "enviados": 1, "escritos": 2}


def test_dependencias_da_fase_10(monkeypatch):
    for nome in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "OBSIDIAN_REPO", "CLAUDE_MODEL"):
        monkeypatch.delenv(nome, raising=False)
    deps = index.montar_dependencias(index.carregar_config())
    assert deps.claude is None and deps.notas is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.setenv("OBSIDIAN_REPO", "lug-v/venire-notas")
    config = index.carregar_config()
    deps = index.montar_dependencias(config)
    assert config.claude_modelo == "claude-sonnet-5-5"
    assert isinstance(deps.claude(), Claude) and isinstance(deps.notas(), RepositorioNotas)


# ---------------------------------------------------------------- Banco em memória

class BancoMemoria:
    """Guarda ideias, envios e pendências num dicionário, imitando as funções de banco."""

    def __init__(self):
        self.ideias: dict[int, Ideia] = {}
        self.envios: set[str] = set()
        self.pendencias: dict[str, tuple[str, dict]] = {}
        self.tarefas: list[str] = []

    def instalar(self, monkeypatch):
        m = monkeypatch.setattr
        m(ideias, "salvar", self.salvar)
        m(ideias, "atualizar", lambda c, i: self.ideias.__setitem__(i.id, copy.deepcopy(i)))
        m(ideias, "apagar", lambda c, x: self.ideias.pop(x, None) is not None)
        m(ideias, "buscar", lambda c, x: copy.deepcopy(self.ideias.get(x)))
        m(ideias, "listar", self.listar)
        m(ideias, "contagem", self.contagem)
        m(ideias, "por_fonte", lambda c: {i.fonte: copy.deepcopy(i) for i in self.ideias.values() if i.fonte})
        m(ideias, "datas_ocupadas", lambda c: {i.data_prevista for i in self.ideias.values()
                                               if i.status in ("agendada", "publicada") and i.data_prevista})
        m(ideias, "do_dia", lambda c, d: [copy.deepcopy(i) for i in self.ideias.values()
                                          if i.status == "agendada" and i.data_prevista == d])
        m(ideias, "proximas", lambda c, d, limite=10: sorted(
            (copy.deepcopy(i) for i in self.ideias.values() if i.status == "agendada" and i.data_prevista),
            key=lambda i: (i.data_prevista, i.id))[:limite])
        m(ideias, "pendentes_ate", lambda c, d: sorted(
            (copy.deepcopy(i) for i in self.ideias.values() if i.status == "agendada" and i.data_prevista <= d),
            key=lambda i: (i.data_prevista, i.id)))
        m(envios, "reservar", lambda c, k: k not in self.envios and not self.envios.add(k))
        m(envios, "reservar_trava", lambda c, k, minutos=5: k not in self.envios and not self.envios.add(k))
        m(envios, "liberar", lambda c, k: self.envios.discard(k))
        m(pendencias, "guardar", self.guardar_pendencia)
        m(pendencias, "retirar", lambda c, x: self.pendencias.pop(x, None))
        m(tarefas, "salvar", lambda c, t, d: self.tarefas.append(t) or str(len(self.tarefas)))
        m(tarefas, "abertas", lambda c: [(str(n), t, HOJE) for n, t in enumerate(self.tarefas)])

    def salvar(self, conexao, ideia):
        ideia.id = len(self.ideias) + 1
        self.ideias[ideia.id] = copy.deepcopy(ideia)
        return ideia.id

    def listar(self, conexao, status, limite=50, recentes_primeiro=False):
        lista = [status] if isinstance(status, str) else list(status)
        itens = sorted((i for i in self.ideias.values() if i.status in lista), key=lambda i: i.id,
                       reverse=recentes_primeiro)
        return [copy.deepcopy(i) for i in itens[:limite]]

    def contagem(self, conexao):
        totais = {}
        for i in self.ideias.values():
            totais[i.status] = totais.get(i.status, 0) + 1
        return totais

    def guardar_pendencia(self, conexao, tipo, dados):
        pid = f"p{len(self.pendencias) + 1}"
        self.pendencias[pid] = (tipo, dados)
        return pid


class IAFalsa:
    """Responde o interpretador, o filtro e o escritor de posts."""

    def __init__(self):
        self.interpretacao, self.filtro, self.posts, self.pedidos = {}, [], [], []

    def json(self, instrucoes, mensagem):
        if instrucoes is conteudo.INSTRUCOES_FILTRO:
            self.pedidos.append(json.loads(mensagem))
            return {"ideias": self.filtro}
        if instrucoes is conteudo.INSTRUCOES_POST:
            self.pedidos.append(json.loads(mensagem))
            return {"post": self.posts.pop(0) if self.posts else "Post padrão #ia"}
        return self.interpretacao

    def json_imagem(self, instrucoes, imagem, tipo, legenda):
        return self.interpretacao


class NotasFalsas:
    arquivos: dict = {}
    fontes: dict = {}          # caminho → (sha, conteúdo) dos conceitos copiados do vault
    falhar = False

    def gravar(self, caminho, texto, mensagem):
        if NotasFalsas.falhar:
            raise ErroNotas("o GitHub recusou o GITHUB_TOKEN (vencido ou errado).")
        NotasFalsas.arquivos[caminho] = texto

    def listar(self, pasta):
        return {c: sha for c, (sha, _) in NotasFalsas.fontes.items() if c.startswith(pasta + "/")}

    def ler(self, caminho):
        return NotasFalsas.fontes[caminho][1]


@pytest.fixture
def memoria(monkeypatch):
    banco = BancoMemoria()
    banco.instalar(monkeypatch)
    NotasFalsas.arquivos, NotasFalsas.fontes, NotasFalsas.falhar = {}, {}, False
    ia = IAFalsa()

    @contextmanager
    def conexao():
        yield None

    deps = Dependencias(ia=lambda: ia, banco=conexao, claude=lambda: ia, notas=lambda: NotasFalsas(),
                        hoje=lambda: HOJE)
    deps.ia_falsa, deps.memoria = ia, banco
    return deps


def capturar(deps, mensagem, **dados):
    deps.ia_falsa.interpretacao = dados
    return processar_texto(mensagem, deps)


def aprovar(id_, titulo, potencial=7, tema="IA e Dados"):
    return {"id": id_, "decisao": "aprovada", "titulo": titulo, "tema": tema, "explicacao": "Explicação.",
            "por_que_importa": "Importa.", "exemplo": "Exemplo.", "potencial": potencial, "angulo": "Ângulo."}


def test_captura_por_prefixo_sem_chamar_a_ia(memoria):
    memoria.ia_falsa.interpretacao = {"tipo": "gasto"}   # se a IA fosse chamada, viraria gasto
    resposta = processar_texto("ideia: post sobre IA no comercial B2B", memoria)
    assert resposta.texto == ("💡 Ideia guardada: Post sobre IA no comercial B2B\n"
                              "🧹 1 item esperando o filtro de domingo (ou use /filtrar).")
    assert resposta.botoes == [("↩️ Desfazer", "ix:1")]
    assert memoria.memoria.ideias[1].tipo == "ideia" and memoria.memoria.ideias[1].origem == "texto"
    assert processar_botao("ix:1", memoria).texto == "↩️ Removida do banco de ideias."
    assert processar_botao("ix:1", memoria).texto == "Essa ideia já tinha sido removida."


def test_captura_pela_ia_com_data_do_post(memoria):
    resposta = capturar(memoria, "ideia para postar dia 20: funil com IA", tipo="ideia",
                        texto="funil com IA", data_post_texto="dia 20")
    assert "📣 Post previsto para sexta, 20/11 (se passar no filtro)." in resposta.texto
    capturar(memoria, "conceito overfitting", tipo="conceito", texto="overfitting")
    capturar(memoria, "anotação", tipo="anotação", texto="RAG busca documentos")      # com acento
    assert [i.tipo for i in memoria.memoria.ideias.values()] == ["ideia", "conceito", "anotacao"]
    texto = processar_texto("/ideias", memoria).texto
    assert texto.startswith("💡 Banco de ideias (3)\n🆕 Esperando o filtro: 3")
    assert "🗒️ #3 RAG busca documentos · nova" in texto


def test_tarefa_ou_ideia_pergunta_com_botoes(memoria):
    resposta = capturar(memoria, "escrever sobre funil de vendas", tipo="tarefa_ou_ideia",
                        texto="escrever sobre funil de vendas")
    assert resposta.texto == "🤔 “Escrever sobre funil de vendas” é uma tarefa ou uma ideia?"
    assert [b[0] for b in resposta.botoes] == ["📝 Tarefa", "💡 Ideia"]
    ideia = processar_botao(resposta.botoes[1][1], memoria)
    assert ideia.texto.startswith("💡 Ideia guardada: Escrever sobre funil de vendas")
    assert processar_botao(resposta.botoes[0][1], memoria).texto == "Esse pedido expirou ou já foi respondido."

    outra = capturar(memoria, "ligar pro João sobre o post", tipo="tarefa_ou_ideia", texto="ligar pro João")
    assert processar_botao(outra.botoes[0][1], memoria).texto.startswith("📝 Anotado: Ligar pro João")


def test_foto_de_slide_vira_anotacao(memoria):
    memoria.ia_falsa.interpretacao = {"tipo": "anotacao", "texto": "Slide: Regressão logística — classifica em 0 ou 1"}
    [resposta] = processar_foto(b"jpg", "image/jpeg", "", memoria)
    assert resposta.texto.startswith("📸 🗒️ Anotação guardada: Slide: Regressão logística")
    assert memoria.memoria.ideias[1].origem == "foto"


def test_filtro_completo(memoria):
    capturar(memoria, "a", tipo="conceito", texto="overfitting")
    capturar(memoria, "b", tipo="ideia", texto="overfitting no forecast de vendas")
    capturar(memoria, "c", tipo="ideia", texto="comprar pão")
    capturar(memoria, "d", tipo="ideia", texto="CRM com IA", data_post_texto="dia 20")
    memoria.ia_falsa.filtro = [
        aprovar(1, "Overfitting", potencial=6),
        {"id": 2, "decisao": "juntar", "juntar_com": 1, "motivo": "mesmo assunto"},
        {"id": 3, "decisao": "descartada", "motivo": "lembrete pessoal"},
        aprovar(4, "CRM com IA", potencial=9, tema="Comercial B2B"),
    ]
    resposta = processar_texto("/filtrar", memoria)
    assert resposta.texto.splitlines() == [
        "🧹 Filtro do banco de ideias", "",
        "✅ Aprovadas (2):",
        "• 📚 Overfitting · IA e Dados · ⭐ 6 → 📣 terça, 03/11",
        "• 💡 CRM com IA · Comercial B2B · ⭐ 9 → 📣 sexta, 20/11", "",
        "🔗 Juntadas (1):", "• “Overfitting no forecast de vendas” → #1", "",
        "🗑️ Descartadas (1):", "• “Comprar pão” — lembrete pessoal", "",
        "🗂️ 2 notas gravadas no Obsidian.",
    ]
    assert resposta.botoes == [("♻️ Manter: Comprar pão", "ir:3")]

    salvas = memoria.memoria.ideias
    assert salvas[1].texto_original == "overfitting\n\n+ overfitting no forecast de vendas"
    assert salvas[1].caminho_nota == "IA e Dados/Overfitting (1).md"
    assert "+ overfitting no forecast de vendas" in NotasFalsas.arquivos["IA e Dados/Overfitting (1).md"]
    assert (salvas[2].status, salvas[3].status) == ("descartada", "descartada")
    assert memoria.ia_falsa.pedidos[0]["ja_aprovadas"] == [] and len(memoria.ia_falsa.pedidos[0]["novas"]) == 4

    assert processar_texto("/filtrar", memoria).texto.startswith("🧹 Nada novo para filtrar")

    # ♻️ Manter: a descartada volta e é aprovada, mesmo que a IA insista em descartar.
    memoria.ia_falsa.filtro = [{"id": 3, "decisao": "descartada", "motivo": "ainda acho fraco"}]
    recuperada = processar_botao("ir:3", memoria)
    assert "✅ Aprovadas (1):\n• 💡 Comprar pão · Outros → 📣 quinta, 05/11" in recuperada.texto
    assert memoria.ia_falsa.pedidos[-1]["manter"] == [3]
    assert memoria.ia_falsa.pedidos[-1]["ja_aprovadas"] == [{"id": 4, "titulo": "CRM com IA"},
                                                           {"id": 1, "titulo": "Overfitting"}]

    # Ideia nova juntada a uma antiga: a nota antiga é atualizada com o texto novo.
    capturar(memoria, "e", tipo="anotacao", texto="overfitting também acontece com poucos dados")
    memoria.ia_falsa.filtro = [{"id": 5, "decisao": "juntar", "juntar_com": 1, "motivo": "mesmo assunto"}]
    processar_texto("/filtrar", memoria)
    assert "+ overfitting também acontece com poucos dados" in NotasFalsas.arquivos["IA e Dados/Overfitting (1).md"]
    assert memoria.memoria.ideias[1].texto_original.endswith("+ overfitting também acontece com poucos dados")


def test_filtro_sem_github_e_com_falha(memoria):
    capturar(memoria, "a", tipo="conceito", texto="churn")
    memoria.ia_falsa.filtro = [aprovar(1, "Churn")]
    NotasFalsas.falhar = True
    texto = processar_texto("/filtrar", memoria).texto
    assert "⚠️ Não consegui gravar as notas no Obsidian: o GitHub recusou" in texto
    assert memoria.memoria.ideias[1].status == "agendada"           # o filtro não se perde

    capturar(memoria, "b", tipo="conceito", texto="LTV")
    memoria.ia_falsa.filtro = [aprovar(2, "LTV")]
    memoria.notas = None
    assert "falta GITHUB_TOKEN e OBSIDIAN_REPO" in processar_texto("/filtrar", memoria).texto


def test_filtro_com_erro_da_ia(memoria):
    capturar(memoria, "a", tipo="conceito", texto="churn")

    def quebrar(instrucoes, mensagem):
        raise ErroIA("Claude respondeu 529: overloaded")
    memoria.ia_falsa.json = quebrar
    texto = processar_texto("/filtrar", memoria).texto
    assert texto.startswith("😕 Não consegui rodar o filtro agora. As ideias continuam guardadas.")
    assert memoria.memoria.ideias[1].status == "nova"


def test_filtro_em_rodadas(memoria):
    for n in range(1, 11):
        capturar(memoria, "x", tipo="conceito", texto=f"conceito {n}")
    memoria.ia_falsa.filtro = [aprovar(n, f"Conceito {n}") for n in range(1, 11)]
    # /filtrar faz uma rodada só (para caber nos 60 s da Vercel)...
    texto = processar_texto("/filtrar", memoria).texto
    assert "✅ Aprovadas (8)" in texto and "⏳ 2 ficaram para o próximo filtro" in texto
    # ...e o filtro de domingo faz duas.
    for n in range(11, 21):
        capturar(memoria, "x", tipo="conceito", texto=f"conceito {n}")
    memoria.ia_falsa.filtro = [aprovar(n, f"Conceito {n}") for n in range(1, 21)]
    enviadas = []
    conteudo.executar_filtro(memoria, lambda t, b=None: enviadas.append(t))
    assert "✅ Aprovadas (12)" in enviadas[0] and "⏳" not in enviadas[0]
    assert [len(p["novas"]) for p in memoria.ia_falsa.pedidos] == [8, 8, 4]


def test_segunda_rodada_falha_e_guarda_a_primeira(memoria):
    for n in range(1, 11):
        capturar(memoria, "x", tipo="conceito", texto=f"conceito {n}")
    memoria.ia_falsa.filtro = [aprovar(n, f"Conceito {n}") for n in range(1, 11)]
    original = memoria.ia_falsa.json

    def limite_na_segunda(instrucoes, mensagem):
        if memoria.ia_falsa.pedidos:
            raise ErroIA("Groq respondeu 429: rate limit")
        return original(instrucoes, mensagem)
    memoria.ia_falsa.json = limite_na_segunda
    enviadas = []
    conteudo.executar_filtro(memoria, lambda t, b=None: enviadas.append(t))   # 2 rodadas
    texto = enviadas[0]
    assert "✅ Aprovadas (8)" in texto
    assert "⏳ 2 ficaram para o próximo filtro (ou use /filtrar de novo)." in texto
    assert sum(i.status == "nova" for i in memoria.memoria.ideias.values()) == 2


NOTA_DO_VAULT = """---
tipo: conceito
disciplinas:
  - "[[IA Generativa para Negócios]]"
status: rascunho
---
# API, RAG e MCP

> [!abstract] Em uma frase
> A API é o canal, o RAG busca conhecimento e o MCP pluga ferramentas.
"""


def test_le_nota_do_vault():
    campos, corpo = ideias.ler_nota_do_vault("﻿" + NOTA_DO_VAULT)
    assert campos == {"tipo": "conceito", "disciplinas": "", "status": "rascunho"}
    assert corpo.startswith("# API, RAG e MCP\n\n> [!abstract]")
    assert ideias.ler_nota_do_vault("sem frontmatter") == ({}, "sem frontmatter")
    assert ideias.nome_da_fonte("_fontes/MBA/API, RAG e MCP.md") == "API, RAG e MCP"


def test_importa_conceitos_do_vault_e_liga_a_nota_original(memoria):
    NotasFalsas.fontes = {
        "_fontes/MBA/API, RAG e MCP.md": ("sha1", NOTA_DO_VAULT),
        "_fontes/MBA/Rascunho pessoal.md": ("sha2", "---\nvenire: false\n---\n# Não importar"),
        "_fontes/MBA/Vazia.md": ("sha3", "---\ntipo: conceito\n---\n"),
        "Outra pasta/Ignorada.md": ("sha4", "# fora de _fontes"),
    }
    memoria.ia_falsa.filtro = [aprovar(1, "Título que a IA inventou", potencial=8)]
    texto = processar_texto("/filtrar", memoria).texto
    assert texto.splitlines()[:5] == [
        "🧹 Filtro do banco de ideias", "", "📥 1 conceito importado do Obsidian.", "",
        "✅ Aprovadas (1):"]
    ideia = memoria.memoria.ideias[1]
    assert (ideia.tipo, ideia.origem, ideia.fonte, ideia.titulo) == (
        "conceito", "obsidian", "_fontes/MBA/API, RAG e MCP.md", "API, RAG e MCP")   # mantém o nome original
    assert memoria.ia_falsa.pedidos[0]["novas"][0]["origem"] == "obsidian"
    nota = NotasFalsas.arquivos["IA e Dados/API, RAG e MCP (1).md"]
    assert "# API, RAG e MCP\n\nFonte: [[API, RAG e MCP]]\n" in nota

    # Rodar de novo sem mudanças: nada novo.
    assert processar_texto("/filtrar", memoria).texto.startswith("🧹 Nada novo para filtrar")

    # Nota editada no vault: o texto e a nota do Venire são atualizados, sem voltar para o filtro.
    NotasFalsas.fontes["_fontes/MBA/API, RAG e MCP.md"] = ("sha1b", NOTA_DO_VAULT + "\nExemplo novo da De Toni.")
    texto = processar_texto("/filtrar", memoria).texto
    assert "🔄 1 atualizado do Obsidian." in texto
    assert memoria.memoria.ideias[1].status == "agendada"
    assert "Exemplo novo da De Toni." in NotasFalsas.arquivos["IA e Dados/API, RAG e MCP (1).md"]


def test_falha_ao_ler_o_vault_nao_trava_o_filtro(memoria):
    def quebrar(pasta):
        raise ErroNotas("o GitHub recusou o GITHUB_TOKEN (vencido ou errado).")
    monkey = NotasFalsas.listar
    NotasFalsas.listar = lambda self, pasta: quebrar(pasta)
    try:
        capturar(memoria, "a", tipo="conceito", texto="churn")
        memoria.ia_falsa.filtro = [aprovar(1, "Churn")]
        texto = processar_texto("/filtrar", memoria).texto
    finally:
        NotasFalsas.listar = monkey
    assert "⚠️ Não consegui ler os conceitos do Obsidian: o GitHub recusou" in texto
    assert "✅ Aprovadas (1)" in texto


def test_repositorio_lista_e_le_fontes():
    def github(request: httpx.Request):
        if request.url.path.endswith("/git/trees/HEAD"):
            return httpx.Response(200, json={"tree": [
                {"path": "_fontes/MBA/A.md", "type": "blob", "sha": "s1"},
                {"path": "_fontes/MBA", "type": "tree", "sha": "t"},
                {"path": "_fontes/MBA/img.png", "type": "blob", "sha": "s2"},
                {"path": "IA e Dados/B (1).md", "type": "blob", "sha": "s3"}]})
        return httpx.Response(200, json={"content": base64.b64encode("# Olá".encode()).decode()})

    repo = RepositorioNotas("ghp_x", "lug-v/venire-notas", cliente=httpx.Client(transport=httpx.MockTransport(github)))
    assert repo.listar("_fontes") == {"_fontes/MBA/A.md": "s1"}
    assert repo.ler("_fontes/MBA/A.md") == "# Olá"
    vazio = RepositorioNotas("ghp_x", "r/r", cliente=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(409))))
    assert vazio.listar("_fontes") == {}


def test_sem_claude_usa_a_ia_padrao(memoria):
    capturar(memoria, "a", tipo="conceito", texto="churn")
    memoria.ia_falsa.filtro = [aprovar(1, "Churn")]
    memoria.claude = None
    assert "✅ Aprovadas (1)" in processar_texto("/filtrar", memoria).texto


def test_cron_do_filtro(memoria):
    enviadas = []
    assert conteudo.executar_filtro(memoria, lambda t, b=None: enviadas.append(t)) == {"enviados": 0, "filtradas": 0}
    capturar(memoria, "a", tipo="conceito", texto="churn")
    memoria.ia_falsa.filtro = [aprovar(1, "Churn")]
    assert conteudo.executar_filtro(memoria, lambda t, b=None: enviadas.append(t)) == {"enviados": 1, "filtradas": 1}
    assert enviadas[0].startswith("🧹 Filtro do banco de ideias")


def agendar_dois(memoria):
    capturar(memoria, "a", tipo="conceito", texto="overfitting")
    capturar(memoria, "b", tipo="ideia", texto="CRM com IA")
    memoria.ia_falsa.filtro = [aprovar(1, "Overfitting", 9), aprovar(2, "CRM com IA", 5)]
    processar_texto("/filtrar", memoria)


def test_linkedin_vespera_dia_e_botoes(memoria, monkeypatch):
    agendar_dois(memoria)                       # 1 → terça 03/11, 2 → quinta 05/11
    assert processar_texto("/linkedin", memoria).texto.splitlines()[:3] == [
        "📣 Próximos posts no LinkedIn", "• terça, 03/11 — Overfitting", "• quinta, 05/11 — CRM com IA"]

    # Segunda, 8h: nada para hoje; escreve o post de terça (véspera).
    memoria.ia_falsa.posts = ["Seu modelo decorou a prova?\n\n...\n\n#ia #dados #vendas"]
    enviados = []
    enviar = lambda texto, botoes=None: enviados.append((texto, botoes))   # noqa: E731
    assert conteudo.executar_linkedin(memoria, enviar) == {"enviados": 0, "escritos": 1, "falhas": 0}
    assert memoria.memoria.ideias[1].post_texto.startswith("Seu modelo decorou a prova?")
    assert "Seu modelo decorou a prova?" in NotasFalsas.arquivos["IA e Dados/Overfitting (1).md"]
    assert "✍️" in processar_texto("/linkedin", memoria).texto

    # Terça, 8h: entrega o rascunho com os 4 botões (amanhã é quarta, sem post).
    monkeypatch.setattr(memoria, "hoje", lambda: TERCA)
    assert conteudo.executar_linkedin(memoria, enviar) == {"enviados": 1, "escritos": 0, "falhas": 0}
    texto, botoes = enviados[0]
    assert texto.startswith("📣 Post de hoje: Overfitting\n\nSeu modelo decorou a prova?")
    assert [b[0] for b in botoes] == ["✅ Publiquei", "✏️ Reescrever", "⏭️ Adiar uma semana", "🗑️ Descartar"]
    assert conteudo.executar_linkedin(memoria, enviar)["enviados"] == 0     # nada duplicado

    # Resumo das 6h30 ganha a linha do post.
    with memoria.banco() as c:
        assert ideias.bloco_resumo(c, TERCA) == "📣 Post de hoje: Overfitting"

    memoria.ia_falsa.posts = ["Versão nova #ia"]
    nova = processar_botao("lr:1", memoria)
    assert nova.texto.startswith("✏️ Nova versão: Overfitting\n\nVersão nova #ia")
    assert memoria.ia_falsa.pedidos[-1]["versao_anterior"].startswith("Seu modelo decorou")

    assert processar_botao("lp:1", memoria).texto.startswith("✅ Publicado: Overfitting")
    assert memoria.memoria.ideias[1].publicado_em == TERCA
    assert "status: publicada" in NotasFalsas.arquivos["IA e Dados/Overfitting (1).md"]
    assert processar_botao("lp:1", memoria).texto == "✅ “Overfitting” já estava marcado como publicado."
    assert processar_botao("lr:1", memoria).texto == "Esse post não está mais agendado."

    # Quarta, 8h: escreve o de quinta. Quinta: se a véspera falhou, escreve na hora e entrega.
    monkeypatch.setattr(memoria, "hoje", lambda: date(2026, 11, 4))
    assert conteudo.executar_linkedin(memoria, enviar) == {"enviados": 0, "escritos": 1, "falhas": 0}
    memoria.memoria.ideias[2].post_texto = ""
    monkeypatch.setattr(memoria, "hoje", lambda: QUINTA)
    assert conteudo.executar_linkedin(memoria, enviar) == {"enviados": 1, "escritos": 1, "falhas": 0}
    assert enviados[-1][0].startswith("📣 Post de hoje: CRM com IA")


def test_adiar_trocar_data_e_descartar(memoria):
    agendar_dois(memoria)
    assert processar_botao("la:1", memoria).texto == "📅 “Overfitting” agora sai terça, 10/11."
    opcoes = processar_botao("lt:2", memoria)
    assert [b[0] for b in opcoes.botoes] == ["Terça, 03/11", "Quinta, 05/11", "Quinta, 12/11", "Terça, 17/11"]
    assert processar_botao(opcoes.botoes[0][1], memoria).texto == "📅 “CRM com IA” agora sai terça, 03/11."
    assert processar_botao("ld:2", memoria).texto.startswith("🗑️ Post descartado: CRM com IA")
    assert memoria.memoria.ideias[2].data_prevista is None
    assert processar_botao("la:2", memoria).texto == "Esse post não está mais agendado."
    assert processar_botao("lp:99", memoria).texto == "Essa ideia não existe mais."


# ---------------------------------------------------------------- Correções da revisão

def test_post_que_falhou_sai_atrasado_e_nao_trava_os_outros(memoria, monkeypatch):
    agendar_dois(memoria)                            # 1 → terça 03/11, 2 → quinta 05/11
    original = memoria.ia_falsa.json

    def groq_fora(instrucoes, mensagem):
        raise ErroIA("Groq respondeu 429")
    memoria.ia_falsa.json = groq_fora
    enviados = []
    enviar = lambda texto, botoes=None: enviados.append(texto)   # noqa: E731
    monkeypatch.setattr(memoria, "hoje", lambda: TERCA)
    assert conteudo.executar_linkedin(memoria, enviar) == {"enviados": 0, "escritos": 0, "falhas": 1}
    assert "⚠️ atrasado" not in processar_texto("/linkedin", memoria).texto

    # Quarta: o Groq voltou. O post de terça sai como atrasado e continua visível até ser resolvido.
    memoria.ia_falsa.json = original
    monkeypatch.setattr(memoria, "hoje", lambda: date(2026, 11, 4))
    assert "terça, 03/11 — Overfitting ⚠️ atrasado" in processar_texto("/linkedin", memoria).texto
    assert conteudo.executar_linkedin(memoria, enviar)["enviados"] == 1
    assert enviados[0].startswith("📣 Post atrasado (era de terça, 03/11): Overfitting")
    assert conteudo.executar_linkedin(memoria, enviar)["enviados"] == 0     # sai uma vez só

    # Dois posts no mesmo dia: a falha no primeiro não impede o segundo.
    memoria.memoria.ideias[1].post_texto = ""
    memoria.memoria.ideias[1].data_prevista = QUINTA
    memoria.memoria.ideias[2].post_texto = "Pronto #ia"
    chamadas = {"n": 0}

    def falha_so_no_primeiro(instrucoes, mensagem):
        chamadas["n"] += 1
        raise ErroIA("Groq respondeu 429")
    memoria.ia_falsa.json = falha_so_no_primeiro
    monkeypatch.setattr(memoria, "hoje", lambda: QUINTA)
    assert conteudo.executar_linkedin(memoria, enviar) == {"enviados": 1, "escritos": 0, "falhas": 1}
    assert enviados[-1].startswith("📣 Post de hoje: CRM com IA")


def test_adiar_envia_de_novo_na_nova_data(memoria, monkeypatch):
    agendar_dois(memoria)
    enviados = []
    monkeypatch.setattr(memoria, "hoje", lambda: TERCA)
    conteudo.executar_linkedin(memoria, lambda t, b=None: enviados.append(t))
    assert processar_botao("la:1", memoria).texto == "📅 “Overfitting” agora sai terça, 10/11."
    monkeypatch.setattr(memoria, "hoje", lambda: date(2026, 11, 10))
    conteudo.executar_linkedin(memoria, lambda t, b=None: enviados.append(t))
    do_adiado = [t.split(":")[0] for t in enviados if "Overfitting" in t.split("\n")[0]]
    assert do_adiado == ["📣 Post de hoje", "📣 Post de hoje"]     # terça 03/11 e de novo em 10/11
    assert any(t.startswith("📣 Post atrasado (era de quinta, 05/11): CRM com IA") for t in enviados)


def test_filtro_com_trava(memoria):
    memoria.memoria.envios.add(conteudo.TRAVA_FILTRO)              # outro filtro rodando
    capturar(memoria, "a", tipo="conceito", texto="churn")
    assert processar_texto("/filtrar", memoria).texto == "🧹 O filtro já está rodando. Espere a mensagem dele chegar."
    assert conteudo.executar_filtro(memoria, print)["motivo"] == "outro filtro rodando"
    memoria.memoria.envios.clear()
    memoria.ia_falsa.filtro = [aprovar(1, "Churn")]
    assert "✅ Aprovadas (1)" in processar_texto("/filtrar", memoria).texto
    assert conteudo.TRAVA_FILTRO not in memoria.memoria.envios          # liberada no fim

    def quebrar(instrucoes, mensagem):
        raise ErroIA("falhou")
    capturar(memoria, "b", tipo="conceito", texto="LTV")
    memoria.ia_falsa.json = quebrar
    processar_texto("/filtrar", memoria)
    assert conteudo.TRAVA_FILTRO not in memoria.memoria.envios          # liberada mesmo com erro


def test_nota_ignorada_nao_e_baixada_de_novo_e_volta_se_desmarcar(memoria):
    lidas = []
    original = NotasFalsas.ler
    NotasFalsas.ler = lambda self, c: lidas.append(c) or original(self, c)
    try:
        NotasFalsas.fontes = {"_fontes/MBA/Pessoal.md": ("s1", "---\nvenire: false\n---\n# Pessoal\ntexto")}
        assert processar_texto("/filtrar", memoria).texto.startswith("🧹 Nada novo")
        assert processar_texto("/filtrar", memoria).texto.startswith("🧹 Nada novo")
        assert lidas == ["_fontes/MBA/Pessoal.md"]                      # baixada uma vez só
        assert memoria.memoria.ideias[1].status == "descartada"

        NotasFalsas.fontes = {"_fontes/MBA/Pessoal.md": ("s2", "# Pessoal\nagora pode")}   # tirou a marca
        memoria.ia_falsa.filtro = [aprovar(1, "x")]
        texto = processar_texto("/filtrar", memoria).texto
    finally:
        NotasFalsas.ler = original
    assert "📥 1 conceito importado do Obsidian." in texto and "✅ Aprovadas (1)" in texto
    assert memoria.memoria.ideias[1].texto_original == "# Pessoal\nagora pode"


def test_importacao_limitada_por_execucao(memoria, monkeypatch):
    monkeypatch.setattr(conteudo, "LIMITE_IMPORTACAO", 3)
    NotasFalsas.fontes = {f"_fontes/MBA/C{n}.md": (f"s{n}", f"# C{n}") for n in range(5)}
    assert conteudo.importar_fontes(memoria) == (3, 0, "")
    assert conteudo.importar_fontes(memoria) == (2, 0, "")
    assert conteudo.importar_fontes(memoria) == (0, 0, "")


def test_data_pedida_ocupada_vai_para_o_proximo_dia_livre():
    a = Ideia(id=1, data_prevista=QUINTA)
    b = Ideia(id=2, data_prevista=QUINTA)                     # pediu o mesmo dia
    c = Ideia(id=3, data_prevista=date(2026, 11, 10))         # pediu um dia já ocupado
    ideias.distribuir([a, b, c], {date(2026, 11, 10)}, HOJE)
    assert (a.data_prevista, b.data_prevista, c.data_prevista) == (
        QUINTA, date(2026, 11, 12), date(2026, 11, 17))
    assert ideias.data_pedida_livre(date(2026, 11, 20), set(), HOJE) == date(2026, 11, 20)   # sexta livre


def test_trocar_para_data_que_foi_ocupada(memoria):
    agendar_dois(memoria)                                     # 1 → 03/11, 2 → 05/11
    texto = processar_botao("lq:2:2026-11-03", memoria).texto  # botão antigo: 03/11 já é do 1
    assert texto == "📅 “CRM com IA” agora sai quinta, 05/11.\n(Terça, 03/11 já não estava livre.)"


def test_desfazer_antigo_nao_apaga_ideia_agendada(memoria):
    agendar_dois(memoria)
    resposta = processar_botao("ix:1", memoria)
    assert resposta.texto.startswith("Essa ideia já passou pelo filtro e está agendada para terça, 03/11.")
    assert resposta.botoes == [("🗑️ Descartar", "ld:1")]
    assert memoria.memoria.ideias[1].status == "agendada"
    processar_botao("ld:1", memoria)
    assert processar_botao("ix:1", memoria).texto == (
        "Essa ideia já passou pelo filtro (descartada), então não dá mais para desfazer.")


def test_resumo_da_manha_sem_post_nao_muda(memoria):
    with memoria.banco() as c:
        assert ideias.bloco_resumo(c, HOJE) == ""
    texto = automacoes.resumo_do_dia(memoria, HOJE, "☀️ Bom dia!", com_dolar_e_gastos=False)
    assert "📣" not in texto


# ---------------------------------------------------------------- SQL de verdade

@pytest.fixture
def conexao_real():
    if not URL_TESTE:
        pytest.skip("TEST_DATABASE_URL não configurada")
    recriar_tabelas(URL_TESTE)
    with psycopg.connect(URL_TESTE) as conexao:
        yield conexao


def test_sql_da_tabela_ideias(conexao_real):
    c = conexao_real
    a = Ideia(tipo="conceito", texto_original="overfitting", criado_em=HOJE)
    b = Ideia(tipo="ideia", texto_original="CRM com IA", criado_em=HOJE, origem="audio")
    assert ideias.salvar(c, a) == 1 and ideias.salvar(c, b) == 2
    assert ideias.contagem(c) == {"nova": 2}
    assert [i.id for i in ideias.listar(c, "nova")] == [1, 2]
    assert [i.id for i in ideias.listar(c, ("nova",), recentes_primeiro=True)] == [2, 1]

    a.status, a.titulo, a.tema, a.potencial, a.data_prevista = "agendada", "Overfitting", "IA e Dados", 8, TERCA
    ideias.atualizar(c, a)
    lida = ideias.buscar(c, 1)
    assert (lida.status, lida.titulo, lida.potencial, lida.data_prevista, lida.criado_em) == (
        "agendada", "Overfitting", 8, TERCA, HOJE)
    assert ideias.datas_ocupadas(c) == {TERCA}
    assert [i.id for i in ideias.do_dia(c, TERCA)] == [1]
    assert [i.id for i in ideias.proximas(c, HOJE)] == [1]
    assert [i.id for i in ideias.pendentes_ate(c, TERCA)] == [1] and ideias.pendentes_ate(c, HOJE) == []
    assert envios.reservar_trava(c, "trava:teste") and not envios.reservar_trava(c, "trava:teste")
    c.execute("UPDATE envios SET enviado_em = NOW() - INTERVAL '10 minutes' WHERE chave = 'trava:teste'")
    assert envios.reservar_trava(c, "trava:teste")              # trava esquecida vence sozinha
    assert ideias.apagar(c, 2) and not ideias.apagar(c, 2)
    assert ideias.buscar(c, 2) is None

    do_vault = Ideia(tipo="conceito", texto_original="RAG", criado_em=HOJE, origem="obsidian",
                     fonte="_fontes/MBA/RAG.md", fonte_sha="abc")
    ideias.salvar(c, do_vault)
    assert list(ideias.por_fonte(c)) == ["_fontes/MBA/RAG.md"]
    ideias.criar_tabela(c)  # rodar de novo (produção) não quebra: as colunas novas usam IF NOT EXISTS
