"""Tests for scripts/leakguard_patterns.py: personal data known by its shape (CPF, CNPJ, account,
e-mail, the owner's name), run through the guard and one function at a time.

Every PII-shaped value is made up and assembled at run time (tests/leakguard_fixtures.py).
"""

import itertools
import os

import pytest

from scripts import leakguard_checkout, leakguard_patterns
from tests import leakguard_fixtures
from tests.leakguard_fixtures import (
    ACCT, CNPJ, CNPJ_PUNCT, CPF, CPF_PUNCT, EMAIL, SECRET, _cnpj, _cpf, _wrong, run,
)

repo = pytest.fixture(leakguard_fixtures.repo)
workspace = pytest.fixture(leakguard_fixtures.workspace)


# ---------------------------------------------------------------------------- patterns


@pytest.mark.parametrize(("label", "value"), [("CPF", CPF), ("CPF", CPF_PUNCT), ("CNPJ", CNPJ),
                                              ("CNPJ", CNPJ_PUNCT), ("ACCT", ACCT), ("EMAIL", EMAIL)])
def test_personal_data_fails_with_its_label_and_line_without_being_echoed(repo, capsys, label, value):
    code, _, failures = run(capsys, repo("notes.txt", f"first\nbefore {value} after\n"))
    assert code == 1
    assert failures == [f"{label}: notes.txt:2 -> '{value[:2]}…' ({len(value)} chars)"]


@pytest.mark.parametrize("value", [_wrong(CPF), _wrong(CPF_PUNCT), _wrong(CNPJ), "000" + "00000000",
                                   "111" + "22233344", "12345678" + "000190", "98765432" + "000155",
                                   "000" + "00000000000"])
def test_a_document_with_invalid_check_digits_is_made_up_and_passes(repo, capsys, value):
    assert run(capsys, repo("gen.py", f"doc = '{value}'\n"))[2] == []


def test_a_cpf_with_its_first_check_digit_wrong_passes(repo, capsys):
    first_wrong = CPF[:9] + str((int(CPF[9]) + 1) % 10) + CPF[10]
    cnpj_first_wrong = CNPJ[:12] + str((int(CNPJ[12]) + 1) % 10) + CNPJ[13]
    assert run(capsys, repo("gen.py", f"a = '{first_wrong}'\nb = '{cnpj_first_wrong}'\n"))[2] == []


@pytest.mark.parametrize("text", [f"x = {CPF}9", f"x = 9{CPF}", f"x = 1.{CPF}", f"x = {CPF}.5",
                                  f"x = {CNPJ}0", f"x = 7,{CNPJ}"])
def test_digits_inside_a_longer_number_are_not_a_document(repo, capsys, text):
    assert run(capsys, repo("x.py", text + "\n"))[2] == []


@pytest.mark.parametrize("text", [f"sha256:7f{CPF}ab", f"h = a{CNPJ}", f"cpf_{CPF}", f"{CPF}_old", f"id{CPF}",
                                  f"CPF\t{CPF[:3]}\t{CPF[3:6]}\t{CPF[6:9]}\t{CPF[9:]}",
                                  f"{CNPJ[:2]}\t{CNPJ[2:5]}\t{CNPJ[5:8]}\t{CNPJ[8:12]}\t{CNPJ[12:]}"])
def test_a_document_next_to_a_letter_or_underscore_or_split_by_tabs_is_flagged(repo, capsys, text):
    """m1: only a digit next to it makes it part of something longer; a letter or '_' does not."""
    assert len(run(capsys, repo("x.py", text + "\n"))[2]) == 1


def test_a_document_split_across_lines_is_not_one(repo, capsys):
    assert run(capsys, repo("x.txt", f"{CPF[:3]}\n{CPF[3:6]}\n{CPF[6:9]}\n{CPF[9:]}\n"))[2] == []


def test_uv_lock_hashes_are_not_documents_but_its_real_values_are_checked(repo, capsys, workspace, monkeypatch):
    """A hex digest may hold eleven digits that pass the check-digit rule: in uv.lock, which holds
    hashes and package names only, the document patterns do not apply; every other check does."""
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    code, _, failures = run(capsys, repo("uv.lock", f"hash = 'sha256:7f{CPF}ab'\nx = '{SECRET}'\n{EMAIL}\n"))
    assert code == 1
    assert [f.split(" -> ")[0] for f in failures] == ["EMAIL: uv.lock:3", "REAL: uv.lock:2"]
    assert len(run(capsys, repo("workspaces/W/uv.lock", f"h = 7f{CPF}ab\n"))[2]) == 1


