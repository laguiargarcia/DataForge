"""Propriedades do roteamento de categorias (df-xkhx.4): a precedência dos passos contra um modelo linha
a linha, cada linha roteada sozinha, regra sem diferenciar maiúsculas e o efeito de uma regra.

Ficam fora dos alvos de mutação do testloop.toml (df-2edm): cada exemplo roda o polars, e centenas de
exemplos por mutante estouravam o orçamento do alvo (910 s contra 37 s só com os testes de exemplo).
"""
import sys

import polars as pl
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tests import _delta_util

sys.path.insert(0, str(_delta_util.SCRIPTS / "report"))

from classificacao import TIPOS_ENTRADA, TIPOS_SAIDA, classificar, efeito_da_regra
from tests.test_classificacao import _FACT_SCHEMA, DIC, ENTRADA, SAIDA, _corr, _row, _rules

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
