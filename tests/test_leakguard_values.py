"""Tests for scripts/leakguard_values.py: the real values, where they come from, how they are read and
how they are found in a text.

Every "real" value is made up and assembled at run time (tests/leakguard_fixtures.py).
"""

import re
import sqlite3
from pathlib import Path

import polars as pl
import pytest

from scripts import leakguard_checkout, leakguard_patterns, leakguard_values
from tests import leakguard_fixtures
from tests.leakguard_fixtures import (
    AMOUNT, CARD_FINAL, NO_SOURCE, NUMBER, NUMBER_BARE, OWNER, PIX_NAME, PIX_SHOPS, RECEIVER, SECRET, TRANSFER,
    make_workspace, run,
)

repo = pytest.fixture(leakguard_fixtures.repo)
workspace = pytest.fixture(leakguard_fixtures.workspace)


# ---------------------------------------------------------------------------- real values: reading them


def test_a_worktree_without_its_main_checkout_fails_the_check(repo, capsys, monkeypatch):
    """D2: without the main checkout the real data's workspaces are not known; that must not pass."""
    monkeypatch.setattr(leakguard_checkout, "MAIN_ROOT_ERROR", "this is a worktree and its main checkout could not be found")
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == ["REAL-VALUES: this is a worktree and its main checkout could not be found"]


def test_real_values_come_from_raw_edicao_and_env(workspace):
    values = leakguard_values.real_values(workspace)
    for expected, source in [(OWNER.lower(), "raw.accounts.owner"), (NUMBER, "raw.accounts.number"),
                             (TRANSFER, "raw.accounts.bankData"), (RECEIVER.lower(), "raw.transactions.paymentData"),
                             ("txn-uuid-0003-cccc", "raw.transactions.id"), ("7654321", "raw.transactions.paymentData"),
                             ("padaria do ze", "edicao.mapeamento"), (SECRET, ".env.CLIENT_SECRET"),
                             ("item-uuid-0009-zzzz", ".env.ITEM_IDS"), ("client-id-123", ".env.CLIENT_ID")]:
        assert values.get(expected) == source, expected


def test_short_descriptions_dates_short_words_and_temp_files_are_not_real_values(workspace):
    values = leakguard_values.real_values(workspace)
    for absent in ["padaria", "netflix", "x", "conta", "2026-11-20", "2026-11-12t09:00:00.000z",
                   "temporario nao lido jamais", "1.5", "true", "12"]:
        assert absent not in values, absent


# ---------------------------------------------------------------------------- descriptions, amounts, card digits (df-88xr)


def test_descriptions_amounts_and_card_digits_are_real_values(workspace):
    values = leakguard_values.real_values(workspace)
    for expected, source in [(PIX_NAME.lower(), "raw.transactions.description"),
                             (AMOUNT, "raw.transactions.amount"), ("123456.78", "raw.accounts.balance"),
                             (CARD_FINAL, "raw.transactions.creditCardMetadata")]:
        assert values.get(expected) == source, expected


def test_a_person_named_in_a_pix_description_is_blocked_written_alone(repo, capsys, workspace, monkeypatch):
    """The bank's wording around the name ("Pix enviado", a date glued to it) is not needed to find it."""
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    code, _, failures = run(capsys, repo("fixture.py", f"x = 1\nWHO = '{PIX_NAME}'\n"))
    assert code == 1
    assert failures == [f"REAL: fixture.py:2 -> 'si…' ({len(PIX_NAME)} chars, raw.transactions.description from ws)"]


