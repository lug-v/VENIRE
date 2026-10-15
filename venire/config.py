"""Configurações do Venire, lidas das variáveis de ambiente.

Localmente elas vêm do arquivo .env; na Vercel, de Settings → Environment Variables.
Nenhum segredo fica escrito no código.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

FUSO = "America/Sao_Paulo"


def _carregar_env_local() -> None:
    """Lê o arquivo .env (se existir) sem precisar de biblioteca extra."""
    arquivo = Path(__file__).resolve().parents[1] / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip())


_carregar_env_local()


@dataclass(frozen=True)
class Config:
    telegram_token: str
    venire_secret: str
    chat_id_autorizado: int | None
    database_url: str = ""
    groq_api_key: str = ""
    groq_modelo: str = "qwen/qwen3.8-27b"
    google_conta_servico_json: str = ""
    google_planilha_id: str = ""

    @property
    def segredo_webhook(self) -> str:
        """Senha que o Telegram envia em cada mensagem para provar que é ele.

        É derivada do VENIRE_SECRET, então você só precisa cadastrar uma senha.
        """
        return hashlib.sha256(f"telegram:{self.venire_secret}".encode()).hexdigest()


def carregar_config() -> Config:
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return Config(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        venire_secret=os.getenv("VENIRE_SECRET", "").strip(),
        chat_id_autorizado=int(chat_id) if chat_id.lstrip("-").isdigit() else None,
        database_url=os.getenv("DATABASE_URL", "").strip(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_modelo=os.getenv("GROQ_MODEL", "").strip() or "qwen/qwen3.8-27b",
        google_conta_servico_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip(),
        google_planilha_id=os.getenv("GOOGLE_SHEET_ID", "").strip(),
    )
