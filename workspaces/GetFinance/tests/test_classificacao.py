"""Roteamento E3/E3a (df-cns etapa 1, Task 2) — casos à mão para cada passo e borda (spec §3.1);
correções por transação e efeito de uma regra (df-cns.8.1)."""
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

from classificacao import TIPOS_ENTRADA, TIPOS_SAIDA, classificar, efeito_da_regra, eh_aplicacao_automatica

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


def _corr(*pairs):
    return pl.DataFrame(list(pairs), schema={"transacao_id": pl.String, "subcategoria": pl.String}, orient="row")


def _route(row, rules=None, correcoes=None):
    rules = _rules() if rules is None else rules
    out = classificar(pl.DataFrame([row], schema=_FACT_SCHEMA), DIC, rules, correcoes)
    assert out.height == 1
    return tuple(out.select("subcategoria", "categoria", "Tipo", "lado").row(0))


def test_listas_de_tipo():
    assert TIPOS_SAIDA == ("Essencial", "Não Essencial", "Pagamento cartão")
    assert TIPOS_ENTRADA == ("Salário", "Família", "Reembolso", "Venda", "Rendimento", "Outras entradas")


def test_etl_tag_investimento_vence_regra():
    row = _row("1", SAIDA, "APLICACAO MERCADO", etl_tag="investment")
    assert _route(row, _rules(("MERCADO", "Groceries"))) == (
        "Investimentos", "Investimentos", "Investimentos", "neutro")


@pytest.mark.parametrize("lado", [SAIDA, ENTRADA])
def test_aplicacao_automatica_e_neutra_e_vence_regra(lado):
    # df-7twv.2: movimento conta <-> aplicação automática; nem entrada nem saída, nenhuma regra passa por cima.
    row = _row("1", lado, "APL APLIC AUT FICT", etl_tag="aplicacao_automatica")
    assert _route(row, _rules(("APLIC", "Groceries"))) == (
        "Aplicação automática", "Aplicação automática", "Aplicação automática", "neutro")


def test_correcao_vence_a_marca_de_aplicacao_automatica():
    row = _row("1", SAIDA, "APL APLIC AUT FICT", etl_tag="aplicacao_automatica")
    assert _route(row, correcoes=_corr(("1", "Groceries"))) == ("Groceries", "Alimentação", "Essencial", "saida")


@pytest.mark.parametrize("descricao,casa", [
    ("APL APLIC AUT FICT", True), ("RES APLIC AUT FICT", True), ("Aplicação APL APLIC AUT FICT", True),
    ("Resgate RES APLIC AUT FICT", True), ("Entrada RES APLIC AUT FICT", True),
    ("REND PAGO APLIC AUT FICT", False), ("RESGATE COFRINHO", False), ("XAPL APLIC AUTOMOVEL", False), (None, False),
])
def test_padrao_da_aplicacao_automatica(descricao, casa):
    df = pl.DataFrame({"description": [descricao]}, schema={"description": pl.String})
    assert df.select(eh_aplicacao_automatica())["description"].to_list() == [casa]


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


# --- correções por transação (passo 0) -------------------------------------------------------------

@pytest.mark.parametrize("tag", ["investment", "card_payment_bank", "card_payment_card"])
def test_correcao_vence_etl_tag(tag):
    row = _row("1", SAIDA, "APLICACAO", etl_tag=tag)
    assert _route(row, correcoes=_corr(("1", "Groceries"))) == ("Groceries", "Alimentação", "Essencial", "saida")


def test_correcao_vence_regra_e_padrao_do_lado():
    row = _row("1", ENTRADA, "MERCADO X", income_source_key="Fulano")
    assert _route(row, _rules(("MERCADO", "Groceries")), _corr(("1", "Salario"))) == (
        "Salario", "Salário", "Salário", "entrada")


def test_correcao_sem_dicionario_usa_o_padrao_do_lado():
    assert _route(_row("1", SAIDA, "X"), correcoes=_corr(("1", "Nova"))) == ("Nova", "Nova", "Não Essencial", "saida")


def test_correcao_para_investimentos_fica_neutra():
    assert _route(_row("1", SAIDA, "X"), correcoes=_corr(("1", "Investimentos")))[2:] == ("Investimentos", "neutro")


def test_correcao_so_na_sua_transacao_e_orfa_ignorada():
    fact = pl.DataFrame([_row("1", SAIDA, "A", category="Shopping"), _row("2", SAIDA, "B", category="Shopping")],
                        schema=_FACT_SCHEMA)
    out = classificar(fact, DIC, _rules(), _corr(("2", "Contas"), ("sumiu", "Groceries")))
    assert out.select("id", "subcategoria", "corrigida").rows() == [("1", "Shopping", False), ("2", "Contas", True)]


def test_correcao_de_linha_anulada_nao_traz_a_linha_de_volta():
    fact = pl.DataFrame([_row("1", SAIDA, "A", netting_applied=True)], schema=_FACT_SCHEMA)
    assert classificar(fact, DIC, _rules(), _corr(("1", "Contas"))).height == 0


