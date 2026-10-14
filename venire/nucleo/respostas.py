"""Decide o que responder. Por enquanto (fase 1), só boas-vindas e ajuda.

Este arquivo não sabe nada de Telegram: recebe texto e devolve texto.
É isso que permitirá, no futuro, usar o mesmo núcleo num app de celular.
"""

AJUDA = (
    "Sou o Venire, seu assessor pessoal. 👋\n\n"
    "Estou em construção. Em breve vou:\n"
    "📅 agendar compromissos no seu Google Agenda\n"
    "💰 registrar gastos por texto, áudio ou foto\n"
    "☀️ mandar clima e dólar toda manhã às 6h30\n\n"
    "Por enquanto, me mande qualquer mensagem e eu respondo."
)


def responder(texto: str, nome: str = "") -> str:
    texto = (texto or "").strip()
    saudacao = f", {nome}" if nome else ""

    if texto in ("/start", "/ajuda"):
        return AJUDA
    if not texto:
        return "Por enquanto eu só entendo texto. Áudio e foto chegam nas próximas fases. 😉"
    return f"Olá{saudacao}! Recebi: “{texto}”.\nAinda estou aprendendo, mas o caminho Telegram → Venire → Telegram já funciona. ✅"
