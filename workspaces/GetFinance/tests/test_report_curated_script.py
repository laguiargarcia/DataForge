"""A task `report` (report_curated.py) rodada inteira, com um curated inventado (df-qwta).

test_report_scripts roda o mesmo script sobre uma cópia do curated real e por isso só existe na
máquina que tem o dado; aqui nada é real: um mês de banco, um cartão com uma compra parcelada e uma
aplicação, montados à mão. O dicionário e as regras são os CSVs de tests/fixtures.
"""
import sys
from datetime import date
from pathlib import Path

import polars as pl

from tests._delta_util import SCRIPTS, read, run_script, seed

sys.path.insert(0, str(SCRIPTS / "report"))

from saude import saude_por_mes

_FIXTURES = Path(__file__).parent / "fixtures"
_CARGA = ["--dicionario", str(_FIXTURES / "dicionario_v3.csv"), "--mapeamento", str(_FIXTURES / "mapeamento_v3.csv")]
_ARGV = ["--source", "curated", "--target", "curated"]
_SAIDAS = ["report_saude", "report_saude_categoria", "report_parcelas", "report_fatura_itens",
           "report_liquidity", "report_cartao", "report_investimentos"]

_FACT = {"id": pl.String, "date": pl.Date, "account_uuid": pl.String, "account_type": pl.String,
         "txn_type": pl.String, "description_normalized": pl.String, "category": pl.String,
         "income_source_key": pl.String, "etl_tag": pl.String, "operation_type": pl.String,
         "netting_applied": pl.Boolean, "cash_flag": pl.Boolean, "amount": pl.Float64,
         "amount_signed": pl.Float64, "cc_bill_id": pl.String, "cc_installment_number": pl.Int64,
         "cc_total_installments": pl.Int64, "cc_purchase_date": pl.Date}
_SNAP = {"account_uuid": pl.String, "snapshot_date": pl.Date, "account_type": pl.String,
         "balance": pl.Float64, "available": pl.Float64, "card_outstanding": pl.Float64,
         "credit_limit": pl.Float64, "available_credit_limit": pl.Float64}
_FATURA = {"account_uuid": pl.String, "anomes_key": pl.Int64, "statement_id": pl.String,
           "valor_fatura": pl.Float64, "valor_pago": pl.Float64, "valor_pendente": pl.Float64,
           "is_open": pl.Boolean}
_POS = {"holding_uuid": pl.String, "snapshot_date": pl.Date, "invested_amount": pl.Float64,
        "current_value": pl.Float64, "return": pl.Float64}
_HOLD = {"holding_uuid": pl.String, "due_date": pl.Date}


def _lanc(id_, conta, tipo, desc, valor, **campos):
    """valor com sinal (negativo = saída). campos: etl_tag, operation_type, cc_* e category."""
    row = {"id": id_, "date": date(2026, 6, 10), "account_uuid": conta,
           "account_type": "CREDIT" if conta == "cartao" else "BANK", "txn_type": tipo,
           "description_normalized": desc, "category": None, "income_source_key": None, "etl_tag": None,
           "operation_type": None, "netting_applied": False, "cash_flag": conta != "cartao",
           "amount": abs(valor), "amount_signed": valor, "cc_bill_id": None, "cc_installment_number": None,
           "cc_total_installments": None, "cc_purchase_date": None}
    row.update(campos)
    return row


def _curated(tmp_path):
    parcela = {"cc_bill_id": "fatura-06", "cc_installment_number": 1, "cc_total_installments": 3,
               "cc_purchase_date": date(2026, 6, 10)}
    tabelas = {
        "fact_transaction": pl.DataFrame([
            _lanc("1", "banco", "CREDIT", "empresa inventada", 1000.0),
            _lanc("2", "banco", "DEBIT", "padaria inventada", -50.0, category="Groceries"),
            _lanc("3", "banco", "DEBIT", "aplicacao inventada", -200.0, etl_tag="investment",
                  operation_type="APLIC_FINANCEIRA"),
            _lanc("4", "cartao", "DEBIT", "loja inventada 01/03", -90.0, **parcela),
        ], schema=_FACT),
        "fact_balance_snapshot": pl.DataFrame([
            ("banco", date(2026, 6, 30), "BANK", 750.0, 750.0, None, None, None),
            ("cartao", date(2026, 6, 30), "CREDIT", None, None, 90.0, 1000.0, 910.0),
        ], schema=_SNAP, orient="row"),
        "fact_fatura_mes": pl.DataFrame([("cartao", 202606, "fatura-06", 90.0, 0.0, 90.0, True)],
                                        schema=_FATURA, orient="row"),
        "fact_investment_position": pl.DataFrame([("cdb", date(2026, 6, 30), 200.0, 202.0, 2.0)],
                                                 schema=_POS, orient="row"),
        "dim_investment": pl.DataFrame([("cdb", None)], schema=_HOLD, orient="row"),
    }
    for tabela, df in tabelas.items():
        seed(tmp_path, "curated", tabela, df)


def test_o_report_grava_as_tabelas_do_resumo_sobre_um_curated_inventado(tmp_path, capsys):
    _curated(tmp_path)
    run_script("report", "carga_inicial_edicao", _CARGA, tmp_path)
    run_script("report", "classify", _ARGV, tmp_path)
    capsys.readouterr()

    run_script("report", "report_curated", _ARGV, tmp_path)

    classificada = read(tmp_path, "curated", "fact_transaction_classified")
    assert read(tmp_path, "curated", "report_saude").equals(saude_por_mes(classificada))
    parcelas = read(tmp_path, "curated", "report_parcelas")
    assert parcelas.select("pagas", "restam").rows() == [("1/3", 2)]
    assert read(tmp_path, "curated", "report_fatura_itens")["valor"].to_list() == [90.0]
    junho = read(tmp_path, "curated", "report_investimentos").filter(pl.col("anomes_key") == 202606)
    assert junho["posicao"].to_list() == [202.0]
    saida = capsys.readouterr().out
    assert all(f"{t}: " in saida for t in _SAIDAS)
    assert saida.strip().endswith("último mês: 202606")
