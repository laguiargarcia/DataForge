"""Properties of scripts/leakguard.py: a real number, a CPF or a CNPJ is found whatever is written
between its digits (df-ks84). Every value is made up and assembled at run time (valid CPFs and CNPJs
from the check-digit rule), so this file holds no real one.

What escapes on purpose, each with its reason:
- a letter or another symbol as the separator ("98765 x 4", "98765|4"): it is not how a number is
  punctuated, and matching it would flag prose and tables;
- a comma followed by a space ("98765, 4"): a list of numbers, not one number (a weights table
  in the guard's own source read as a real number);
- a CPF or CNPJ across a line break: that is two things, not one document (a real value does match
  across lines).
"""
from hypothesis import example, given, settings, strategies as st

from scripts import leakguard_files, leakguard_patterns, leakguard_values
from tests.leakguard_fixtures import _cnpj, _cpf

def _sources_for(value):
    return [("ws", {value: "raw.accounts.number"})]

# Ways people write the gap between two digits: a space or several, a dash with or without spaces
# (hyphen, en dash, em dash), the underscore of a Python literal, a comma, a dot, a slash, a tab.
_GAP = st.sampled_from(["", " ", "   ", " - ", "-", "–", "—", " – ", " — ", "_", ",",
                        ".", "/", "\t", " / ", "\n", "-\n"])
_DOC_GAP = st.sampled_from(["", " ", "   ", " - ", "-", "–", "—", " – ", "_", ",", ".",
                            "/", "\t", " / ", ". ", "-."])
_DIGITS = st.text("0123456789", min_size=6, max_size=14).filter(lambda d: len(set(d)) > 1)


def _write(digits, gaps):
    return "".join(d + g for d, g in zip(digits, gaps, strict=False)) + digits[len(gaps):]


@st.composite
def _spaced_number(draw):
    digits = draw(_DIGITS)
    gaps = draw(st.lists(_GAP, min_size=len(digits) - 1, max_size=len(digits) - 1))
    return digits, _write(digits, gaps)


@settings(max_examples=200)
@example((("98765" + "4"), "98765" + " - " + "4"))
@example(("987654", "98765" + "_" + "4"))
@example(("987654", "98765" + "," + "4"))
@example(("987654", "98765" + "–" + "4"))
@given(_spaced_number())
def test_real_number_found_whatever_separates_its_digits(case):
    digits, written = case
    hits = leakguard_values.real_value_hits(f"id {written} end", {digits: "raw.accounts.number"})
    assert [(line, value) for line, value, _ in hits] == [(1, digits)]


@settings(max_examples=200)
@given(_spaced_number())
def test_real_number_in_a_file_is_a_finding(case):
    digits, written = case
    failures = leakguard_files.check_text("notes.txt", f"x {written}\n", _sources_for(digits))
    assert any(f.startswith("REAL:") for f in failures)


@given(_DIGITS, st.sampled_from("xX|:;*+#"))
def test_a_letter_or_symbol_between_digits_is_not_a_separator(digits, symbol):
    text = f"id {digits[:3]}{symbol}{digits[3:]} end"
    assert leakguard_values.real_value_hits(text, {digits: "raw.accounts.number"}) == []


@given(_DIGITS, st.sampled_from("0123456789"))
def test_digits_inside_a_longer_number_are_not_the_value(digits, extra):
    values = {digits: "raw.accounts.number"}
    assert leakguard_values.real_value_hits(f"{extra}{digits}", values) == []
    assert leakguard_values.real_value_hits(f"{digits}{extra}", values) == []


@st.composite
def _spaced_cpf(draw):
    doc = _cpf(draw(st.text("0123456789", min_size=9, max_size=9)))
    groups = [doc[:3], doc[3:6], doc[6:9], doc[9:]]
    gaps = draw(st.lists(_DOC_GAP, min_size=3, max_size=3))
    return doc, "".join(g + sep for g, sep in zip(groups, [*gaps, ""], strict=True))


@st.composite
def _spaced_cnpj(draw):
    doc = _cnpj(draw(st.text("0123456789", min_size=12, max_size=12)))
    groups = [doc[:2], doc[2:5], doc[5:8], doc[8:12], doc[12:]]
    gaps = draw(st.lists(_DOC_GAP, min_size=4, max_size=4))
    return doc, "".join(g + sep for g, sep in zip(groups, [*gaps, ""], strict=True))


@settings(max_examples=200)
@given(_spaced_cpf().filter(lambda c: leakguard_patterns._cpf_valid(c[0])))
def test_valid_cpf_found_whatever_separates_its_groups(case):
    _, written = case
    failures = leakguard_files.check_text("notes.txt", f"cpf {written} ok\n", [])
    assert any(f.startswith("CPF:") for f in failures)


@settings(max_examples=200)
@given(_spaced_cnpj().filter(lambda c: leakguard_patterns._cnpj_valid(c[0])))
def test_valid_cnpj_found_whatever_separates_its_groups(case):
    _, written = case
    failures = leakguard_files.check_text("notes.txt", f"cnpj {written} ok\n", [])
    assert any(f.startswith("CNPJ:") for f in failures)


@given(_spaced_cpf().filter(lambda c: leakguard_patterns._cpf_valid(c[0])), st.sampled_from(["\n", "\r\n"]))
def test_cpf_across_a_line_break_is_not_a_document(case, newline):
    doc, _ = case
    text = f"{doc[:6]}{newline}{doc[6:]}"
    assert not any(f.startswith("CPF:") for f in leakguard_files.check_text("notes.txt", text, []))


@given(_DIGITS)
def test_numbers_in_a_comma_list_are_not_one_number(digits):
    text = ", ".join(digits)
    assert leakguard_values.real_value_hits(text, {digits: "raw.accounts.number"}) == []
