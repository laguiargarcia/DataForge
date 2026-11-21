"""classificacao.py — roteamento de categorias do Resumo v3 (spec 2026-07-01 §11 E2/E3/E3a).

Cada transação viva ganha subcategoria -> categoria -> Tipo, e o lado (entrada/saida/neutro).
Precedência (E3):
  0. correção do usuário para aquela transação (tabela correcoes, df-cns.8) — vence tudo, inclusive o
     etl_tag; categoria e Tipo vêm do dicionário como em qualquer subcategoria;
  1. etl_tag estrutural do ETL — nenhuma regra do usuário sobrepõe;
  2. regra do mapeamento: contains sem diferenciar maiúsculas, vence o padrão mais longo
     (empate: subcategoria ascendente). Portão de direção (E3a) só na regra vencedora: alvo de
     saída não captura entrada e vice-versa; nesse caso a linha cai no passo 3, sem tentar a
     segunda regra. Alvo sem Tipo no dicionário vale para os dois lados;
  3. padrão do lado: entrada usa income_source_key, saída (ou entrada sem chave) usa category;
  4. dicionário: categoria e Tipo da subcategoria; sem linha, categoria = subcategoria e Tipo pelo
     lado (o único ponto em que a direção decide).
Funções puras: recebem e devolvem DataFrames, sem I/O. `efeito_da_regra` conta o que uma regra faz,
pelo mesmo roteamento, para a tela de edição avisar regra na direção errada.
"""
import polars as pl

TIPOS_SAIDA = ("Essencial", "Não Essencial", "Pagamento cartão")
TIPOS_ENTRADA = ("Salário", "Família", "Reembolso", "Venda", "Rendimento", "Outras entradas")

SUB_INVESTIMENTOS = "Investimentos"
SUB_PAGAMENTO_CARTAO = "Pagamento cartão"
SUB_SEM_CATEGORIA = "(sem categoria)"
TIPO_PADRAO_ENTRADA = "Outras entradas"
TIPO_PADRAO_SAIDA = "Não Essencial"

TAG_PAGAMENTO_NO_CARTAO = "card_payment_card"     # a perna da fatura no lado do cartão
_TAGS_PAGAMENTO_CARTAO = ("card_payment_bank", TAG_PAGAMENTO_NO_CARTAO)


def _vazio_para_nulo(col: str) -> pl.Expr:
    return pl.when(pl.col(col) == "").then(None).otherwise(pl.col(col)).alias(col)


def _preparar_dicionario(dicionario: pl.DataFrame) -> pl.DataFrame:
    # Primeira linha por subcategoria (o v3 ignorava duplicatas); "" conta como ausente.
    return (
        dicionario.select("subcategoria", "categoria", "Tipo")
        .unique(subset="subcategoria", keep="first", maintain_order=True)
        .with_columns(_vazio_para_nulo("categoria"), _vazio_para_nulo("Tipo"))
    )


def _regras(mapeamento: pl.DataFrame) -> pl.DataFrame:
    return (
        mapeamento.select("padrao", "subcategoria")
        .filter(pl.col("padrao").is_not_null() & (pl.col("padrao").str.strip_chars() != ""))
        .with_columns(
            pl.col("padrao").str.to_uppercase().alias("_pu"),
            pl.col("padrao").str.len_chars().alias("_plen"),
        )
    )


def _casamentos(fact: pl.DataFrame, regras: pl.DataFrame) -> pl.DataFrame:
    """Um par (_row, regra) para cada regra cujo padrão está na descrição."""
    return (
        fact.select("_row", pl.col("description_normalized").fill_null("").str.to_uppercase().alias("_du"))
        .join(regras, how="cross")
        .filter(pl.col("_du").str.contains(pl.col("_pu"), literal=True))
    )


def _regra_vencedora(fact: pl.DataFrame, mapeamento: pl.DataFrame) -> pl.DataFrame:
    """_row -> padrão e subcategoria da regra de padrão mais longo que casa a descrição (ou nada)."""
    return (
        _casamentos(fact, _regras(mapeamento))
        .sort(["_row", "_plen", "subcategoria"], descending=[False, True, False])
        .group_by("_row", maintain_order=True)
        .agg(pl.col("_pu").first().alias("_regra_pu"), pl.col("subcategoria").first().alias("_regra_sub"))
    )


_ENTRADA = (pl.col("account_type") == "BANK") & (pl.col("txn_type") == "CREDIT")
_TAG_ESTRUTURAL = (pl.col("etl_tag") == "investment") | pl.col("etl_tag").is_in(_TAGS_PAGAMENTO_CARTAO)


def _vivas(fact: pl.DataFrame) -> pl.DataFrame:
    return fact.filter(~pl.col("netting_applied").fill_null(False)).with_row_index("_row")