def test_an_amount_with_cents_copied_from_raw_is_blocked(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    code, _, failures = run(capsys, repo("fixture.py", f"x = 1\nVALOR = -{AMOUNT}\nTEXTO = 'R$ 1.234,56'\n"))
    assert code == 1
    assert failures == [f"REAL: fixture.py:{n} -> '12…' ({len(AMOUNT)} chars, raw.transactions.amount from ws)"
                        for n in (2, 3)]


def test_round_amounts_small_amounts_and_the_bank_wording_pass(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    text = "a = 5000.00\nb = 45.90\nc = 10.00\nd = 'Pix enviado'\ne = 'Loja Alfa'\nf = 4321\n"
    assert run(capsys, repo("fixture.py", text))[0] == 0


def _parts(*descriptions):
    return set(leakguard_values.description_parts([(d, "raw.transactions.description") for d in descriptions]))


def test_a_description_s_own_part_starts_after_the_bank_s_wording():
    """A word in VOCABULARY_PHRASES descriptions is the bank's ("Pix", "enviado", "Loja" here); a
    common word after a name stays in. The shops' own part is one short word: not a value."""
    assert _parts(*PIX_SHOPS, "Pix enviado Beltrano Loja 01/02") == {"beltrano loja"}


def test_with_fewer_descriptions_than_the_bank_s_wording_each_is_all_its_own_part():
    """In one description fewer than VOCABULARY_PHRASES, no word is the bank's: each is all its own part."""
    seven = PIX_SHOPS[:leakguard_values.VOCABULARY_PHRASES - 1]
    assert _parts(*seven) == {shop.lower() for shop in seven}


def test_a_description_made_of_the_bank_s_wording_only_has_no_own_part():
    """The same wording with another date is the same description: only different words count."""
    wording = [f"PAGAMENTO RECEBIDO {shop} {i}/12" for i, shop in enumerate(PIX_SHOPS)]
    assert _parts(*wording, "PAGAMENTO RECEBIDO 03/04") == set()
    assert _parts("PAGAMENTO RECEBIDO 01/12", "PAGAMENTO RECEBIDO 02/12") == {"pagamento recebido"}


def test_a_date_glued_to_a_name_is_left_out_of_it():
    assert _parts("PIX TRANSF FULANA BELTRANA12/03") == {"pix transf fulana beltrana"}


@pytest.mark.parametrize(("description", "parts"), [("PADARIA", set()), ("Pix", set()), ("IOF X", set()),
                                                    ("SUPERMERCADO", {"supermercado"}), ("SUPERMERCAD", set()),
                                                    ("Jo Ana", {"jo ana"}), ("Jo Al", set()), (None, set()),
                                                    ("", set())])
def test_a_short_description_is_not_a_value(description, parts):
    """Two words or more from MIN_VALUE_LENGTH characters; one word from MIN_WORD_LENGTH."""
    assert _parts(description) == parts


def test_a_description_of_the_bank_s_wording_only_does_not_stop_the_ones_after_it():
    wording = [f"PAGAMENTO RECEBIDO {shop}" for shop in PIX_SHOPS]
    assert _parts("PAGAMENTO RECEBIDO", *wording, "PAGAMENTO RECEBIDO Beltrano Silva") == {"beltrano silva"}


@pytest.mark.parametrize("written", [f"WHO = '{PIX_NAME}'", f"{PIX_NAME.upper()}12/03", "sicrano  ficticio\n souza",
                                     "Sicrano-Ficticio_Souza", "PIX SICRANO FICTICIO SOUZA S.A."])
def test_a_description_s_own_part_matches_with_any_non_letter_between_its_words(written):
    values = {PIX_NAME.lower(): "raw.transactions.description"}
    assert [h[1] for h in leakguard_values.real_value_hits(written, values)] == [PIX_NAME.lower()]


@pytest.mark.parametrize("written", ["Sicrano Ficticio Souzas", "XSicrano Ficticio Souza", "Sicrano Souza"])
def test_a_description_s_own_part_inside_a_longer_word_does_not_match(written):
    assert leakguard_values.real_value_hits(written, {PIX_NAME.lower(): "raw.transactions.description"}) == []


@pytest.mark.parametrize("written", ["1234.56", "1234,56", "1.234,56", "1,234.56", "-1234.56", "R$1.234,56",
                                     "[1, 1234.56]", "Decimal('1234.56')"])
def test_an_amount_matches_written_as_money(written):
    values = {AMOUNT: "raw.transactions.amount"}
    assert [h[1] for h in leakguard_values.real_value_hits(written, values)] == [AMOUNT]


@pytest.mark.parametrize("written", ["11234.56", "1234.567", "1234.5", "1.1234,56", "1234 56", "123456",
                                     "2.1234.56", "1234-56"])
def test_an_amount_inside_another_number_or_not_written_as_money_does_not_match(written):
    assert leakguard_values.real_value_hits(written, {AMOUNT: "raw.transactions.amount"}) == []


def test_an_amount_below_a_thousand_has_no_grouped_form():
    assert leakguard_values.real_value_hits("123.45 e 0.123,45", {"123.45": "raw.transactions.amount"}) == [
        (1, "123.45", "raw.transactions.amount")]


@pytest.mark.parametrize(("amount", "kept"), [("-1234.56", "1234.56"), ("100.01", "100.01"), ("99.99", None),
                                              ("5000.00", None), ("5001.00", None), ("5000", None), ("abc", None),
                                              ("1e3", None),
                                              (" 250.5 ", "250.50")])
def test_which_amounts_are_real_values(tmp_path, amount, kept):
    make_workspace(tmp_path)
    frame = {"id": ["t-amount-0001"], "amount": [amount]}
    path = tmp_path / "data" / "raw" / "transactions" / "ingestion_date=2026-11-01" / "data.parquet"
    pl.DataFrame(frame).write_parquet(path)
    amounts = {v for v, src in leakguard_values.real_values(tmp_path).items() if src == "raw.transactions.amount"}
    assert amounts == ({kept} if kept else set())


@pytest.mark.parametrize("written", ['"cardNumber": "4321"', "final 4321", "Cartão final: 4321", "**** 4321",
                                     "•••• 4321", "XXXX-4321", "conta-4321", "account_4321", "numero 4321"])
def test_a_card_final_matches_next_to_what_names_a_card(written):
    values = {CARD_FINAL: "raw.transactions.creditCardMetadata"}
    assert [h[1] for h in leakguard_values.real_value_hits(written, values)] == [CARD_FINAL]


@pytest.mark.parametrize("written", ["4321", "ano 4321", "port = 4321", "final 43210", "final 14321", "v1.4321"])
def test_a_card_final_alone_or_inside_a_number_does_not_match(written):
    assert leakguard_values.real_value_hits(written, {CARD_FINAL: "raw.transactions.creditCardMetadata"}) == []


def test_amounts_are_read_from_every_partition_past_a_value_that_is_not_one(tmp_path):
    make_workspace(tmp_path)
    transactions = tmp_path / "data" / "raw" / "transactions"
    pl.DataFrame({"id": ["t-1", "t-2"], "amount": ["abc", AMOUNT]}).write_parquet(
        transactions / "ingestion_date=2026-11-01" / "data.parquet")
    (transactions / "ingestion_date=2026-11-02").mkdir()
    pl.DataFrame({"id": ["t-3"], "amount": ["250.75"]}).write_parquet(
        transactions / "ingestion_date=2026-11-02" / "data.parquet")
    amounts = {v for v, src in leakguard_values.real_values(tmp_path).items() if src == "raw.transactions.amount"}
    assert amounts == {AMOUNT, "250.75"}


def test_a_short_account_number_is_a_card_final(tmp_path):
    make_workspace(tmp_path)
    path = tmp_path / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01" / "data.parquet"
    pl.DataFrame({"id": ["acc-uuid-0001-aaaa", "acc-uuid-0002-aaaa"], "number": [None, " 8765 "]}).write_parquet(path)
    assert leakguard_values.real_values(tmp_path).get("8765") == "raw.accounts.number"


@pytest.mark.parametrize(("metadata", "final"), [('{"cardNumber": "4321"}', "4321"), ('{"cardNumber": 4321}', "4321"),
                                                 ('{"billId": "4321"}', None), ('{"cardNumber": "54321"}', None),
                                                 ("4321", None), ("", None)])
def test_which_card_metadata_gives_a_card_final(tmp_path, metadata, final):
    make_workspace(tmp_path)
    path = tmp_path / "data" / "raw" / "transactions" / "ingestion_date=2026-11-01" / "data.parquet"
    pl.DataFrame({"id": ["t-card-0001"], "creditCardMetadata": [metadata]}).write_parquet(path)
    finals = {v for v, src in leakguard_values.real_values(tmp_path).items() if src == "raw.transactions.creditCardMetadata"}
    assert finals == ({final} if final else set())


def test_in_the_attribution_files_the_owner_named_in_a_description_passes(repo, capsys, workspace, monkeypatch):
    """A Pix to oneself names the owner: in LICENSE and README.md that is attribution, elsewhere a leak."""
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    owner_part = " ".join(OWNER.split()[:2])
    values = {OWNER.lower(): "raw.accounts.owner", owner_part.lower(): "raw.transactions.description"}
    monkeypatch.setattr(leakguard_values, "real_values", lambda ws: values)
    assert run(capsys, repo("README.md", f"By {owner_part}.\n"))[0] == 0
    assert run(capsys, repo("notes.md", f"By {owner_part}.\n"))[0] == 1


def test_no_data_means_no_real_values(tmp_path):
    assert leakguard_values.real_values(tmp_path) == {}


def test_each_part_of_a_transfer_number_is_a_value_of_its_own(workspace):
    """The account segment may be written alone, without the bank and agency around it."""
    values = leakguard_values.real_values(workspace)
    assert values[TRANSFER] == "raw.accounts.bankData"
    assert NUMBER in values
    assert "0001" not in values   # a part too short to block


@pytest.mark.parametrize(("leaf", "worth"), [("abc12", False), ("abc123", True), ("netflixnetfl", True),
                                             ("netflixnetf", False), ("netflix brasil", True), ("000000", False),
                                             ("aaaaaa", False), ("111-111", False), ("12345", False),
                                             ("2026-11-20", False), ("2026-11-12 09:00", False), ("901/0001", True)])
def test_which_leaves_are_worth_blocking(leaf, worth):
    assert leakguard_values._worth_blocking(leaf) is worth


def test_env_values_of_any_key_with_export_quotes_and_comments(tmp_path):
    """Every value in .env is a credential to keep out, however the line is written."""
    (tmp_path / ".env").write_text("# comment\nexport CLIENT_SECRET=s3cr3t-export\n"
                                   "CLIENT_ID=client-abc-123   # dev app\nOTHER_TOKEN=\"tok-999-xyz\"\n"
                                   "EMPTY=\nNOEQUALS\n  \n", encoding="utf-8")
    assert leakguard_values.real_values(tmp_path) == {"s3cr3t-export": ".env.CLIENT_SECRET",
                                               "client-abc-123": ".env.CLIENT_ID", "tok-999-xyz": ".env.OTHER_TOKEN"}


@pytest.mark.parametrize(("raw", "expected"), [("abc   # dev app", "abc   "), ("abc#not-a-comment", "abc#not-a-comment"),
                                               ("#x", "#x"), ("a\t#b", "a\t"), ("a " + " " * 50_000 + "b",
                                                                                  "a " + " " * 50_000 + "b")])
def test_inline_comments_are_cut_only_after_whitespace(raw, expected):
    assert leakguard_values._strip_inline_comment(raw) == expected


@pytest.mark.parametrize(("text", "expected"), [('{"a": ["x", {"b": "y"}]}', ["x", "y"]), ("{'a': None, 'b': 'z'}", ["z"]),
                                                ("[1, 'w']", ["1", "w"]), ("{not json", ["{not json"]),
                                                ("  plain  ", ["plain"]), ("[" * 5000, ["[" * 5000])])
def test_nested_text_is_opened_into_its_string_leaves(text, expected):
    assert list(leakguard_values._string_leaves(text)) == expected


# ---------------------------------------------------------------------------- real values: matching them


def _hits(text, values):
    return leakguard_values.real_value_hits(text, values)


@pytest.mark.parametrize("written", [NUMBER_BARE, "98765 4", "98765.4", "98765/4", f"account {NUMBER}."])
def test_a_real_number_matches_with_any_or_no_separator(written):
    assert _hits(f"x = '{written}'", {NUMBER: "raw.accounts.number"}) == [(1, NUMBER, "raw.accounts.number")]


def test_a_real_number_inside_a_longer_number_does_not_match():
    values = {NUMBER: "raw.accounts.number"}
    assert _hits("x = 1987654", values) == []
    assert _hits("x = 9876543", values) == []


def test_a_real_name_matches_in_any_case_and_wrapped_across_lines():
    values = {OWNER.lower(): "raw.accounts.owner"}
    assert _hits(f"a\nWritten by {OWNER.upper()[:14]}\n   da Silva, 2026\n", values) == [
        (2, OWNER.lower(), "raw.accounts.owner")]


def test_every_occurrence_is_a_hit_in_line_order():
    values = {"zz-value-1": "a", NUMBER: "b"}
    assert _hits(f"{NUMBER}\nzz-value-1 and {NUMBER}\n", values) == [(1, NUMBER, "b"), (2, NUMBER, "b"),
                                                                     (2, "zz-value-1", "a")]


# ---------------------------------------------------------------------------- real values: where they come from


def test_getfinance_data_gives_the_real_values_of_its_workspace(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    code, _, failures = run(capsys, repo("fixture.py", f"x = 1\nOWNER = '{OWNER}'\nS = '{SECRET}'\n"))
    assert code == 1
    assert failures == [f"REAL: fixture.py:2 -> 'fu…' ({len(OWNER)} chars, raw.accounts.owner from ws)",
                        f"REAL: fixture.py:3 -> 's3…' ({len(SECRET)} chars, .env.CLIENT_SECRET from ws)"]
    assert OWNER not in "".join(failures)


def test_a_clean_file_says_how_many_real_values_it_was_checked_against(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    code, out, _ = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 0
    assert out.strip() == f"leak guard: 1 file(s) clean ({len(leakguard_values.real_values(workspace))} real values from ws)"


@pytest.mark.parametrize("bad", ["missing/data", "ws", "noraw/data"])
def test_getfinance_data_that_is_not_a_data_directory_with_raw_fails_closed(repo, capsys, tmp_path, monkeypatch, bad):
    """A typo in the variable would check against no data and pass silently: it fails instead. It
    names the data/ directory itself, not the workspace above it."""
    make_workspace(tmp_path / "ws")
    (tmp_path / "noraw" / "data").mkdir(parents=True)
    monkeypatch.setenv("GETFINANCE_DATA", str(tmp_path / bad))
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == [f"REAL-VALUES: GETFINANCE_DATA={tmp_path / bad} is not a workspace's data/ directory "
                        "with raw/ in it"]


def test_getfinance_data_that_yields_no_real_value_fails(repo, capsys, tmp_path, monkeypatch):
    empty = tmp_path / "empty" / "data"
    (empty / "raw").mkdir(parents=True)
    monkeypatch.setenv("GETFINANCE_DATA", str(empty))
    code, out, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert (code, out) == (1, "")
    assert failures == [f"REAL-VALUES: GETFINANCE_DATA={empty} yields no real value (no data/raw, data/edicao.db "
                        "or .env there); nothing was checked against it"]


def test_an_empty_getfinance_data_is_unset(repo, capsys, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", "")
    assert run(capsys, repo("a.py", "x = 1\n"))[0] == 0
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    assert run(capsys, repo("a.py", "x = 1\n"))[0] == 1


@pytest.mark.parametrize("waiver", [None, "", "0", "yes", "true"])
def test_outside_ci_a_run_with_no_source_of_real_values_fails_and_says_what_to_set(repo, capsys, monkeypatch,
                                                                                    waiver):
    """B2: no real data must be declared, never assumed from an absent variable or listing."""
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    if waiver is not None:
        monkeypatch.setenv("LEAKGUARD_NO_REAL_DATA", waiver)
    code, out, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert (code, out, failures) == (1, "", [NO_SOURCE])


def test_in_ci_a_run_with_no_source_of_real_values_passes_and_says_why(repo, capsys, monkeypatch):
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    monkeypatch.setenv("CI", "true")
    assert run(capsys, repo("a.py", "x = 1\n")) == (0, "leak guard: 1 file(s) clean (no real values: CI)\n", [])


def test_the_waiver_does_not_skip_a_named_source(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    assert run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))[0] == 1


def test_the_getfinance_data_directory_of_the_main_checkout_is_found_without_being_named(repo, capsys, tmp_path,
                                                                                        monkeypatch):
    """B2: the real data where the pipeline writes it (workspaces/GetFinance/data/) is read with no
    variable and no listing, as both old guards did."""
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    make_workspace(tmp_path / "workspaces" / "GetFinance")
    code, _, failures = run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))
    assert code == 1
    assert failures == [f"REAL: a.py:1 -> '98…' ({len(NUMBER)} chars, raw.accounts.number from GetFinance)"]


def test_a_getfinance_data_directory_with_only_edicao_is_found(repo, capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    data = tmp_path / "workspaces" / "GetFinance" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(data / "edicao.db")
    conn.execute("CREATE TABLE mapeamento (padrao TEXT)")
    conn.execute("INSERT INTO mapeamento VALUES ('MERCADO DO BAIRRO')")
    conn.commit()
    conn.close()
    assert run(capsys, repo("a.py", "y = 'mercado do bairro'\n"))[2] == [
        "REAL: a.py:1 -> 'me…' (17 chars, edicao.mapeamento from GetFinance)"]


@pytest.mark.parametrize("layout", ["synthetic", "other workspace", "no raw or edicao"])
def test_synthetic_data_or_another_workspace_is_not_a_source(repo, capsys, tmp_path, monkeypatch, layout):
    """The demo's data (raw/SYNTHETIC, written by its generator) is made up, and another workspace in
    the checkout is not the one whose data is real: reading either as real would flag values that
    are not. Without another source, the run still needs the waiver."""
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    if layout == "synthetic":
        ws = make_workspace(tmp_path / "workspaces" / "GetFinance")
        (ws / "data" / "raw" / "SYNTHETIC").write_text("written by the generator\n", encoding="utf-8")
    elif layout == "other workspace":
        make_workspace(tmp_path / "workspaces" / "MyBank")
    else:
        (tmp_path / "workspaces" / "GetFinance" / "data" / "curated").mkdir(parents=True)
    assert run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))[2] == [NO_SOURCE]


def test_a_found_data_directory_that_yields_nothing_fails_closed(repo, capsys, tmp_path):
    (tmp_path / "workspaces" / "GetFinance" / "data" / "raw").mkdir(parents=True)
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == [f"REAL-VALUES: {tmp_path / 'workspaces' / 'GetFinance'} (found in the main checkout) "
                        "yields no real value (no data/raw, data/edicao.db or .env there); nothing was checked "
                        "against it"]


def test_a_listed_workspace_outside_the_checkout_is_checked(repo, capsys, tmp_path):
    ws = make_workspace(tmp_path.parent / f"{tmp_path.name}-outside" / "Real")
    repo(".leakguard-workspaces", f"# the real workspace stays on the Windows drive\n\n  {ws}  \n")
    code, _, failures = run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))
    assert code == 1
    assert failures == [f"REAL: a.py:1 -> '98…' ({len(NUMBER)} chars, raw.accounts.number from Real)"]


