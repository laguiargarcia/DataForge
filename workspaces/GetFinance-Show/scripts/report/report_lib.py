"""report_lib.py — Pure Polars helpers for GetFinance report v2 (df-dgu).

Task 1 (df-u6r): liquidity_per_month — semi-additive liquidity aggregate per month.
"""

import polars as pl

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

_OUTPUT_SCHEMA = {
    "anomes_key": pl.Int32,
    "mes_ano": pl.String,
    "cash_disponivel": pl.Float64,
    "card_outstanding": pl.Float64,
    "saldo_disponivel": pl.Float64,
    "saidas_conta": pl.Float64,
    # caixa_saldo appends AFTER the v2 columns: Excel Csv.Document may pin Columns=<count at
    # authoring>, so a mid-table insert would displace the v2 columns out of the read window.
    "caixa_saldo": pl.Float64,
}


def _empty_result() -> pl.DataFrame:
    return pl.DataFrame({col: pl.Series([], dtype=dtype) for col, dtype in _OUTPUT_SCHEMA.items()})


def liquidity_per_month(snap: pl.DataFrame, fact: pl.DataFrame) -> pl.DataFrame:
    """Return one row per snapshot month with semi-additive liquidity figures.

    Args:
        snap: fact_balance_snapshot rows. Required columns:
              account_uuid (Utf8), snapshot_date (Date), account_type (Utf8),
              available (Float64, nullable), balance (Float64, nullable),
              card_outstanding (Float64, nullable).
        fact: fact_transaction rows. Required columns:
              date (Date), account_type (Utf8), amount_signed (Float64).

    Returns:
        DataFrame with columns: anomes_key, mes_ano, cash_disponivel,
        card_outstanding, saldo_disponivel, saidas_conta, caixa_saldo (appended, v3).
        One row per snapshot month, sorted by anomes_key ascending.
    """
    if snap.is_empty():
        return _empty_result()

    # --- Step 1: derive snapshot month key ---
    snap = snap.with_columns(
        (
            pl.col("snapshot_date").dt.year() * 100 + pl.col("snapshot_date").dt.month()
        ).cast(pl.Int32).alias("anomes_key")
    )

    # --- Step 2: latest snapshot per (anomes_key, account_uuid) ---
    latest_dates = (
        snap
        .group_by(["anomes_key", "account_uuid"])
        .agg(pl.col("snapshot_date").max().alias("latest_date"))
    )
    snap_latest = (
        snap
        .join(latest_dates, on=["anomes_key", "account_uuid"], how="inner")
        .filter(pl.col("snapshot_date") == pl.col("latest_date"))
        .drop("latest_date")
    )

    # --- Step 3: aggregate cash and card per month ---
    bank = (
        snap_latest
        .filter(pl.col("account_type") == "BANK")
        .group_by("anomes_key")
        .agg(
            pl.col("available").fill_null(0.0).sum().alias("cash_disponivel"),
            pl.col("balance").fill_null(0.0).sum().alias("caixa_saldo"),
        )
    )
    card = (
        snap_latest
        .filter(pl.col("account_type") == "CREDIT")
        .group_by("anomes_key")
        .agg(
            pl.col("card_outstanding").fill_null(0.0).sum().alias("card_outstanding")
        )
    )

    # All snapshot months (union of BANK + CREDIT months, driven by snap)
    all_months = snap_latest.select("anomes_key").unique()

    liquidity = (
        all_months
        .join(bank, on="anomes_key", how="left")
        .join(card, on="anomes_key", how="left")
        .with_columns([
            pl.col("cash_disponivel").fill_null(0.0),
            pl.col("caixa_saldo").fill_null(0.0),
            pl.col("card_outstanding").fill_null(0.0),
        ])
        .with_columns(
            (pl.col("cash_disponivel") - pl.col("card_outstanding")).alias("saldo_disponivel")
        )
    )

    # --- Step 4: saidas_conta — BANK outflows per month from fact ---
    outflows = (
        fact
        .filter(
            (pl.col("account_type") == "BANK") & (pl.col("amount_signed") < 0)
        )
        .with_columns(
            (
                pl.col("date").dt.year() * 100 + pl.col("date").dt.month()
            ).cast(pl.Int32).alias("anomes_key")
        )
        .group_by("anomes_key")
        .agg(
            (-pl.col("amount_signed").sum()).alias("saidas_conta")
        )
    )

    # --- Step 5: left-join outflows onto snapshot months ---
    result = (
        liquidity
        .join(outflows, on="anomes_key", how="left")
        .with_columns(pl.col("saidas_conta").fill_null(0.0))
    )

    # --- Step 6: derive mes_ano label ---
    result = result.with_columns(
        pl.col("anomes_key").map_elements(
            lambda k: f"{MESES[(k % 100) - 1]}/{k // 100}",
            return_dtype=pl.String,
        ).alias("mes_ano")
    )

    # --- Step 7: final column order, sort ---
    return (
        result
        .select(["anomes_key", "mes_ano", "cash_disponivel", "card_outstanding",
                 "saldo_disponivel", "saidas_conta", "caixa_saldo"])
        .sort("anomes_key")
    )


