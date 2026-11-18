import polars as pl
import yaml
from pathlib import Path

from tests import _delta_util


def _seed_cleansed_investments(tmp_path: Path, rows: list[dict]) -> None:
    """Create cleansed.investments Delta table in U5's output shape."""
    df = pl.DataFrame(
        {
            "id": [r.get("id") for r in rows],
            "itemId": [r.get("itemId") for r in rows],
            "name": [r.get("name") for r in rows],
            "type": [r.get("type") for r in rows],
            "subtype": [r.get("subtype") for r in rows],
            "issuer": [r.get("issuer") for r in rows],
            "institution": [r.get("institution") for r in rows],
            "currency": [r.get("currency", "BRL") for r in rows],
            "balance": [r.get("balance") for r in rows],
            "amountOriginal": [r.get("amountOriginal") for r in rows],
            "ingested_at": [r.get("ingested_at", "2026-01-01T00:00:00Z") for r in rows],
        },
        schema={
            "id": pl.Utf8, "itemId": pl.Utf8, "name": pl.Utf8, "type": pl.Utf8,
            "subtype": pl.Utf8, "issuer": pl.Utf8, "institution": pl.Utf8,
            "currency": pl.Utf8, "balance": pl.Float64, "amountOriginal": pl.Float64,
            "ingested_at": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "cleansed", "investments", df)


def _seed_empty_cleansed(tmp_path: Path) -> None:
    """cleansed.investments present but empty (U5's empty-seam output)."""
    _seed_cleansed_investments(tmp_path, [])


def _seed_institution_via_accounts(tmp_path: Path, institutions: dict) -> None:
    # df-6j6: accounts.yaml is deleted; the itemId->institution map is now DERIVED by joining
    # cleansed.accounts (id, itemId) to curated.dim_account (pluggy_account_id, institution).
    # Seed one synthetic bank account per item so the join resolves the institution.
    items = list(institutions.keys())
    acc = pl.DataFrame(
        {"id": [f"acc-{i}" for i in range(len(items))], "itemId": items},
        schema={"id": pl.Utf8, "itemId": pl.Utf8},
    )
    # cleansed.accounts carries the full schema; only id/itemId matter for the institution join.
    for c in ("type", "subtype", "number", "transfer_number", "name", "currency",
              "creditData", "ingested_at"):
        acc = acc.with_columns(pl.lit(None, dtype=pl.Utf8).alias(c))
    acc = acc.with_columns(pl.lit(None, dtype=pl.Float64).alias("balance"))
    _delta_util.seed(tmp_path, "cleansed", "accounts", acc)

    dim = pl.DataFrame(
        {
            "account_uuid": [f"u-{i}" for i in range(len(items))],
            "pluggy_account_id": [f"acc-{i}" for i in range(len(items))],
            "name": ["n" for _ in items],
            "institution": [institutions[it] for it in items],
            "type": ["BANK" for _ in items],
            "currency": ["BRL" for _ in items],
        },
        schema={"account_uuid": pl.Utf8, "pluggy_account_id": pl.Utf8, "name": pl.Utf8,
                "institution": pl.Utf8, "type": pl.Utf8, "currency": pl.Utf8},
    )
    _delta_util.seed(tmp_path, "curated", "dim_account", dim)


def _write_investments_config(tmp_path: Path, holdings: dict) -> Path:
    # investments.yaml: pluggy_holding_id -> {holding_uuid, alias}. Source of truth for the
    # holding_uuid surrogate (mirrors how U3 owns account_uuid).
    cfg = tmp_path / "investments.yaml"
    cfg.write_text(yaml.safe_dump({"holdings": holdings}))
    return cfg


def _run(tmp_path: Path, inv_cfg: Path) -> None:
    _delta_util.run_script("cleansed2curated", "cleansed2curated_investments",
                           ["--source", "cleansed", "--target", "curated",
                            "--investments-config", str(inv_cfg)],
                           tmp_path)


def _read_dim(tmp_path: Path) -> list[dict]:
    return _delta_util.read(tmp_path, "curated", "dim_investment").sort("holding_uuid").to_dicts()


def test_holding_uuid_comes_from_yaml_map(tmp_path):
    # holding_uuid is the YAML-controlled surrogate slug (A1, mirrors U3's account_uuid) — it
    # equals the investments.yaml value for the holding's pluggy_holding_id, NOT a hash of `id`.
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-A", "itemId": "item-ficticio", "type": "FIXED_INCOME", "subtype": "CDB",
         "name": "CDB A"},
    ])
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={
        "inv-A": {"holding_uuid": "ficticio-cdb-2031-03", "alias": "CDB A"},
    })
    _run(tmp_path, inv_cfg)
    r = _read_dim(tmp_path)[0]
    assert r["holding_uuid"] == "ficticio-cdb-2031-03", "holding_uuid must be the YAML map slug"
    assert r["pluggy_holding_id"] == "inv-A", "pluggy id is the current-link column"