def test_a_listed_entry_may_be_relative_to_the_root_or_start_with_a_tilde(repo, capsys, tmp_path, monkeypatch):
    home = tmp_path.parent / f"{tmp_path.name}-home"
    make_workspace(home / "Ws", owner="Ciclano Morador da Casa")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))   # what expanduser reads on Windows
    make_workspace(tmp_path / "rel")
    repo(".leakguard-workspaces", "rel\n~/Ws\n")
    code, _, failures = run(capsys, repo("a.py", f"x = '{OWNER}'\ny = 'Ciclano Morador da Casa'\n"))
    assert code == 1
    assert [f.rsplit(" from ", 1)[1] for f in failures] == ["rel)", "Ws)"]


def test_a_listed_directory_without_data_fails_closed(repo, capsys, tmp_path):
    repo(".leakguard-workspaces", f"{tmp_path / 'gone'}\n")
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == [f"REAL-VALUES: {tmp_path / 'gone'} (listed in .leakguard-workspaces) yields no real value "
                        "(no data/raw, data/edicao.db or .env there); nothing was checked against it"]


def test_data_that_cannot_be_read_fails_closed_and_does_not_stop_the_others(repo, capsys, tmp_path, workspace):
    broken = tmp_path / "broken"
    (broken / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01").mkdir(parents=True)
    (broken / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01" / "data.parquet").write_bytes(b"not parquet")
    repo(".leakguard-workspaces", f"{broken}\n{workspace}\n")
    code, _, failures = run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))
    assert code == 1
    assert failures[0].startswith(f"REAL-VALUES: {broken} (listed in .leakguard-workspaces): its data could not "
                                  "be read (")
    assert failures[1:] == [f"REAL: a.py:1 -> '98…' ({len(NUMBER)} chars, raw.accounts.number from ws)"]


