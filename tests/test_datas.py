"""Testes das contas de calendário. Referência: terça-feira, 13/10/2026."""

from datetime import date

import pytest

from venire.datas import data_do_gasto, inicio_da_semana

HOJE = date(2026, 10, 13)  # terça


@pytest.mark.parametrize("quando, esperado", [
    ("", date(2026, 10, 13)),
    ("hoje", date(2026, 10, 13)),
    ("ontem", date(2026, 10, 12)),
    ("anteontem", date(2026, 10, 11)),
    ("sexta", date(2026, 10, 9)),
    ("na sexta-feira", date(2026, 10, 9)),
    ("Terça", date(2026, 10, 13)),
    ("terça passada", date(2026, 10, 6)),
    ("domingo", date(2026, 10, 11)),
    ("dia 10", date(2026, 10, 10)),
    ("dia 20", date(2026, 9, 20)),
    ("05/10", date(2026, 10, 5)),
    ("25/12", date(2025, 12, 25)),
    ("31/02", date(2026, 10, 13)),
    ("algum dia aí", date(2026, 10, 13)),
])
def test_data_do_gasto(quando, esperado):
    assert data_do_gasto(quando, HOJE) == esperado


def test_semana_comeca_no_domingo():
    assert inicio_da_semana(HOJE) == date(2026, 10, 11)
    assert inicio_da_semana(date(2026, 10, 11)) == date(2026, 10, 11)
    assert inicio_da_semana(date(2026, 10, 17)) == date(2026, 10, 11)
