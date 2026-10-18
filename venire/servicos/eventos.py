"""Compromissos: montar o evento a partir do que a IA extraiu e formatar respostas."""

from datetime import date, datetime, time, timedelta
import uuid

from pydantic import BaseModel, Field

from venire import repeticao as rep
from venire.datas import data_do_evento, descrever, hora_do_evento

DURACAO_PADRAO = 60  # minutos, quando não há horário final


class Evento(BaseModel):
    # O Google aceita IDs com letras de a-v e números; um UUID sem hífens serve.
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    titulo: str
    data: date
    inicio: time | None = None
    fim: time | None = None
    local: str = ""
    texto_original: str = ""
    nota: str = ""
    detalhes: str = ""             # vai na descrição do Google (professor, observações...)
    repeticao: str = ""            # regra RRULE, ex.: "FREQ=WEEKLY;BYDAY=TU,TH"
    repeticao_texto: str = ""      # ex.: "toda terça e quinta"

    @property
    def dia_inteiro(self) -> bool:
        return self.inicio is None


class Interpretacao(BaseModel):
    """Resultado de entender um pedido de agendamento."""
    evento: Evento | None = None
    opcoes_de_data: list[date] = []   # preenchido quando é preciso perguntar o dia
    problema: str = ""                # o que faltou para conseguir agendar


def titulo_bonito(texto: str) -> str:
    texto = " ".join((texto or "").split())[:120]
    return texto[:1].upper() + texto[1:] if texto else "Compromisso"


def de_dados(dados: dict, texto: str, referencia: date, hora_atual: time | None = None) -> Interpretacao:
    """Transforma os campos extraídos pela IA em um Evento, com datas calculadas em Python."""
    resultado_data = data_do_evento(str(dados.get("data_texto") or ""), referencia)
    if resultado_data is None:
        return Interpretacao(problema="Não entendi a data. Tente algo como “dia 15”, “quinta” ou “15/10”.")

    inicio = hora_do_evento(str(dados.get("hora_texto") or ""))
    fim = hora_do_evento(str(dados.get("hora_fim_texto") or "")) if inicio else None
    if inicio and fim is None:
        try:
            duracao = int(dados.get("duracao_minutos") or DURACAO_PADRAO)
        except (TypeError, ValueError):
            duracao = DURACAO_PADRAO
        fim = (datetime.combine(date.min, inicio) + timedelta(minutes=max(15, min(duracao, 720)))).time()

    nota = resultado_data.nota
    data_escolhida = resultado_data.opcoes[0]
    # Sem dia informado e com horário que já passou hoje → amanhã.
    if not dados.get("data_texto") and inicio and hora_atual and referencia == data_escolhida:
        if inicio <= hora_atual:
            data_escolhida = referencia + timedelta(days=1)
            nota = "esse horário já passou hoje, então marquei para amanhã"

    regra = rep.interpretar(str(dados.get("repeticao_texto") or ""), referencia)
    if regra:
        # a data vira a primeira ocorrência da regra a partir do dia pedido (ou de hoje)
        data_escolhida = regra.primeira_data(resultado_data.opcoes[0], inicio,
                                             hora_atual if resultado_data.opcoes[0] == referencia else None)
        nota = ""

    evento = Evento(
        titulo=titulo_bonito(dados.get("titulo")),
        data=data_escolhida,
        inicio=inicio,
        fim=fim,
        local=str(dados.get("local") or "")[:200],
        texto_original=texto[:500],
        detalhes=str(dados.get("detalhes") or "")[:500],
        nota=nota,
        repeticao=regra.regra if regra else "",
        repeticao_texto=regra.descricao if regra else "",
    )
    if len(resultado_data.opcoes) > 1 and not regra:
        return Interpretacao(evento=evento, opcoes_de_data=resultado_data.opcoes)
    return Interpretacao(evento=evento)


def quando(evento: Evento) -> str:
    dia = descrever(evento.data)
    if evento.dia_inteiro:
        return f"{dia} · dia inteiro"
    return f"{dia} · {evento.inicio:%H:%M}–{evento.fim:%H:%M}"


def confirmacao(evento: Evento) -> str:
    if evento.repeticao:
        horario = "dia inteiro" if evento.dia_inteiro else f"{evento.inicio:%H:%M}–{evento.fim:%H:%M}"
        linhas = [f"🔁 Agendado: {evento.titulo}", f"🗓️ {evento.repeticao_texto[:1].upper()}{evento.repeticao_texto[1:]} · {horario}",
                  f"▶️ Começa {descrever(evento.data)}"]
    else:
        linhas = [f"📅 Agendado: {evento.titulo}", f"🗓️ {quando(evento)}"]
    if evento.local:
        linhas.append(f"📍 {evento.local}")
    if evento.nota:
        linhas.append(f"ℹ️ {evento.nota[:1].upper()}{evento.nota[1:]}.")
    return "\n".join(linhas)


def horario_de(item: dict) -> str:
    """Formata o horário de um evento vindo do Google."""
    inicio = item.get("start", {})
    if "dateTime" not in inicio:
        return "dia inteiro"
    comeco = datetime.fromisoformat(inicio["dateTime"]).strftime("%H:%M")
    fim = item.get("end", {}).get("dateTime")
    return f"{comeco}–{datetime.fromisoformat(fim).strftime('%H:%M')}" if fim else comeco


def aviso_conflito(evento: Evento, conflitos: list[dict]) -> str:
    nomes = "\n".join(f"• {horario_de(c)} — {c.get('summary', '(sem título)')}" for c in conflitos[:5])
    return (f"⚠️ {evento.titulo} ({quando(evento)}) conflita com:\n{nomes}\n\n"
            "Quer agendar mesmo assim?")


def lista_do_dia(titulo: str, itens: list[dict]) -> str:
    if not itens:
        return f"{titulo}\nNada marcado. 🙌"
    linhas = [titulo]
    for item in itens:
        local = f" · {item['location']}" if item.get("location") else ""
        linhas.append(f"• {horario_de(item)} — {item.get('summary', '(sem título)')}{local}")
    return "\n".join(linhas)