def test_a_workspace_named_twice_is_checked_once(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    repo(".leakguard-workspaces", f"{workspace}\n{workspace}/.\n")
    code, _, failures = run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))
    assert code == 1
    assert len(failures) == 1


def test_the_owner_name_is_attribution_in_license_and_readme_but_nothing_else_is(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    lic = repo("LICENSE", f"Copyright (c) 2026 {OWNER}\n")
    readme = repo("README.md", f"by {OWNER}, account {NUMBER[:5]}{NUMBER[6:]}\n")
    other = repo("notes.md", f"{OWNER}\n")
    nested = repo("workspaces/W/README.md", f"{OWNER}\n")
    code, _, failures = run(capsys, lic, readme, other, nested)
    assert code == 1
    assert [f.split(" -> ")[0] for f in failures] == ["REAL: README.md:1", "REAL: notes.md:1",
                                                      "REAL: workspaces/W/README.md:1"]
    assert "raw.accounts.number" in failures[0]


def test_the_owner_exemption_does_not_depend_on_which_source_came_first(repo, capsys, tmp_path):
    """The owner's name may also be a counterparty somewhere; it is still attribution in LICENSE."""
    first = make_workspace(tmp_path / "A", owner="Someone Else Entirely")
    (first / ".env").write_text(f"PAYEE={OWNER}\n", encoding="utf-8")
    make_workspace(tmp_path / "B")
    repo(".leakguard-workspaces", "A\nB\n")
    assert run(capsys, repo("LICENSE", f"(c) {OWNER}\n"))[:1] == (0,)
    assert len(run(capsys, repo("notes.md", f"(c) {OWNER}\n"))[2]) == 2


def test_a_value_of_exactly_the_minimum_length_is_kept(repo, capsys, tmp_path):
    ws = tmp_path / "W"
    ws.mkdir()
    (ws / ".env").write_text("A=ab" + "c123\nB=xy" + "z12\n", encoding="utf-8")
    repo(".leakguard-workspaces", "W\n")
    code, _, failures = run(capsys, repo("a.py", "x = 'abc123'\ny = 'xyz12'\n"))
    assert code == 1
    assert failures == ["REAL: a.py:1 -> 'ab…' (6 chars, .env.A from W)"]


@pytest.mark.parametrize(("leaf", "worth"), [("121212", True), ("1212.12", True), ("aaaaaa", False),
                                             ("111111", False)])
def test_two_distinct_characters_are_enough_to_be_a_value(leaf, worth):
    assert leakguard_values._worth_blocking(leaf) is worth


def test_a_value_that_only_has_digits_in_it_is_not_number_like():
    assert leakguard_values._is_number_like("abc1234567") is False
    assert leakguard_values._is_number_like("1234567") is True


@pytest.mark.parametrize(("function", "setup"), [
    ("_listed_workspaces", ".leakguard-workspaces"), ("_load_owner_name_patterns", ".leakguard-local")])
def test_text_files_of_the_guard_are_read_as_utf8_and_a_stray_byte_does_not_stop_it(function, setup, repo,
                                                                                     tmp_path, monkeypatch):
    seen = []
    original = Path.read_text

    def spy(self, *args, **kwargs):
        seen.append((self.name, kwargs.get("encoding"), kwargs.get("errors")))
        return original(self, *args, **kwargs)

    (tmp_path / setup).write_bytes(b"# caf\xe9\nfulano\n")
    monkeypatch.setattr(Path, "read_text", spy)
    module = leakguard_values if function == "_listed_workspaces" else leakguard_patterns
    result = getattr(module, function)()
    assert seen == [(setup, "utf-8", "ignore")]
    assert len(result) == 1


def test_an_env_file_is_read_as_utf8(tmp_path, monkeypatch):
    seen = []
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: seen.append(k.get("encoding")) or original(self, *a, **k))
    (tmp_path / ".env").write_text("A=abcdef\n", encoding="utf-8")
    assert dict(leakguard_values._env_values(tmp_path / ".env")) == {"abcdef": ".env.A"}
    assert seen == ["utf-8"]