def test_reconnect_new_pluggy_id_same_holding_uuid(tmp_path):
    # Reconnect-stability (the whole point of A1): the SAME real holding arriving under a NEW
    # pluggy_holding_id, repointed in investments.yaml to the SAME holding_uuid, yields the same
    # surrogate — so snapshot history does not split. A hash of `id` could never do this.
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})

    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-OLD", "itemId": "item-ficticio", "type": "FIXED_INCOME", "name": "CDB"},
    ])
    inv_old = _write_investments_config(tmp_path, holdings={
        "inv-OLD": {"holding_uuid": "ficticio-cdb-2031-03", "alias": "CDB"},
    })
    _run(tmp_path, inv_old)
    uuid_before = _read_dim(tmp_path)[0]["holding_uuid"]

    # Reconnect: Pluggy mints a new id; user repoints the new id to the same surrogate. Cleansed
    # is SCD-1 drop/rebuild upstream, so the new fetch replaces the old (overwrite re-seed).
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-NEW", "itemId": "item-ficticio", "type": "FIXED_INCOME", "name": "CDB"},
    ])
    inv_new = _write_investments_config(tmp_path, holdings={
        "inv-NEW": {"holding_uuid": "ficticio-cdb-2031-03", "alias": "CDB"},
    })
    _run(tmp_path, inv_new)
    after = _read_dim(tmp_path)[0]

    assert after["holding_uuid"] == uuid_before == "ficticio-cdb-2031-03", \
        "repointed new pluggy id must map to the same holding_uuid (reconnect-stable)"
    assert after["pluggy_holding_id"] == "inv-NEW", "current link follows the new pluggy id"


def test_itemId_resolves_to_institution(tmp_path):
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-1", "itemId": "item-ficticio", "type": "EQUITY", "name": "FICT3",
         "institution": "B3"},
    ])
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={
        "inv-1": {"holding_uuid": "ficticio-fict3", "alias": "FICT3"},
    })
    _run(tmp_path, inv_cfg)
    assert _read_dim(tmp_path)[0]["institution"] == "Banco Fictício"


def test_unmapped_holding_quarantined(tmp_path):
    # A holding whose pluggy_holding_id is absent from investments.yaml's holdings: map is
    # quarantined (holding-grain), never emitted to dim_investment (A1: unknowns surfaced, never
    # auto-resolved). Mirrors U3's accounts_unmapped LEFT-JOIN-NULL on the natural key.
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-known", "itemId": "item-ficticio", "type": "EQUITY", "name": "Known"},
        {"id": "inv-stranger", "itemId": "item-ficticio", "type": "EQUITY", "name": "Mystery"},
    ])
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={
        "inv-known": {"holding_uuid": "ficticio-known", "alias": "Known"},
    })
    _run(tmp_path, inv_cfg)

    dim = _read_dim(tmp_path)
    assert [r["pluggy_holding_id"] for r in dim] == ["inv-known"], "only mapped holdings in dim"

    unmapped = _delta_util.read(tmp_path, "cleansed", "investments_unmapped")["pluggy_holding_id"].to_list()
    assert unmapped == ["inv-stranger"], "unmapped holding must be quarantined"


def test_mapped_but_unresolvable_itemId_fails_loud(tmp_path):
    # A holding whose Pluggy item has NO bank/credit account in dim_account cannot derive an
    # institution (no row in the derived itemId->institution map). The script must fail loud
    # (raise SystemExit) before writing, leaving no half-resolved dim_investment — mirrors U3's
    # fail-loud on a mapped account whose itemId yields a NULL institution. run_script swallows
    # the SystemExit, so we assert the table was never written.
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-1", "itemId": "item-broken", "type": "FIXED_INCOME", "name": "X"},
    ])
    # account layers know item-ficticio only — item-broken has no account, so it resolves to NULL.
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={
        "inv-1": {"holding_uuid": "x-1", "alias": "X"},  # holding IS mapped; institution is not
    })
    _run(tmp_path, inv_cfg)  # guard raises SystemExit; run_script swallows it, no dim written
    assert not (tmp_path / "curated" / "dim_investment" / "_delta_log").exists(), \
        "holding whose item has no derivable institution must be rejected, not written half-resolved"