def test_sem_correcoes_ninguem_corrigido():
    out = classificar(pl.DataFrame([_row("1", SAIDA, "A")], schema=_FACT_SCHEMA), DIC, _rules())
    assert out["corrigida"].to_list() == [False]


# --- efeito_da_regra ------------------------------------------------------------------------------

def _fact(*rows):
    return pl.DataFrame(list(rows), schema=_FACT_SCHEMA)


def test_efeito_regra_mais_longa_tira_a_vitoria():
    fact = _fact(_row("1", SAIDA, "UBER EATS"), _row("2", SAIDA, "UBER X"), _row("3", SAIDA, "TAXI"))
    rules = _rules(("UBER", "Contas"), ("UBER EATS", "Groceries"))
    assert efeito_da_regra(fact, DIC, rules, "UBER") == {"casa": 2, "vence": 1, "barradas": 0}
    assert efeito_da_regra(fact, DIC, rules, "uber eats") == {"casa": 1, "vence": 1, "barradas": 0}


def test_efeito_conta_as_barradas_pela_direcao():
    fact = _fact(_row("1", ENTRADA, "MERCADO REEMBOLSO"), _row("2", SAIDA, "MERCADO X"))
    assert efeito_da_regra(fact, DIC, _rules(("MERCADO", "Groceries")), "MERCADO") == {
        "casa": 2, "vence": 2, "barradas": 1}


def test_efeito_nao_conta_etl_tag_nem_correcao_nem_anulada():
    fact = _fact(_row("1", SAIDA, "MERCADO", etl_tag="investment"), _row("2", SAIDA, "MERCADO"),
                 _row("3", SAIDA, "MERCADO"), _row("4", SAIDA, "MERCADO", netting_applied=True))
    efeito = efeito_da_regra(fact, DIC, _rules(("MERCADO", "Groceries")), "MERCADO", _corr(("2", "Contas")))
    assert efeito == {"casa": 3, "vence": 1, "barradas": 0}


def test_efeito_de_padrao_que_nao_casa():
    assert efeito_da_regra(_fact(_row("1", SAIDA, "A")), DIC, _rules(("ZZZ", "Contas")), "ZZZ") == {
        "casa": 0, "vence": 0, "barradas": 0}


# --- propriedades do roteamento (df-xkhx.4) ----------------------------------------------------
# Linhas, regras e correções sorteadas de um vocabulário pequeno, para que regra, correção, etl_tag e
# padrão do lado disputem a mesma linha.
_SUBS = ["Groceries", "Contas", "Salario", "Estranho", "SemTipo", "Investimentos", "Fora do dicionario"]
_PALAVRAS = ["MERCADO", "mercado sul", "PIX", "Salario", "LUZ", "sul"]
# A maioria das linhas sem etl_tag, para a regra e o portão de direção decidirem na maior parte dos sorteios.
_TAGS = [None, None, None, None, "outra", "investment", "aplicacao_automatica", "card_payment_bank",
         "card_payment_card"]
_SUB_DA_TAG = {"investment": "Investimentos", "aplicacao_automatica": "Aplicação automática",
               "card_payment_bank": "Pagamento cartão", "card_payment_card": "Pagamento cartão"}
_linhas = st.lists(st.fixed_dictionaries({
    "lado": st.sampled_from([SAIDA, SAIDA, ENTRADA, ENTRADA, ("CREDIT", "DEBIT")]),
    "desc": st.none() | st.lists(st.sampled_from(_PALAVRAS), min_size=1, max_size=3).map(" ".join),
    "category": st.sampled_from([None, "", *_SUBS]),
    "income_source_key": st.sampled_from([None, "", *_SUBS]),
    "etl_tag": st.sampled_from(_TAGS),
    "netting_applied": st.sampled_from([False, False, False, None, True]),
}), min_size=1, max_size=8)
_regras_sorteadas = st.lists(st.tuples(st.sampled_from([*_PALAVRAS, "", "  ", None]), st.sampled_from(_SUBS)),
                             max_size=5)
_correcoes_sorteadas = st.dictionaries(st.integers(0, 9).map(str), st.sampled_from(_SUBS), max_size=2)
_TIPO_NO_DIC = {sub: tipo for sub, _, tipo in DIC.rows() if tipo}


def _fact_sorteado(linhas):
    rows = [_row(str(i), linha["lado"], linha["desc"], **{k: v for k, v in linha.items() if k not in ("lado", "desc")})
            for i, linha in enumerate(linhas)]
    return pl.DataFrame(rows, schema=_FACT_SCHEMA)


def _sub_pela_regra_ou_pelo_lado(linha, regras):
    """Passos 2 e 3 escritos linha a linha, como o cabeçalho de classificacao.py os descreve."""
    entrada = linha["lado"] == ENTRADA
    descricao = (linha["desc"] or "").upper()
    casam = [(p, sub) for p, sub in regras if p and p.strip() and p.upper() in descricao]
    if casam:
        # Vence o padrão mais longo (empate: subcategoria ascendente); o portão olha só a vencedora.
        _, sub = min(casam, key=lambda regra: (-len(regra[0]), regra[1]))
        tipo = _TIPO_NO_DIC.get(sub)
        if tipo is None or (tipo in TIPOS_SAIDA and not entrada) or (tipo in TIPOS_ENTRADA and entrada):
            return sub
    if entrada and linha["income_source_key"]:
        return linha["income_source_key"]
    return linha["category"] or "(sem categoria)"