_CARTAO_SCHEMA = {
    "anomes_key": pl.Int32,
    "mes_ano": pl.String,
    "limite": pl.Float64,
    "saldo_total": pl.Float64,
    "disponivel_limite": pl.Float64,
    "proxima_fatura": pl.Float64,
    "comprometido_parcelas": pl.Float64,
}


def _mes_ano_expr():
    return pl.col("anomes_key").map_elements(
        lambda k: f"{MESES[(k % 100) - 1]}/{k // 100}", return_dtype=pl.String
    ).alias("mes_ano")


def _anomes_expr(col):
    return (pl.col(col).dt.year() * 100 + pl.col(col).dt.month()).cast(pl.Int32).alias("anomes_key")


def _proxima_fatura(fatura: pl.DataFrame, anomes: int) -> float:
    """Σ valor_pendente of the smallest anomes_key >= anomes whose pendente-sum > 0 (E5).

    fact_fatura_mes carries CURRENT payment state, so this is exact for the current month and
    degrades gracefully for past months (already-settled bills contribute 0).
    """
    if fatura.is_empty():
        return 0.0
    pend = (
        fatura.filter(pl.col("anomes_key") >= anomes)
        .group_by("anomes_key")
        .agg(pl.col("valor_pendente").fill_null(0.0).sum().alias("pend"))
        .filter(pl.col("pend") > 0.0)
        .sort("anomes_key")
    )
    return float(pend["pend"][0]) if pend.height else 0.0


_SUFIXO_PARCELA = len("NN/NN")   # "04/12" no fim da descrição de uma parcela


def _strip_parcela_suffix(s: str | None) -> str:
    """Strip a trailing 'NN/NN' installment marker from a description (mirrors the workbook's
    StripTail). 'ANUIDADE DIFERENCI04/12' -> 'ANUIDADE DIFERENCI'."""
    if s is None:
        return ""
    t = s.rstrip()
    if len(t) >= _SUFIXO_PARCELA:
        tail = t[-_SUFIXO_PARCELA:]
        if tail[2] == "/" and tail[:2].isdigit() and tail[3:].isdigit():
            return t[:-_SUFIXO_PARCELA].rstrip()
    return t


