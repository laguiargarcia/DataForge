"""Propriedade de investimentos_per_month (df-xkhx.4): só o fluxo estrutural conta, e a posição do mês é
o último snapshot de cada aplicação.

Fica fora dos alvos de mutação do testloop.toml (df-2edm), como test_classificacao_propriedades.py.
"""
import sys
from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from tests import _delta_util

sys.path.insert(0, str(_delta_util.SCRIPTS / "report"))

from report_lib import investimentos_per_month
from tests.test_report_v3 import _inv_fact, _positions

# Valores inteiros: as somas em float saem exatas em qualquer ordem.
_dias = st.dates(date(2026, 5, 1), date(2026, 7, 31))
_fluxos = st.lists(st.tuples(
    _dias, st.just("BANK"), st.integers(-1000, 1000).map(float), st.sampled_from(["investment", None, "outra"]),
    st.sampled_from([True, True, False, None]),
    st.sampled_from(["APLIC_FINANCEIRA", "RESGATE_APLIC_FINANCEIRA", "RENDIMENTO_APLIC_FINANCEIRA", None]),
), max_size=12)
_valor = st.none() | st.integers(0, 1000).map(float)
_snapshots = st.dictionaries(st.tuples(st.sampled_from(["h1", "h2", "h3"]), _dias),
                             st.tuples(_valor, st.none() | st.integers(-100, 100).map(float)), max_size=10)


def _anomes(d):
    return d.year * 100 + d.month


@settings(max_examples=150, deadline=None)
@given(fluxos=_fluxos, snapshots=_snapshots)
def test_propriedade_investimentos_fluxo_estrutural_e_posicao_do_ultimo_snapshot(fluxos, snapshots):
    pos = _positions([(h, d, 0.0, valor, retorno) for (h, d), (valor, retorno) in snapshots.items()])
    out = investimentos_per_month(_inv_fact(fluxos), pos)
    # Só a linha de investimento que passou pela conta conta; as outras não mudam nada.
    estruturais = [f for f in fluxos if f[3] == "investment" and f[4]]
    assert investimentos_per_month(_inv_fact(estruturais), pos).equals(out)
    assert out["anomes_key"].to_list() == sorted({_anomes(f[0]) for f in estruturais}
                                                 | {_anomes(d) for _, d in snapshots})
    for r in out.iter_rows(named=True):
        do_mes = [f for f in estruturais if _anomes(f[0]) == r["anomes_key"]]
        assert r["aplicado"] == -sum(f[2] for f in do_mes if f[2] < 0)
        assert r["resgatado"] == sum(f[2] for f in do_mes if f[2] > 0 and f[5] == "RESGATE_APLIC_FINANCEIRA")
        assert r["rendimento_conta"] == sum(
            f[2] for f in do_mes if f[2] > 0 and f[5] == "RENDIMENTO_APLIC_FINANCEIRA")
        assert r["liquido"] == r["aplicado"] - r["resgatado"]
        # Posição: o último snapshot de cada aplicação no mês; mês sem snapshot fica nulo, sem carregar o anterior.
        ultimo = {}
        for (h, d), (valor, retorno) in sorted(snapshots.items()):
            if _anomes(d) == r["anomes_key"]:
                ultimo[h] = (valor or 0.0, retorno or 0.0)
        if not ultimo:
            assert (r["posicao"], r["principal"], r["rentabilidade"]) == (None, None, None)
            continue
        assert r["posicao"] == sum(valor for valor, _ in ultimo.values())
        assert r["rentabilidade"] == sum(retorno for _, retorno in ultimo.values())
        assert r["posicao"] == r["principal"] + r["rentabilidade"]