# ---- .env and nested values


def test_env_values_keep_an_equals_sign_inside_the_value_and_only_trim_quotes(tmp_path):
    (tmp_path / ".env").write_text("# OLD_KEY=oldvalue123\nTOKEN=ab12cd34==\nEDGE=\"Xedge-valueX\"\n#   =\n",
                                   encoding="utf-8")
    assert dict(leakguard_values._env_values(tmp_path / ".env")) == {"ab12cd34==": ".env.TOKEN",
                                                              "Xedge-valueX": ".env.EDGE"}


@pytest.mark.parametrize(("raw", "expected"), [("#a ", "#a "), (" #b", " "), ("x#y #z", "x#y ")])
def test_an_inline_comment_may_start_right_after_the_first_character(raw, expected):
    assert leakguard_values._strip_inline_comment(raw) == expected


def test_a_transfer_number_is_split_on_its_slashes_only_when_it_is_a_number():
    assert list(leakguard_values._expand(TRANSFER)) == [TRANSFER, "901", "0001", NUMBER]
    assert list(leakguard_values._expand("901 / 0001 / 98765")) == ["901 / 0001 / 98765", "901", "0001", "98765"]
    assert list(leakguard_values._expand("abc/def-ghi")) == ["abc/def-ghi"]
    assert list(leakguard_values._expand("98765432")) == ["98765432"]


