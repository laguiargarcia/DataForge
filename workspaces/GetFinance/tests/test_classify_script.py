"""As tasks `seed_edicao` (carga_inicial_edicao.py) e `classify` (classify.py) rodadas inteiras, com
lançamentos inventados (df-g4z7).

test_report_scripts roda os mesmos scripts sobre uma cópia do curated real e por isso só existe na
máquina que tem o dado; aqui nada é real: o fact_transaction é montado à mão e o dicionário e as
regras são os CSVs de tests/fixtures.
"""
import sys
from contextlib import closing
from pathlib import Path

import polars as pl
import pytest

from tests._delta_util import SCRIPTS, read, run_script, seed
from tests.test_classificacao import _FACT_SCHEMA, ENTRADA, SAIDA, _row

sys.path.insert(0, str(SCRIPTS / "report"))

from edicao import abrir, ler_dicionario

_FIXTURES = Path(__file__).parent / "fixtures"
_CARGA = ["--dicionario", str(_FIXTURES / "dicionario_v3.csv"), "--mapeamento", str(_FIXTURES / "mapeamento_v3.csv")]
_ARGV = ["--source", "curated", "--target", "curated"]


def _fact(tmp_path):
    seed(tmp_path, "curated", "fact_transaction", pl.DataFrame(
        [_row("1", SAIDA, "padaria inventada", category="Groceries"),
         _row("2", ENTRADA, "empresa inventada"),
         _row("3", SAIDA, "compensada", netting_applied=True)],
        schema=_FACT_SCHEMA))


def test_a_carga_inicial_e_a_classificacao_rodam_sobre_lancamentos_inventados(tmp_path):
    _fact(tmp_path)

    run_script("report", "carga_inicial_edicao", _CARGA, tmp_path)
    run_script("report", "classify", _ARGV, tmp_path)

    with closing(abrir(tmp_path / "edicao")) as conn:
        assert ler_dicionario(conn).height > 0
    classificada = read(tmp_path, "curated", "fact_transaction_classified")
    assert sorted(classificada["id"]) == ["1", "2"]   # a linha compensada (netting_applied) não entra
    assert classificada["subcategoria"].null_count() == 0
    assert read(tmp_path, "curated", "edicao_aplicada")["versao"].to_list() == [0]


def test_classify_sem_a_carga_inicial_falha_dizendo_o_que_rodar(tmp_path):
    """E fecha o banco de edição: a conexão aberta numa falha é o que a regra de vazamento acusa."""
    _fact(tmp_path)

    with pytest.raises(RuntimeError, match="carga_inicial_edicao"):
        run_script("report", "classify", _ARGV, tmp_path)

    assert not (tmp_path / "curated" / "fact_transaction_classified").exists()