def _com_regra(vivas: pl.DataFrame, dic: pl.DataFrame, mapeamento: pl.DataFrame,
               correcoes: pl.DataFrame | None) -> pl.DataFrame:
    """Passos 0 e 2: _corr_sub (correção), _regra_pu/_regra_sub (regra vencedora) e _regra_vale
    (o portão de direção, E3a, só na regra vencedora: alvo sem Tipo vale para os dois lados)."""
    corr = (pl.DataFrame(schema={"transacao_id": pl.String, "subcategoria": pl.String})
            if correcoes is None else correcoes.select("transacao_id", "subcategoria"))
    vivas = vivas.join(
        corr.rename({"transacao_id": "id", "subcategoria": "_corr_sub"}), on="id", how="left",
    ).join(_regra_vencedora(vivas, mapeamento), on="_row", how="left").join(
        dic.select(pl.col("subcategoria").alias("_regra_sub"), pl.col("Tipo").alias("_regra_tipo")),
        on="_regra_sub", how="left",
    )
    regra_vale = pl.col("_regra_sub").is_not_null() & (
        pl.col("_regra_tipo").is_null()
        | (pl.col("_regra_tipo").is_in(TIPOS_SAIDA) & _ENTRADA.not_())
        | (pl.col("_regra_tipo").is_in(TIPOS_ENTRADA) & _ENTRADA)
    )
    return vivas.with_columns(regra_vale.alias("_regra_vale"))


def classificar(fact: pl.DataFrame, dicionario: pl.DataFrame, mapeamento: pl.DataFrame,
                correcoes: pl.DataFrame | None = None) -> pl.DataFrame:
    """Curated fact_transaction -> linhas vivas (sem netting_applied) com anomes_key, subcategoria,
    categoria, Tipo, lado e corrigida. `correcoes` tem transacao_id e subcategoria; uma correção
    cujo id não está em `fact` não faz nada. Mantém a ordem das linhas de entrada."""
    dic = _preparar_dicionario(dicionario)
    vivas = _com_regra(_vivas(fact), dic, mapeamento, correcoes)
    chave_origem = pl.col("income_source_key").fill_null("")
    category = pl.col("category").fill_null("")
    subcategoria = (
        pl.when(pl.col("_corr_sub").is_not_null()).then(pl.col("_corr_sub"))              # (0)
        .when(pl.col("etl_tag") == "investment").then(pl.lit(SUB_INVESTIMENTOS))          # (1)
        .when(pl.col("etl_tag").is_in(_TAGS_PAGAMENTO_CARTAO)).then(pl.lit(SUB_PAGAMENTO_CARTAO))
        .when(pl.col("_regra_vale")).then(pl.col("_regra_sub"))                           # (2)
        .when(_ENTRADA & (chave_origem != "")).then(chave_origem)                           # (3)
        .when(category != "").then(category)
        .otherwise(pl.lit(SUB_SEM_CATEGORIA))
    )
    vivas = vivas.with_columns(subcategoria.alias("subcategoria")).join(
        dic.select("subcategoria", pl.col("categoria").alias("_d_categoria"), pl.col("Tipo").alias("_d_tipo")),
        on="subcategoria", how="left",
    )

    # (4) dicionário, com o padrão de passagem.
    tipo = (
        pl.when(pl.col("subcategoria") == SUB_INVESTIMENTOS).then(pl.lit(SUB_INVESTIMENTOS))
        .when(pl.col("_d_tipo").is_not_null()).then(pl.col("_d_tipo"))
        .when(_ENTRADA).then(pl.lit(TIPO_PADRAO_ENTRADA))
        .otherwise(pl.lit(TIPO_PADRAO_SAIDA))
    )
    vivas = vivas.with_columns(
        (pl.col("date").dt.year() * 100 + pl.col("date").dt.month()).cast(pl.Int32).alias("anomes_key"),
        pl.coalesce(pl.col("_d_categoria"), pl.col("subcategoria")).alias("categoria"),
        tipo.alias("Tipo"),
    ).with_columns(
        pl.when(pl.col("Tipo").is_in(TIPOS_ENTRADA)).then(pl.lit("entrada"))
        .when(pl.col("Tipo").is_in(TIPOS_SAIDA)).then(pl.lit("saida"))
        .otherwise(pl.lit("neutro")).alias("lado"),
        pl.col("_corr_sub").is_not_null().alias("corrigida"),
    )
    return vivas.sort("_row").drop("_row", "_corr_sub", "_regra_pu", "_regra_sub", "_regra_tipo",
                                   "_regra_vale", "_d_categoria", "_d_tipo")


def efeito_da_regra(fact: pl.DataFrame, dicionario: pl.DataFrame, mapeamento: pl.DataFrame,
                    padrao: str, correcoes: pl.DataFrame | None = None) -> dict[str, int]:
    """O que a regra `padrao` (já em `mapeamento`) faz nas transações vivas de `fact`:
    - casa: quantas têm o padrão na descrição;
    - vence: em quantas delas ela é a regra mais longa e nada vem antes (correção, etl_tag);
    - barradas: das que ela vence, quantas o portão de direção deixa de fora (alvo de saída numa
      entrada ou o contrário): essas continuam na categoria de antes."""
    pu = padrao.upper()
    vivas = _com_regra(_vivas(fact), _preparar_dicionario(dicionario), mapeamento, correcoes)
    casa = _casamentos(vivas, _regras(mapeamento)).filter(pl.col("_pu") == pu)["_row"].n_unique()
    alcancadas = vivas.filter(
        (pl.col("_regra_pu") == pu) & pl.col("_corr_sub").is_null() & ~_TAG_ESTRUTURAL.fill_null(False)
    )
    return {"casa": casa, "vence": alcancadas.height,
            "barradas": alcancadas.filter(~pl.col("_regra_vale")).height}