def _classificar(linhas, regras, correcoes):
    return classificar(_fact_sorteado(linhas), DIC, _rules(*regras), _corr(*correcoes.items()))


@settings(max_examples=300, deadline=None)
@given(linhas=_linhas, regras=_regras_sorteadas, correcoes=_correcoes_sorteadas)
def test_propriedade_precedencia_e_lado(linhas, regras, correcoes):
    out = _classificar(linhas, regras, correcoes)
    vivas = [(str(i), linha) for i, linha in enumerate(linhas) if not linha["netting_applied"]]
    # Uma linha por transação viva, na ordem de entrada.
    assert out["id"].to_list() == [id_ for id_, _ in vivas]
    for (id_, linha), o in zip(vivas, out.iter_rows(named=True), strict=True):
        # (0) a correção vence tudo; (1) o etl_tag estrutural vence regra e padrão do lado.
        assert o["corrigida"] == (id_ in correcoes)
        if id_ in correcoes:
            assert o["subcategoria"] == correcoes[id_]
        elif linha["etl_tag"] in _SUB_DA_TAG:
            assert o["subcategoria"] == _SUB_DA_TAG[linha["etl_tag"]]
        else:
            assert o["subcategoria"] == _sub_pela_regra_ou_pelo_lado(linha, regras)
        # O lado é função do Tipo, e o Tipo nunca falta.
        esperado = "entrada" if o["Tipo"] in TIPOS_ENTRADA else "saida" if o["Tipo"] in TIPOS_SAIDA else "neutro"
        assert o["lado"] == esperado
        # (4) só sem Tipo no dicionário (e fora das neutras) a direção decide.
        if o["subcategoria"] in ("Investimentos", "Aplicação automática"):
            assert o["Tipo"] == o["subcategoria"]
        elif o["subcategoria"] in _TIPO_NO_DIC:
            assert o["Tipo"] == _TIPO_NO_DIC[o["subcategoria"]]
        else:
            assert o["Tipo"] == ("Outras entradas" if linha["lado"] == ENTRADA else "Não Essencial")


@settings(max_examples=100, deadline=None)
@given(linhas=_linhas, regras=_regras_sorteadas, correcoes=_correcoes_sorteadas)
def test_propriedade_cada_linha_e_roteada_sozinha(linhas, regras, correcoes):
    # O destino de uma linha não depende das outras linhas do fact.
    fact = _fact_sorteado(linhas)
    juntas = classificar(fact, DIC, _rules(*regras), _corr(*correcoes.items()))
    for o in juntas.iter_rows(named=True):
        sozinha = classificar(fact.filter(pl.col("id") == o["id"]), DIC, _rules(*regras), _corr(*correcoes.items()))
        assert sozinha.to_dicts() == [o]


@settings(max_examples=100, deadline=None)
@given(linhas=_linhas, regras=_regras_sorteadas, correcoes=_correcoes_sorteadas)
def test_propriedade_regra_nao_diferencia_maiusculas_e_regra_que_nao_casa_nao_muda_nada(linhas, regras, correcoes):
    out = _classificar(linhas, regras, correcoes)
    trocadas = [(p and p.swapcase(), sub) for p, sub in regras]
    assert _classificar(linhas, trocadas, correcoes).equals(out)
    assert _classificar(linhas, [*regras, ("PADRAO QUE NENHUMA DESCRICAO TEM", "Contas")], correcoes).equals(out)


@settings(max_examples=150, deadline=None)
@given(linhas=_linhas, regras=_regras_sorteadas, correcoes=_correcoes_sorteadas, data=st.data())
def test_propriedade_efeito_da_regra_conta_o_que_o_roteamento_faz(linhas, regras, correcoes, data):
    validas = [p for p, _ in regras if p and p.strip()]
    assume(validas)
    padrao = data.draw(st.sampled_from(validas))
    efeito = efeito_da_regra(_fact_sorteado(linhas), DIC, _rules(*regras), padrao, _corr(*correcoes.items()))
    assert efeito["casa"] >= efeito["vence"] >= efeito["barradas"] >= 0
    assert efeito["casa"] == sum(1 for linha in linhas
                                 if not linha["netting_applied"] and padrao.upper() in (linha["desc"] or "").upper())
    # Tirar a regra muda no máximo as linhas que ela vencia.
    com = _classificar(linhas, regras, correcoes)
    sem = _classificar(linhas, [(p, s) for p, s in regras if not p or p.upper() != padrao.upper()], correcoes)
    mudou = sum(1 for a, b in zip(com.to_dicts(), sem.to_dicts(), strict=True) if a != b)
    assert mudou <= efeito["vence"]
    if efeito["vence"] == 0:
        assert sem.equals(com)