@pytest.mark.parametrize("text", [f"CPF {CPF_PUNCT}.", f"CPF: {CPF[:3]} {CPF[3:6]} {CPF[6:9]} {CPF[9:]}",
                                  f"cnpj {CNPJ_PUNCT}.", f"({CPF})"])
def test_a_document_at_the_end_of_a_sentence_or_with_spaces_is_flagged(repo, capsys, text):
    failures = run(capsys, repo("x.md", text + "\n"))[2]
    assert len(failures) == 1


@pytest.mark.parametrize("text", ["SERIE 2026-4-10 10X", "0000-0", "99999-9", "a1234-5", "1234-56", "123-4"])
def test_a_date_part_a_filler_or_another_shape_is_not_an_account(repo, capsys, text):
    assert run(capsys, repo("x.py", f"s = '{text}'\n"))[2] == []


def test_an_account_at_the_end_of_a_date_like_run_is_still_flagged(repo, capsys):
    assert len(run(capsys, repo("x.py", f"s = '10-{ACCT}'\n"))[2]) == 1


@pytest.mark.parametrize(("user", "domain"), [("x.y", "uol.com.br"), ("a", "bol.com.br"), ("a", "proton.me"),
                                             ("z", "terra.com.br"), ("q", "googlemail.com"), ("w", "yahoo.com.br"),
                                             ("m", "icloud.com"), ("o", "OUTLOOK.COM"), ("h", "hotmail.com")])
def test_personal_e_mail_providers_are_flagged(repo, capsys, user, domain):
    assert len(run(capsys, repo("x.py", f"to = '{user}@{domain}'\n"))[2]) == 1


def test_an_example_e_mail_passes(repo, capsys):
    assert run(capsys, repo("x.py", "to = 'test@example.com'\nfrom = 'ci@github.com'\n"))[2] == []


def test_an_owner_name_is_blocked_except_where_attribution_is_intended(repo, capsys):
    text = "Copyright\nFulano de\n  Tal\n"
    code, _, failures = run(capsys, repo("src.py", text), repo("README.md", text), repo("LICENSE", text))
    assert code == 1
    assert failures == ["NAME: src.py:2 -> 'Fu…' (15 chars)"]


def test_attribution_files_are_still_checked_for_other_pii(repo, capsys):
    assert run(capsys, repo("README.md", f"mail me at {EMAIL}\n"))[2] == ["EMAIL: README.md:1 -> 'so…' (17 chars)"]


def test_every_hit_in_every_file_is_reported(repo, capsys):
    a = repo("a.txt", f"{ACCT} and {EMAIL}\n")
    b = repo("b.txt", f"\n{CPF}\n")
    assert run(capsys, a, b)[2] == ["ACCT: a.txt:1 -> '12…' (6 chars)", "EMAIL: a.txt:1 -> 'so…' (17 chars)",
                                    "CPF: b.txt:2 -> '52…' (11 chars)"]


# ---------------------------------------------------------------------------- owner name patterns


@pytest.fixture
def guard_home(tmp_path, monkeypatch):
    """Point the guard at a scratch repo root (its .leakguard-local lives there) with no env."""
    monkeypatch.setattr(leakguard_checkout, "MAIN_ROOT", tmp_path)
    monkeypatch.delenv("LEAKGUARD_NAME_PATTERNS", raising=False)
    return tmp_path


def names(patterns, text):
    return [m.group(0) for rx in patterns for m in rx.finditer(text)]


def test_no_env_and_no_local_file_means_no_name_patterns(guard_home):
    assert leakguard_patterns._load_owner_name_patterns() == []


def test_env_patterns_are_split_by_pathsep_and_blanks_dropped(guard_home, monkeypatch):
    monkeypatch.setenv("LEAKGUARD_NAME_PATTERNS", os.pathsep.join(["fulano", " ", "beltrano"]))
    found = names(leakguard_patterns._load_owner_name_patterns(), "FULANO e Beltrano")
    assert found == ["FULANO", "Beltrano"]


