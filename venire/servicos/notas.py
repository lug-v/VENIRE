"""Grava as notas do Obsidian num repositório do GitHub (ex.: lug-v/venire-notas).

No computador, o plugin Obsidian Git puxa o repositório para dentro do vault.
Assim o Venire não depende do PC ligado e o custo continua zero.
"""

import base64
from urllib.parse import quote

import httpx

API = "https://api.github.com/repos/{repo}/contents/{caminho}"


class ErroNotas(Exception):
    pass


class RepositorioNotas:
    def __init__(self, token: str, repo: str, cliente: httpx.Client | None = None):
        if not token or not repo:
            raise ErroNotas("GITHUB_TOKEN ou OBSIDIAN_REPO não configurado.")
        self._repo = repo.strip().strip("/")
        self._cabecalho = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._cliente = cliente or httpx.Client(timeout=20)

    def _url(self, caminho: str) -> str:
        return API.format(repo=self._repo, caminho=quote(caminho, safe="/"))

    def gravar(self, caminho: str, conteudo: str, mensagem: str) -> None:
        """Cria o arquivo ou substitui o que já existe no mesmo caminho."""
        url = self._url(caminho)
        atual = self._cliente.get(url, headers=self._cabecalho)
        corpo = {"message": mensagem, "content": base64.b64encode(conteudo.encode("utf-8")).decode()}
        if atual.status_code == 200:
            corpo["sha"] = atual.json().get("sha")
        elif atual.status_code != 404:
            raise ErroNotas(self._explicar(atual))
        resposta = self._cliente.put(url, headers=self._cabecalho, json=corpo)
        if resposta.status_code not in (200, 201):
            raise ErroNotas(self._explicar(resposta))

    def listar(self, pasta: str) -> dict[str, str]:
        """Arquivos .md dentro da pasta → sha de cada um (o sha muda quando o arquivo muda)."""
        url = f"https://api.github.com/repos/{self._repo}/git/trees/HEAD?recursive=1"
        resposta = self._cliente.get(url, headers=self._cabecalho)
        if resposta.status_code == 409:  # repositório vazio
            return {}
        if resposta.status_code != 200:
            raise ErroNotas(self._explicar(resposta))
        prefixo = pasta.strip("/") + "/"
        return {item["path"]: item["sha"] for item in resposta.json().get("tree", [])
                if item.get("type") == "blob" and item["path"].startswith(prefixo) and item["path"].endswith(".md")}

    def ler(self, caminho: str) -> str:
        resposta = self._cliente.get(self._url(caminho), headers=self._cabecalho)
        if resposta.status_code != 200:
            raise ErroNotas(self._explicar(resposta))
        return base64.b64decode(resposta.json().get("content", "")).decode("utf-8", errors="replace")

    @staticmethod
    def _explicar(resposta: httpx.Response) -> str:
        if resposta.status_code == 401:
            return "o GitHub recusou o GITHUB_TOKEN (vencido ou errado)."
        if resposta.status_code in (403, 404):
            return "o token não tem acesso de escrita ao repositório das notas (confira OBSIDIAN_REPO e as permissões)."
        return f"GitHub respondeu {resposta.status_code}: {resposta.text[:200]}"