def test_fixed_income_null_institution_filled_from_config(tmp_path):
    # Descriptive attributes carried through; for a FIXED_INCOME row where Pluggy leaves
    # institution NULL, the config-resolved institution must be present (A10/A1).
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "itemId": "item-ficticio", "type": "FIXED_INCOME", "subtype": "CDB",
         "name": "CDB - BANCO EXEMPLO", "issuer": "BANCO FICTICIO S.A.", "institution": None},
    ])
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={
        "inv-cdb": {"holding_uuid": "ficticio-cdb-2031-03", "alias": "CDB Banco Fictício"},
    })
    _run(tmp_path, inv_cfg)
    r = _read_dim(tmp_path)[0]
    assert r["name"] == "CDB Banco Fictício", "descriptive name reflects the friendly alias (COALESCE alias, raw name)"
    assert r["type"] == "FIXED_INCOME"
    assert r["issuer"] == "BANCO FICTICIO S.A."
    assert r["institution"] == "Banco Fictício", "NULL Pluggy institution must be supplied by config"


def test_subtype_carried_to_dim(tmp_path):
    # A19 (LOCKED 2026-06-02): subtype is the sole discriminator of investment kind (all live
    # holdings are type=FIXED_INCOME), so dim_investment carries it through from cleansed (U5).
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "itemId": "item-ficticio", "type": "FIXED_INCOME", "subtype": "CDB",
         "name": "CDB - BANCO EXEMPLO"},
    ])
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={
        "inv-cdb": {"holding_uuid": "ficticio-cdb-2031-03", "alias": "CDB Banco Fictício"},
    })
    _run(tmp_path, inv_cfg)
    assert _read_dim(tmp_path)[0]["subtype"] == "CDB", "subtype must survive into dim_investment"


def test_dim_name_uses_alias_for_display(tmp_path):
    # dim_investment.name is the user-facing label = COALESCE(alias, raw name), mirroring U3's
    # dim_account.name. Several CDBs of one bank can share the identical raw Pluggy name
    # "CDB - BANCO EXEMPLO S.A.", so without the alias the dim shows indistinguishable rows.
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "itemId": "item-ficticio", "type": "FIXED_INCOME", "subtype": "CDB",
         "name": "CDB - BANCO EXEMPLO S.A."},
    ])
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={
        "inv-cdb": {"holding_uuid": "ficticio-cdb-2031-03", "alias": "CDB Banco Fictício mar/2031"},
    })
    _run(tmp_path, inv_cfg)
    assert _read_dim(tmp_path)[0]["name"] == "CDB Banco Fictício mar/2031", \
        "dim name must use the friendly alias, not the non-unique raw Pluggy name"


def test_dim_investment_written_to_curated(tmp_path):
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-1", "itemId": "item-ficticio", "type": "FIXED_INCOME", "subtype": "CDB",
         "name": "CDB", "issuer": "FICTICIA PARTICIPACOES S.A."},
    ])
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={
        "inv-1": {"holding_uuid": "ficticio-cdb-2031-03", "alias": "CDB"},
    })
    _run(tmp_path, inv_cfg)
    tbl = _read_dim(tmp_path)
    rows = [(r["holding_uuid"], r["pluggy_holding_id"], r["name"], r["type"],
             r["subtype"], r["issuer"], r["institution"]) for r in tbl]
    assert rows == [("ficticio-cdb-2031-03", "inv-1", "CDB", "FIXED_INCOME", "CDB", "FICTICIA PARTICIPACOES S.A.", "Banco Fictício")]


def test_empty_input_produces_empty_typed_dim(tmp_path):
    # Empty-input seam (mirrors U5): if cleansed.investments is empty, produce an empty,
    # correctly-typed dim_investment rather than crash.
    _seed_empty_cleansed(tmp_path)
    _seed_institution_via_accounts(tmp_path, institutions={"item-ficticio": "Banco Fictício"})
    inv_cfg = _write_investments_config(tmp_path, holdings={})
    _run(tmp_path, inv_cfg)
    df = _delta_util.read(tmp_path, "curated", "dim_investment")
    assert len(df) == 0, "empty seam must yield zero rows, not crash"
    assert df.schema["holding_uuid"] == pl.Utf8
    assert df.schema["pluggy_holding_id"] == pl.Utf8
    assert set(df.columns) == {"holding_uuid", "pluggy_holding_id", "name", "type", "subtype",
                               "issuer", "institution"}, "dim_investment column contract (spec §5)"