def _comprometido_parcelas(fact: pl.DataFrame, anomes: int, fatura: pl.DataFrame | None = None) -> float:
    """Remaining-installment projection as-of month `anomes` (spec §4 Cartão; df-668.3).

    Series key: (account_uuid, stripped description, cc_purchase_date, n, ANCHOR) where
    anchor = bill-month-index − k (falling back to txn-date-month − k when the bill is unknown).
    The anchor is constant per series and is the only reliable discriminator for INTERLEAVED
    same-merchant series (QA/Inter, 2026-07-02: two same-description Amazon 3× purchases with
    NULL cc_purchase_date and per-parcela centavo drift — amount can't key, description alone
    merges them and hides the newer series' remaining installments). `fatura` maps
    statement_id → due-month for the bill anchor; without it every row uses the date fallback.
    Per-installment value = the latest observed amount. A series with ANY netted row is VOIDED
    entirely (df-0fc: the estorno cancels the whole future series).
    """
    if fact.is_empty():
        return 0.0
    obs = fact.filter(
        (pl.col("account_type") == "CREDIT")
        & (pl.col("cc_total_installments") > 1)
        & pl.col("cc_installment_number").is_not_null()
    ).with_columns(_anomes_expr("date").alias("_m"))
    obs = obs.filter(pl.col("_m") <= anomes)
    if obs.is_empty():
        return 0.0
    if fatura is not None and not fatura.is_empty() and "statement_id" in fatura.columns:
        bill_months = fatura.select(
            pl.col("statement_id").alias("cc_bill_id"),
            pl.col("anomes_key").cast(pl.Int64).alias("_bill_anomes"),
        ).drop_nulls("cc_bill_id").unique(subset="cc_bill_id")
        obs = obs.join(bill_months, on="cc_bill_id", how="left")
    else:
        obs = obs.with_columns(pl.lit(None, dtype=pl.Int64).alias("_bill_anomes"))
    _midx = lambda c: (pl.col(c) // 100) * 12 + (pl.col(c) % 100)
    obs = obs.with_columns(
        pl.col("description_normalized").map_elements(
            lambda s: _strip_parcela_suffix(s).upper(), return_dtype=pl.String
        ).alias("_serie"),
        (pl.coalesce(_midx("_bill_anomes"), _midx("_m"))
         - pl.col("cc_installment_number")).alias("_anchor"),
    )
    series = obs.group_by(
        ["account_uuid", "_serie", "cc_purchase_date", "cc_total_installments", "_anchor"]
    ).agg(
        pl.col("cc_installment_number").max().alias("k_max"),
        pl.col("netting_applied").fill_null(False).any().alias("netted"),
        pl.col("amount").sort_by("date").last().alias("valor_parcela"),
    )
    active = series.filter(
        ~pl.col("netted") & (pl.col("k_max") < pl.col("cc_total_installments"))
    )
    if active.is_empty():
        return 0.0
    remaining = (active["cc_total_installments"] - active["k_max"]) * active["valor_parcela"]
    return float(remaining.sum())


def cartao_per_month(snap: pl.DataFrame, fatura: pl.DataFrame, fact: pl.DataFrame) -> pl.DataFrame:
    """One row per CREDIT-snapshot month: limite/saldo/disponível (semi-additive, latest snapshot
    per account per month) + próxima fatura + comprometido em parcelas. Spec §11 E5/E6."""
    if snap.is_empty():
        return pl.DataFrame({c: pl.Series([], dtype=t) for c, t in _CARTAO_SCHEMA.items()})
    credit = snap.filter(pl.col("account_type") == "CREDIT").with_columns(
        _anomes_expr("snapshot_date")
    )
    if credit.is_empty():
        return pl.DataFrame({c: pl.Series([], dtype=t) for c, t in _CARTAO_SCHEMA.items()})
    latest = (
        credit.group_by(["anomes_key", "account_uuid"])
        .agg(pl.col("snapshot_date").max().alias("latest_date"))
    )
    credit_latest = (
        credit.join(latest, on=["anomes_key", "account_uuid"], how="inner")
        .filter(pl.col("snapshot_date") == pl.col("latest_date"))
    )
    agg = credit_latest.group_by("anomes_key").agg(
        pl.col("credit_limit").fill_null(0.0).sum().alias("limite"),
        pl.col("card_outstanding").fill_null(0.0).sum().alias("saldo_total"),
        pl.col("available_credit_limit").fill_null(0.0).sum().alias("disponivel_limite"),
    )
    rows = []
    for r in agg.sort("anomes_key").iter_rows(named=True):
        rows.append((
            r["anomes_key"],
            f"{MESES[(r['anomes_key'] % 100) - 1]}/{r['anomes_key'] // 100}",
            r["limite"], r["saldo_total"], r["disponivel_limite"],
            _proxima_fatura(fatura, r["anomes_key"]),
            _comprometido_parcelas(fact, r["anomes_key"], fatura),
        ))
    return pl.DataFrame(rows, schema=_CARTAO_SCHEMA, orient="row")


_INVEST_SCHEMA = {
    "anomes_key": pl.Int32,
    "mes_ano": pl.String,
    "aplicado": pl.Float64,
    "resgatado": pl.Float64,
    "rendimento_conta": pl.Float64,
    "liquido": pl.Float64,
    "posicao": pl.Float64,
    "principal": pl.Float64,
    "rentabilidade": pl.Float64,
}


def investimentos_per_month(fact: pl.DataFrame, positions: pl.DataFrame) -> pl.DataFrame:
    """Per-month investment flows (structural: etl_tag + cash_flag, E2b/E4) + semi-additive
    position figures (latest snapshot per holding per month). Position columns are NULL on
    months without a snapshot — no fabricated carry-forward."""
    flows_agg = None
    if not fact.is_empty():
        flows = fact.filter(
            (pl.col("etl_tag") == "investment") & pl.col("cash_flag").fill_null(False)
        ).with_columns(_anomes_expr("date"))
        if not flows.is_empty():
            flows_agg = flows.group_by("anomes_key").agg(
                (-pl.col("amount_signed").clip(upper_bound=0.0).sum()).alias("aplicado"),
                pl.col("amount_signed").filter(
                    (pl.col("amount_signed") > 0)
                    & (pl.col("operation_type") == "RESGATE_APLIC_FINANCEIRA")
                ).sum().fill_null(0.0).alias("resgatado"),
                pl.col("amount_signed").filter(
                    (pl.col("amount_signed") > 0)
                    & (pl.col("operation_type") == "RENDIMENTO_APLIC_FINANCEIRA")
                ).sum().fill_null(0.0).alias("rendimento_conta"),
            )

    pos_agg = None
    if not positions.is_empty():
        pos = positions.with_columns(_anomes_expr("snapshot_date"))
        latest = pos.group_by(["anomes_key", "holding_uuid"]).agg(
            pl.col("snapshot_date").max().alias("latest_date")
        )
        pos_latest = pos.join(latest, on=["anomes_key", "holding_uuid"], how="inner").filter(
            pl.col("snapshot_date") == pl.col("latest_date")
        )
        # principal = posição − rentabilidade (df-9uv), NOT Σ invested_amount: on Inter,
        # invested_amount is gross-ever-deposited (partial withdrawals never subtract), so the
        # raw sum overstates the capital actually still invested. Derived this way the panel
        # identity posicao == principal + rentabilidade holds by construction.
        pos_agg = pos_latest.group_by("anomes_key").agg(
            pl.col("current_value").fill_null(0.0).sum().alias("posicao"),
            (pl.col("current_value").fill_null(0.0) - pl.col("return").fill_null(0.0))
            .sum().alias("principal"),
            pl.col("return").fill_null(0.0).sum().alias("rentabilidade"),
        )

    if flows_agg is None and pos_agg is None:
        return pl.DataFrame({c: pl.Series([], dtype=t) for c, t in _INVEST_SCHEMA.items()})

    frames = [f.select("anomes_key") for f in (flows_agg, pos_agg) if f is not None]
    months = pl.concat(frames).unique()
    out = months
    if flows_agg is not None:
        out = out.join(flows_agg, on="anomes_key", how="left")
    else:
        out = out.with_columns([pl.lit(0.0).alias(c) for c in ("aplicado", "resgatado", "rendimento_conta")])
    out = out.with_columns(
        [pl.col(c).fill_null(0.0) for c in ("aplicado", "resgatado", "rendimento_conta")]
    ).with_columns((pl.col("aplicado") - pl.col("resgatado")).alias("liquido"))
    if pos_agg is not None:
        out = out.join(pos_agg, on="anomes_key", how="left")   # nulls stay null (semi-additive)
    else:
        out = out.with_columns(
            [pl.lit(None, dtype=pl.Float64).alias(c) for c in ("posicao", "principal", "rentabilidade")]
        )
    return (
        out.with_columns(_mes_ano_expr())
        .select(list(_INVEST_SCHEMA))
        .sort("anomes_key")
    )


def extend_liquidity_v3(liq: pl.DataFrame, cartao: pl.DataFrame, invest: pl.DataFrame) -> pl.DataFrame:
    """v3 Tier-3 columns (spec §4, E6). Additive: existing columns keep their order.

    patrimonio = caixa_saldo + investimentos_posicao − card_outstanding; NULL when the position
    is unknown for the month (a fabricated 0 would misstate net worth).
    """
    return (
        liq
        .join(cartao.select("anomes_key", "proxima_fatura"), on="anomes_key", how="left")
        .join(
            invest.select("anomes_key", pl.col("posicao").alias("investimentos_posicao")),
            on="anomes_key", how="left",
        )
        .with_columns(pl.col("proxima_fatura").fill_null(0.0))
        .with_columns(
            (pl.col("caixa_saldo") - pl.col("proxima_fatura")).alias("disponivel_gastar"),
            (pl.col("caixa_saldo") + pl.col("investimentos_posicao")
             - pl.col("card_outstanding")).alias("patrimonio"),
        )
    )
