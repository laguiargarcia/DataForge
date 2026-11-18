"""Roteamento E3/E3a (df-cns etapa 1, Task 2) — casos à mão para cada passo e borda (spec §3.1)."""
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

from classificacao import TIPOS_ENTRADA, TIPOS_SAIDA, classificar

DIC = pl.DataFrame(
    [("Groceries", "Alimentação", "Essencial"),
     ("Contas", "Contas", "Essencial"),
     ("Salario", "Salário", "Salário"),
     ("Pagamento cartão", "Pagamento cartão", "Pagamento cartão"),
     ("Estranho", "Estranho", "Outro"),
     ("SemTipo", "", "")],
    schema={"subcategoria": pl.String, "categoria": pl.String, "Tipo": pl.String}, orient="row",
)

_FACT_SCHEMA = {
    "id": pl.String, "date": pl.Date, "account_type": pl.String, "txn_type": pl.String,
    "description_normalized": pl.String, "category": pl.String, "income_source_key": pl.String,
    "etl_tag": pl.String, "netting_applied": pl.Boolean, "amount_signed": pl.Float64,
    "cash_flag": pl.Boolean,
}

SAIDA = ("BANK", "DEBIT")
ENTRADA = ("BANK", "CREDIT")


def _row(id_, lado, desc="", **campos):
    """campos: category, income_source_key, etl_tag, netting_applied (padrão nulo/False)."""
    account_type, txn_type = lado
    row = {"id": id_, "date": date(2026, 6, 15), "account_type": account_type, "txn_type": txn_type,
           "description_normalized": desc, "category": None, "income_source_key": None,
           "etl_tag": None, "netting_applied": False,
           "amount_signed": 10.0 if lado == ENTRADA else -10.0, "cash_flag": True}
    row.update(campos)
    return row


def _rules(*pairs):
    return pl.DataFrame(list(pairs), schema={"padrao": pl.String, "subcategoria": pl.String}, orient="row")


def _route(row, rules=None):
    rules = _rules() if rules is None else rules
    out = classificar(pl.DataFrame([row], schema=_FACT_SCHEMA), DIC, rules)
    assert out.height == 1
    return tuple(out.select("subcategoria", "categoria", "Tipo", "lado").row(0))


def test_listas_de_tipo():
    assert TIPOS_SAIDA == ("Essencial", "Não Essencial", "Pagamento cartão")
    assert TIPOS_ENTRADA == ("Salário", "Família", "Reembolso", "Venda", "Rendimento", "Outras entradas")


def test_etl_tag_investimento_vence_regra():
    row = _row("1", SAIDA, "APLICACAO MERCADO", etl_tag="investment")
    assert _route(row, _rules(("MERCADO", "Groceries"))) == (
        "Investimentos", "Investimentos", "Investimentos", "neutro")


@pytest.mark.parametrize("tag", ["card_payment_bank", "card_payment_card"])
def test_etl_tag_pagamento_cartao(tag):
    assert _route(_row("1", SAIDA, "PAGTO FATURA", etl_tag=tag)) == (
        "Pagamento cartão", "Pagamento cartão", "Pagamento cartão", "saida")


def test_regra_mais_longa_vence():
    rules = _rules(("UBER", "Transporte"), ("UBER EATS", "Delivery"))
    assert _route(_row("1", SAIDA, "UBER EATS SP"), rules)[0] == "Delivery"


def test_empate_por_subcategoria_ascendente():
    rules = _rules(("ABC", "Zeta"), ("XYZ", "Alfa"))
    assert _route(_row("1", SAIDA, "ABC XYZ"), rules)[0] == "Alfa"


def test_portao_de_direcao_sem_segunda_chance():
    # A vencedora (MERCADO -> saída) é da direção errada para uma entrada: cai no padrão do lado,
    # não tenta a regra mais curta MERC (que seria de entrada).
    rules = _rules(("MERCADO", "Groceries"), ("MERC", "Salario"))
    row = _row("1", ENTRADA, "MERCADO X", income_source_key="Fulano")
    assert _route(row, rules) == ("Fulano", "Fulano", "Outras entradas", "entrada")


def test_alvo_sem_dicionario_vale_nos_dois_lados():
    rules = _rules(("VIACAO FICTICIA", "Onibus"))
    assert _route(_row("1", SAIDA, "VIACAO FICTICIA SP"), rules) == ("Onibus", "Onibus", "Não Essencial", "saida")
    assert _route(_row("2", ENTRADA, "VIACAO FICTICIA SP"), rules) == ("Onibus", "Onibus", "Outras entradas", "entrada")


def test_alvo_com_tipo_fora_das_listas_nao_se_aplica():
    row = _row("1", SAIDA, "ESTRANHO LTDA", category="Shopping")
    assert _route(row, _rules(("ESTR", "Estranho")))[0] == "Shopping"


def test_entrada_sem_chave_de_origem_usa_category():
    assert _route(_row("1", ENTRADA, "TED", category="Transfer"))[0] == "Transfer"


def test_sem_regra_e_sem_category():
    assert _route(_row("1", SAIDA, "ALGO")) == (
        "(sem categoria)", "(sem categoria)", "Não Essencial", "saida")


def test_padrao_vazio_e_ignorado():
    rules = _rules(("", "Contas"), ("   ", "Contas"))
    assert _route(_row("1", SAIDA, "QUALQUER", category="Shopping"), rules)[0] == "Shopping"


def test_padrao_e_literal():
    rules = _rules(("PIX*", "Contas"))
    assert _route(_row("1", SAIDA, "PAG PIX* LOJA"), rules)[0] == "Contas"
    assert _route(_row("2", SAIDA, "PAG PIXX LOJA", category="Shopping"), rules)[0] == "Shopping"


def test_casamento_ignora_maiusculas():
    assert _route(_row("1", SAIDA, "UBER 123"), _rules(("uber", "Transporte")))[0] == "Transporte"


def test_dicionario_com_tipo_e_categoria_vazios():
    assert _route(_row("1", SAIDA, "SEMTIPO"), _rules(("SEMT", "SemTipo"))) == (
        "SemTipo", "SemTipo", "Não Essencial", "saida")
    assert _route(_row("2", ENTRADA, "SEMTIPO"), _rules(("SEMT", "SemTipo")))[2] == "Outras entradas"


def test_regra_de_saida_com_dicionario():
    assert _route(_row("1", SAIDA, "MERCADO X"), _rules(("MERCADO", "Groceries"))) == (
        "Groceries", "Alimentação", "Essencial", "saida")


def test_descricao_nula():
    assert _route(_row("1", SAIDA, None, category="Shopping"), _rules(("X", "Contas")))[0] == "Shopping"


def test_anuladas_saem_e_anomes_key():
    fact = pl.DataFrame([_row("1", SAIDA, "A", category="Shopping"),
                         _row("2", SAIDA, "B", category="Shopping", netting_applied=True)], schema=_FACT_SCHEMA)
    out = classificar(fact, DIC, _rules())
    assert out["id"].to_list() == ["1"]
    assert out["anomes_key"].to_list() == [202606]
    assert out.schema["anomes_key"] == pl.Int32
    assert set(_FACT_SCHEMA) <= set(out.columns)
