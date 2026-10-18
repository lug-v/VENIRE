"""Google Agenda: criar, apagar e listar compromissos, e encontrar conflitos.

O Google Agenda é a fonte oficial dos compromissos: o Venire sempre consulta
o próprio Google antes de responder, então mudanças feitas pelo celular valem.
"""

from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from venire.config import FUSO
from venire.servicos.google import ler_conta_servico, token, verificar

API = "https://www.googleapis.com/calendar/v3/calendars/{id}"
ESCOPO = ["https://www.googleapis.com/auth/calendar"]
DICAS = {
    403: "o robô não tem acesso: compartilhe a agenda com o e-mail dele com a permissão "
         "“Fazer alterações nos eventos” e confira se a Google Calendar API está ativada",
    404: "agenda não encontrada: o GOOGLE_CALENDAR_ID deve ser o seu Gmail, e a agenda precisa "
         "estar compartilhada com o robô",
    409: "já existe um evento com esse identificador",
}


class Calendario:
    def __init__(self, conta_servico_json: str, agenda_id: str, cliente: httpx.Client | None = None):
        if not conta_servico_json or not agenda_id:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON ou GOOGLE_CALENDAR_ID não configurados.")
        self._info = ler_conta_servico(conta_servico_json)
        self._url = API.format(id=quote(agenda_id.strip(), safe="@."))
        self._cliente = cliente or httpx.Client(timeout=20)
        self._token = None
        self._fuso = ZoneInfo(FUSO)

    def _cabecalhos(self) -> dict:
        if self._token is None:
            self._token = token(self._info, ESCOPO)
        return {"Authorization": f"Bearer {self._token}"}

    def _pedir(self, metodo: str, caminho: str, **kwargs) -> dict:
        resposta = self._cliente.request(metodo, self._url + caminho, headers=self._cabecalhos(), **kwargs)
        verificar(resposta, DICAS)
        return resposta.json() if resposta.content else {}

    @property
    def email_robo(self) -> str:
        return self._info.get("client_email", "")

    def testar(self) -> str:
        return self._pedir("GET", "").get("summary", "")

    def criar(self, evento) -> str:
        corpo = {"id": evento.id, "summary": evento.titulo, **self._periodo(evento)}
        if getattr(evento, "repeticao", ""):
            corpo["recurrence"] = [f"RRULE:{evento.repeticao}"]
        if evento.local:
            corpo["location"] = evento.local
        descricao = [getattr(evento, "detalhes", "")]
        if evento.texto_original:
            descricao.append(f"Criado pelo Venire a partir de: “{evento.texto_original}”")
        if any(descricao):
            corpo["description"] = "\n\n".join(d for d in descricao if d)
        return self._pedir("POST", "/events", json=corpo).get("htmlLink", "")

    def apagar(self, evento_id: str) -> bool:
        resposta = self._cliente.delete(f"{self._url}/events/{evento_id}", headers=self._cabecalhos())
        if resposta.status_code in (404, 410):
            return False  # já tinha sido apagado (por exemplo, pelo celular)
        verificar(resposta, DICAS)
        return True

    def listar(self, inicio: datetime, fim: datetime) -> list[dict]:
        dados = self._pedir("GET", "/events", params={
            "timeMin": inicio.isoformat(), "timeMax": fim.isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 50,
        })
        return [e for e in dados.get("items", []) if e.get("status") != "cancelled"]

    def conflitos(self, evento) -> list[dict]:
        """Eventos com horário marcado que se sobrepõem ao novo. Dias inteiros não contam."""
        if evento.dia_inteiro:
            return []
        inicio, fim = self._datas(evento)
        return [
            e for e in self.listar(inicio, fim)
            if "dateTime" in e.get("start", {}) and e.get("transparency") != "transparent"
        ]

    def _datas(self, evento) -> tuple[datetime, datetime]:
        inicio = datetime.combine(evento.data, evento.inicio, self._fuso)
        fim = datetime.combine(evento.data, evento.fim, self._fuso)
        if fim <= inicio:
            fim += timedelta(days=1)  # termina depois da meia-noite
        return inicio, fim

    def _periodo(self, evento) -> dict:
        if evento.dia_inteiro:
            return {"start": {"date": evento.data.isoformat()},
                    "end": {"date": (evento.data + timedelta(days=1)).isoformat()}}
        inicio, fim = self._datas(evento)
        return {"start": {"dateTime": inicio.isoformat(), "timeZone": FUSO},
                "end": {"dateTime": fim.isoformat(), "timeZone": FUSO}}

    def do_dia(self, dia: date) -> list[dict]:
        inicio = datetime.combine(dia, datetime.min.time(), self._fuso)
        return self.listar(inicio, inicio + timedelta(days=1))