def test_local_file_adds_one_pattern_per_line_skipping_comments_and_blanks(guard_home, monkeypatch):
    monkeypatch.setenv("LEAKGUARD_NAME_PATTERNS", "fulano")
    (guard_home / ".leakguard-local").write_text("# owner names\n\n  ciclano  \n#beltrano\n", encoding="utf-8")
    found = names(leakguard_patterns._load_owner_name_patterns(), "Fulano, Ciclano, #beltrano")
    assert found == ["Fulano", "Ciclano"]


def test_leakguard_local_is_read_from_the_given_root(tmp_path, guard_home):
    other = tmp_path / "other"
    other.mkdir()
    (other / ".leakguard-local").write_text("ciclano\n", encoding="utf-8")
    (guard_home / ".leakguard-local").write_text("fulano\n", encoding="utf-8")
    assert names(leakguard_patterns._load_owner_name_patterns(other), "Fulano Ciclano") == ["Ciclano"]
    assert names(leakguard_patterns._load_owner_name_patterns(), "Fulano Ciclano") == ["Fulano"]


# ---------------------------------------------------------------------------- the rules, one function at a time
# Each test below pins one function on its own, with the guard run in process (the mutation run skips
# the command-line tests of tests/test_leakguard.py), so a change to any detail of the function is noticed.


def _total(digits, weights):
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True))


def _documents_by_remainder(size, base_of):
    """{(which check digit, remainder of its sum mod 11): a valid made-up document}: the rule maps a
    remainder of 0 or 1 to digit 0 and the rest to 11 - remainder, and each case is its own branch."""
    found = {}
    for n in range(1, 4000):
        base = base_of(n)
        doc = _cpf(base) if size == CPF_SIZE else _cnpj(base)
        weights = list(range(11, 1, -1)) if size == CPF_SIZE else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        first = _total(doc[:size - 2], weights[len(weights) - size + 2:]) % 11
        second = _total(doc[:size - 1], weights[len(weights) - size + 1:]) % 11
        if len(set(doc)) > 1:
            found.setdefault((1, first), doc)
            found.setdefault((2, second), doc)
    return {key: doc for key, doc in found.items() if key[1] <= 2}


CPF_SIZE = 11


CPF_BY_REMAINDER = _documents_by_remainder(CPF_SIZE, lambda n: f"{n * 7919:09d}")


CNPJ_BY_REMAINDER = _documents_by_remainder(14, lambda n: f"{n * 7919 % 100_000_000:08d}0001")


def test_every_remainder_branch_of_the_check_digit_rule_is_exercised_by_the_search():
    assert sorted(CPF_BY_REMAINDER) == sorted(CNPJ_BY_REMAINDER) == [(w, r) for w in (1, 2) for r in (0, 1, 2)]


@pytest.mark.parametrize("key", sorted(CPF_BY_REMAINDER))
def test_a_cpf_is_valid_whatever_the_remainder_of_its_check_sums(key):
    assert leakguard_patterns._cpf_valid(CPF_BY_REMAINDER[key]) is True
    assert leakguard_patterns._cpf_valid(_wrong(CPF_BY_REMAINDER[key])) is False


@pytest.mark.parametrize("key", sorted(CNPJ_BY_REMAINDER))
def test_a_cnpj_is_valid_whatever_the_remainder_of_its_check_sums(key):
    assert leakguard_patterns._cnpj_valid(CNPJ_BY_REMAINDER[key]) is True
    assert leakguard_patterns._cnpj_valid(_wrong(CNPJ_BY_REMAINDER[key])) is False


def _two_digit_document(make, size):
    """A valid made-up document written with exactly two distinct digits."""
    for pair in itertools.combinations("0123456789", 2):
        for mask in range(1, 2 ** size):
            doc = make("".join(pair[mask >> i & 1] for i in range(size)))
            if len(set(doc)) == 2:
                return doc
    raise AssertionError("no such document")


def test_documents_made_of_two_distinct_digits_are_still_documents():
    """Only a run of one digit is a filler."""
    assert leakguard_patterns._cpf_valid(_two_digit_document(_cpf, 9)) is True
    assert leakguard_patterns._cnpj_valid(_two_digit_document(_cnpj, 12)) is True
    assert leakguard_patterns._cpf_valid("1" * 11) is False
    assert leakguard_patterns._cnpj_valid("1" * 14) is False


def test_a_two_digit_account_is_still_an_account(repo, capsys):
    assert run(capsys, repo("a.txt", "n = 1111" + "-2\n"))[2] == ["ACCT: a.txt:1 -> '11…' (6 chars)"]