def test_a_real_name_with_a_doubled_space_still_matches_the_text_written_with_one():
    values = {"beltrano  recebedor": "src"}
    assert leakguard_values.real_value_hits("x Beltrano Recebedor", values) == [(1, "beltrano  recebedor", "src")]


def test_a_listing_with_a_duplicate_and_one_without_values_does_not_stop_at_them(repo, capsys, tmp_path, workspace):
    other = make_workspace(tmp_path / "other", owner="Ciclano Morador da Casa")
    empty = tmp_path / "empty"
    empty.mkdir()
    repo(".leakguard-workspaces", f"{workspace}\n{workspace}\n{empty}\n{other}\n")
    sources, failures = leakguard_values.load_real_values()
    assert [name for name, _ in sources] == ["ws", "other"]
    assert len(failures) == 1
    assert failures[0].startswith(f"REAL-VALUES: {empty} (listed in .leakguard-workspaces) yields no real value")


def test_data_that_cannot_be_read_names_the_kind_of_error(repo, tmp_path):
    broken = tmp_path / "broken"
    (broken / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01").mkdir(parents=True)
    (broken / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01" / "data.parquet").write_bytes(b"not parquet")
    repo(".leakguard-workspaces", f"{broken}\n")
    _, failures = leakguard_values.load_real_values()
    assert re.search(r"\(\w+(Error|Exception): ", failures[0])
    assert "(NoneType" not in failures[0]
