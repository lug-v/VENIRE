"""Rota de leitura do Drill: senha, formato e falhas parciais (sem banco real)."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from api import index
from venire.nucleo import drill
from venire.nucleo.respostas import Dependencias
from venire.servicos.financas import Mes

DIA = date(2026, 11, 9)


class CalendarioFalso:
    def listar(self, inicio, fim):
        return [
            {"summary": "Aula MBA FGV", "start": {"dateTime": "2026-11-09T19:00:00-03:00"},
             "end": {"dateTime": "2026-11-09T22:30:00-03:00"}, "location": "https://fgv.zoom.us/j/123"},
            {"summary": "Feriado", "start": {"date": "2026-11-10"}, "end": {"date": "2026-11-11"}},
        ]


@contextmanager
def banco_falso():
    yield object()


def deps_falsas(**extra):
    return Dependencias(ia=lambda: None, banco=banco_falso, calendario=lambda: CalendarioFalso(),
                        hoje=lambda: DIA, **extra)


def financas_falsas(monkeypatch):
    monkeypatch.setattr(drill.financas, "mes", lambda c, i, f: Mes(Decimal("3550.50"), Decimal("1240.50")))
    monkeypatch.setattr(drill.financas, "projecao", lambda c, d: Mes(Decimal("3550.50"), Decimal("1970.50")))
    monkeypatch.setattr(drill.tarefas, "abertas", lambda c: [("1", "comprar ração", DIA), ("2", "pagar boleto", DIA)])


def test_montar_junta_agenda_mes_e_tarefas(monkeypatch):
    financas_falsas(monkeypatch)
    r = drill.montar(deps_falsas(), DIA)
    assert r["erros"] == []
    assert [e["titulo"] for e in r["eventos"]] == ["Aula MBA FGV", "Feriado"]
    assert r["eventos"][0]["local"].startswith("https://fgv.zoom.us/")
    assert r["eventos"][1]["dia_todo"] is True
    assert r["mes"]["nome"] == "novembro"
    assert r["mes"]["gastos"]["valor"] == 1240.5
    assert r["mes"]["saldo"]["positivo"] is True
    assert r["tarefas"] == ["comprar ração", "pagar boleto"]


def test_falha_no_banco_nao_derruba_a_agenda(monkeypatch):
    def quebra(*a):
        raise RuntimeError("sem banco")
    monkeypatch.setattr(drill.financas, "mes", quebra)
    r = drill.montar(deps_falsas(), DIA)
    assert len(r["eventos"]) == 2
    assert r["mes"] is None
    assert r["erros"] and r["erros"][0].startswith("banco:")


def test_rota_exige_senha(monkeypatch):
    monkeypatch.setenv("DRILL_SECRET", "senha-certa")
    cliente = TestClient(index.app)
    assert cliente.get("/drill/resumo").status_code == 401
    assert cliente.get("/drill/resumo", headers={"X-Drill-Secret": "errada"}).status_code == 401


def test_rota_sem_senha_configurada_recusa_tudo(monkeypatch):
    monkeypatch.delenv("DRILL_SECRET", raising=False)
    cliente = TestClient(index.app)
    assert cliente.get("/drill/resumo", headers={"X-Drill-Secret": ""}).status_code == 401


def test_rota_com_senha_devolve_resumo(monkeypatch):
    monkeypatch.setenv("DRILL_SECRET", "senha-certa")
    monkeypatch.setattr(index, "montar_dependencias", lambda config: deps_falsas())
    financas_falsas(monkeypatch)
    r = TestClient(index.app).get("/drill/resumo", headers={"X-Drill-Secret": "senha-certa"})
    assert r.status_code == 200
    assert r.json()["mes"]["saldo"]["texto"].startswith("R$")
