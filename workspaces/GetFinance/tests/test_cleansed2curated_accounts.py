import polars as pl
import yaml
from pathlib import Path

from tests import _delta_util

# Cleansed accounts now carry number/subtype/transfer_number (D3); the derivation reads them.
CLEANSED_COLS = {
    "id": pl.Utf8, "itemId": pl.Utf8, "type": pl.Utf8, "subtype": pl.Utf8,
    "number": pl.Utf8, "transfer_number": pl.Utf8, "name": pl.Utf8,
    "balance": pl.Float64, "currency": pl.Utf8, "creditData": pl.Utf8, "ingested_at": pl.Utf8,
}


def _seed_cleansed_accounts(tmp_path: Path, rows: list[dict]) -> None:
    df = pl.DataFrame(
        {c: [r.get(c, "BRL" if c == "currency" else None) for r in rows] for c in CLEANSED_COLS},
        schema=CLEANSED_COLS,
    )
    _delta_util.seed(tmp_path, "cleansed", "accounts", df)


def _write_institutions(tmp_path: Path, banks: dict) -> Path:
    cfg = tmp_path / "institutions.yaml"
    cfg.write_text(yaml.safe_dump({"banks": banks}))
    return cfg


def _run(tmp_path: Path, cfg: Path) -> None:
    _delta_util.run_script("cleansed2curated", "cleansed2curated_accounts",
                           ["--source", "cleansed", "--target", "curated",
                            "--institutions-config", str(cfg)], tmp_path)


def _read_dim(tmp_path: Path) -> list[dict]:
    return _delta_util.read(tmp_path, "curated", "dim_account").sort("account_uuid").to_dicts()


def test_bank_checking_derivation(tmp_path):
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-ficticio-bank", "itemId": "item-ficticio", "type": "BANK",
         "subtype": "CHECKING_ACCOUNT", "number": "12345123-6",
         "transfer_number": "901/0001/12345123-6", "name": "ficticio"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    r = _read_dim(tmp_path)[0]
    assert r["account_uuid"] == "banco-ficticio-checking-1236"
    assert r["pluggy_account_id"] == "p-ficticio-bank"
    assert r["name"] == "Banco Fictício Conta Corrente"
    assert r["institution"] == "Banco Fictício"
    assert r["type"] == "BANK"
    assert r["currency"] == "BRL"


def test_credit_card_inherits_item_institution(tmp_path):
    # CREDIT card has no transfer_number → inherits the institution from the item's BANK account.
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-ficticio-bank", "itemId": "item-ficticio", "type": "BANK",
         "subtype": "CHECKING_ACCOUNT", "number": "12345123-6",
         "transfer_number": "901/0001/12345123-6", "name": "ficticio"},
        {"id": "p-ficticio-card", "itemId": "item-ficticio", "type": "CREDIT",
         "subtype": "CREDIT_CARD", "number": "7781", "transfer_number": None,
         "name": "FICTICIO CARTAO EXEMPLO GOLD",
         "creditData": "{'level': 'GOLD', 'brand': 'VISA'}"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    by_uuid = {r["account_uuid"]: r for r in _read_dim(tmp_path)}
    card = by_uuid["banco-ficticio-credit-7781"]
    assert card["institution"] == "Banco Fictício"
    assert card["type"] == "CREDIT"
    assert card["name"] == "Banco Fictício Cartão Gold"


def test_savings_subtype_alias_and_kind(tmp_path):
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-pp", "itemId": "item-ficticio", "type": "BANK", "subtype": "SAVINGS_ACCOUNT",
         "number": "00012345-6", "transfer_number": "901/0001/00012345-6", "name": "poup"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    r = _read_dim(tmp_path)[0]
    # number "00012345-6" -> digits "000123456" -> last4 "3456"
    assert r["account_uuid"] == "banco-ficticio-savings-3456"
    assert r["name"] == "Banco Fictício Poupança"


def test_second_bank_checking_derivation(tmp_path):
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-modelo", "itemId": "item-modelo", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "246802466", "transfer_number": "902/0001/24680246-6", "name": "Conta Corrente"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício", "902": "Banco Modelo"})
    _run(tmp_path, cfg)
    r = _read_dim(tmp_path)[0]
    assert r["account_uuid"] == "banco-modelo-checking-2466"
    assert r["name"] == "Banco Modelo Conta Corrente"
    assert r["institution"] == "Banco Modelo"


def test_every_account_flows_no_filter(tmp_path):
    # D3: no filter — every cleansed account reaches dim_account (no quarantine, no inner join).
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-1", "itemId": "item-ficticio", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "12345123-6", "transfer_number": "901/0001/12345123-6", "name": "A"},
        {"id": "p-2", "itemId": "item-modelo", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "246802466", "transfer_number": "902/0001/24680246-6", "name": "B"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício", "902": "Banco Modelo"})
    _run(tmp_path, cfg)
    assert len(_read_dim(tmp_path)) == 2


def test_unknown_bank_code_gets_placeholder_not_dropped(tmp_path):
    # A bank code absent from institutions.yaml: account still flows with an `unknown-{code}`
    # placeholder institution (logged), never dropped — no quarantine, no hard-fail.
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-x", "itemId": "item-x", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "55554321", "transfer_number": "999/0001/55554321", "name": "Mystery"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})  # 999 not listed
    _run(tmp_path, cfg)
    rows = _read_dim(tmp_path)
    assert len(rows) == 1
    assert rows[0]["institution"] == "unknown-999"
    assert rows[0]["account_uuid"] == "unknown-999-checking-4321"


def test_two_segment_transfer_number_falls_to_plain_unknown(tmp_path):
    # M1: a connector returning only 'agency/account' (no 3-digit COMPE code) must NOT treat the
    # agency as a bank code. The account still flows, with a clean `unknown` institution (not
    # `unknown-0001`), so it isn't mis-keyed (getfinance-pluggy-integration.md §4).
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-2seg", "itemId": "item-2seg", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "12345123-6", "transfer_number": "0001/12345123-6", "name": "NoCode"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    r = _read_dim(tmp_path)[0]
    assert r["institution"] == "unknown"
    assert r["account_uuid"] == "unknown-checking-1236"


def test_credit_card_no_bank_in_item_gets_unknown(tmp_path):
    # m4 / Pass-2 fallback: a CREDIT card whose Pluggy item has NO BANK account can't inherit an
    # institution → logged 'unknown' placeholder, but still flows (no current live case).
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-card-only", "itemId": "item-cardonly", "type": "CREDIT", "subtype": "CREDIT_CARD",
         "number": "1234", "transfer_number": None, "name": "Card", "creditData": "{'level': 'GOLD'}"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    r = _read_dim(tmp_path)[0]
    assert r["institution"] == "unknown"
    assert r["account_uuid"] == "unknown-credit-1234"


def test_bank_null_number_uses_transfer_number_segment(tmp_path):
    # m2: a BANK account that arrives with no `number` derives last4 from the transferNumber
    # account segment instead of a degenerate empty suffix.
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-nonum", "itemId": "item-ficticio", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": None, "transfer_number": "901/0001/12345123-6", "name": "A"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    r = _read_dim(tmp_path)[0]
    # account segment "12345123-6" -> digits "123451236" -> last4 "1236"
    assert r["account_uuid"] == "banco-ficticio-checking-1236"


def test_accounts_unmapped_no_longer_written(tmp_path):
    # The quarantine table is removed — the script must not create cleansed.accounts_unmapped.
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-1", "itemId": "item-ficticio", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "12345123-6", "transfer_number": "901/0001/12345123-6", "name": "A"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    assert not (tmp_path / "cleansed" / "accounts_unmapped" / "_delta_log").exists()


def test_dim_account_column_set_unchanged(tmp_path):
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-1", "itemId": "item-ficticio", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "12345123-6", "transfer_number": "901/0001/12345123-6", "name": "A"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    cols = _delta_util.read(tmp_path, "curated", "dim_account").columns
    assert cols == ["account_uuid", "pluggy_account_id", "name", "institution", "type", "currency"]


def test_multi_institution_resolves_per_bank_code(tmp_path):
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-ficticio", "itemId": "item-ficticio", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "12345123-6", "transfer_number": "901/0001/12345123-6", "name": "A"},
        {"id": "p-nu", "itemId": "item-nu", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "98761234", "transfer_number": "260/0001/98761234", "name": "B"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício", "260": "Nubank"})
    _run(tmp_path, cfg)
    by_uuid = {r["account_uuid"]: r["institution"] for r in _read_dim(tmp_path)}
    assert by_uuid == {"banco-ficticio-checking-1236": "Banco Fictício", "nubank-checking-1234": "Nubank"}


def test_credit_card_no_level_alias_fallback(tmp_path):
    # CREDIT with no parseable creditData level → alias falls back to '{institution} Cartão'.
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-bank", "itemId": "item-ficticio", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "12345123-6", "transfer_number": "901/0001/12345123-6", "name": "A"},
        {"id": "p-card", "itemId": "item-ficticio", "type": "CREDIT", "subtype": "CREDIT_CARD",
         "number": "7781", "transfer_number": None, "name": "Card", "creditData": None},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    by_uuid = {r["account_uuid"]: r for r in _read_dim(tmp_path)}
    assert by_uuid["banco-ficticio-credit-7781"]["name"] == "Banco Fictício Cartão"


def _write_institutions_raw_utf8(tmp_path: Path, banks: dict) -> Path:
    # institutions.yaml stores accented names as RAW UTF-8 (a name like 'Banco Fictício' is
    # b'Banco Fict\xc3\xadcio'), not ascii-escaped like yaml.safe_dump emits. Write raw UTF-8 so the locale-decode bug below is
    # actually reproducible (an ascii-escaped file decodes identically under utf-8 and cp1252).
    lines = ["banks:"] + [f'  "{code}": {name}' for code, name in banks.items()]
    cfg = tmp_path / "institutions.yaml"
    cfg.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return cfg


def test_institution_uuid_immune_to_windows_cp1252_locale(monkeypatch, tmp_path):
    # Regression (an accented bank name gave a wrong slug): the daily prod job runs under Windows
    # Python, whose default locale encoding is cp1252. Reading institutions.yaml WITHOUT
    # encoding='utf-8' mis-decodes 'Banco Fictício' (utf-8 bytes c3 ad) into 'Banco FictÃ\xadcio',
    # which _slugify folds to 'banco-fictacio' — so account_uuid would come out
    # 'banco-fictacio-checking-1236' on Windows but 'banco-ficticio-checking-1236' in WSL dev. The
    # config read must pin utf-8 so the key is platform-independent.
    real_read_text = Path.read_text

    def cp1252_default_read_text(self, *args, **kwargs):
        # Simulate the Windows platform default ONLY for the config file: a read with no explicit
        # encoding decodes cp1252; an explicit encoding='utf-8' (the fix) is honored.
        if self.name == "institutions.yaml" and not args and kwargs.get("encoding") is None:
            return self.read_bytes().decode("cp1252")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", cp1252_default_read_text)

    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-ficticio-bank", "itemId": "item-ficticio", "type": "BANK",
         "subtype": "CHECKING_ACCOUNT", "number": "12345123-6",
         "transfer_number": "901/0001/12345123-6", "name": "ficticio"},
    ])
    cfg = _write_institutions_raw_utf8(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    r = _read_dim(tmp_path)[0]
    assert r["account_uuid"] == "banco-ficticio-checking-1236"
    assert r["institution"] == "Banco Fictício"


def test_rerun_is_idempotent(tmp_path):
    _seed_cleansed_accounts(tmp_path, [
        {"id": "p-1", "itemId": "item-ficticio", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "number": "12345123-6", "transfer_number": "901/0001/12345123-6", "name": "A"},
    ])
    cfg = _write_institutions(tmp_path, {"901": "Banco Fictício"})
    _run(tmp_path, cfg)
    _run(tmp_path, cfg)
    assert len(_read_dim(tmp_path)) == 1
