"""The leak guard's patterns: personal data known by its shape (a CPF or CNPJ with valid check digits,
an agency/account code, a personal e-mail, the owner's name) and how a finding is shown. Part of
scripts/leakguard.py (df-n8ac.36), whose docstring describes the whole guard."""
import os
import re

from scripts import leakguard_checkout

# A document's digits may be split by punctuation, spaces or tabs (not by a line break); a trailing
# sentence period is not a digit, and digits inside a longer number are not a document. A letter or
# '_' next to it does not hide one ("cpf_" then the digits).
_NOT_BEFORE = r"(?<!\d)(?<!\d[.,])"
_NOT_AFTER = r"(?!\d)(?![.,]\d)"
# Between the groups any run (or none) of the characters people put between digits: space, tab, '.',
# '_', '/', '-', en and em dash, or a single comma ("123.456.789 - 00", "123_456_789_00"). Never a line break: a
# document wrapped across lines is two things, not one (the real-value check does join those).
# A wrong guess costs little: only a match whose check digits are valid is a finding.

# One gap between digits: a run of the given characters, or a single comma (a comma and a space is
# a list of numbers, not a gap). Module-level expressions, not helper functions: mutmut cannot test
# what only runs at import.
_RUN_OR_COMMA = r"(?:[{}]+|,)"
_DOC_GAP = _RUN_OR_COMMA.format(r"\t .\-_/\u2013\u2014") + "?"
_D2, _D3, _D4 = r"\d{2}", r"\d{3}", r"\d{4}"
CPF = re.compile(_NOT_BEFORE + _DOC_GAP.join([_D3, _D3, _D3, _D2]) + _NOT_AFTER)
CNPJ = re.compile(_NOT_BEFORE + _DOC_GAP.join([_D2, _D3, _D3, _D4, _D2]) + _NOT_AFTER)
# Files of hex digests and package names only, where a run of digits inside a hash may pass a
# check-digit rule: the document patterns do not apply there (every other check does).
DIGEST_FILES = {"uv.lock"}
# Agency/account short codes. "2026-4-10" is a date, not an account.
ACCT = re.compile(r"\b\d{4,5}-\d\b(?!-\d)")
EMAIL = re.compile(
    r"(?i)\b[\w.+-]+@(?:gmail|googlemail|outlook|hotmail|live|msn|yahoo|icloud|me|uol|bol|terra|ig"
    r"|globo|globomail|r7|zipmail|proton|protonmail|pm|tutanota|gmx|aol|yandex|mail)"
    r"\.(?:com|me|net|org|ch|de)(?:\.br)?\b")
CPF_LENGTH = 11
CNPJ_LENGTH = 14
CNPJ_WEIGHTS = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

# Files where the owner's name is INTENTIONAL attribution (MIT copyright, README author), not a
# leak: the NAME check is skipped there, and a real value read from an owner column is let through.
NAME_EXEMPT_PATHS = {"LICENSE", "README.md"}


def _load_owner_name_patterns(root=None):
    """The owner's name patterns, never hardcoded here (that would publish the name): from the
    LEAKGUARD_NAME_PATTERNS variable (os.pathsep-separated regexes) and the gitignored
    '.leakguard-local' at the main checkout's root (one regex per line; '#' comments). On a fresh
    public clone both are absent and the NAME check is skipped; every other check still applies."""
    raw = [s for s in os.environ.get("LEAKGUARD_NAME_PATTERNS", "").split(os.pathsep) if s.strip()]
    local = (root or leakguard_checkout.MAIN_ROOT) / ".leakguard-local"
    if local.is_file():
        lines = (line.strip() for line in local.read_text(encoding="utf-8", errors="ignore").splitlines())
        raw += [line for line in lines if line and not line.startswith("#")]
    return [re.compile(p, re.IGNORECASE) for p in raw]


NAME_PATTERNS = _load_owner_name_patterns()


def _digits(value):
    return re.sub(r"\D", "", value)


def _check_digits_hold(digits, weights):
    """Each check digit is the mod-11 rule over the digits before it, with the weights that end there."""
    for size in (len(digits) - 2, len(digits) - 1):
        total = sum(int(d) * w for d, w in zip(digits[:size], weights[len(weights) - size:], strict=True))
        if (0 if total % 11 < 2 else 11 - total % 11) != int(digits[size]):   # noqa: PLR2004 -- the rule itself
            return False
    return True


def _cpf_valid(digits):
    return len(digits) == CPF_LENGTH and len(set(digits)) > 1 and _check_digits_hold(digits, range(11, 1, -1))


def _cnpj_valid(digits):
    return len(digits) == CNPJ_LENGTH and len(set(digits)) > 1 and _check_digits_hold(digits, CNPJ_WEIGHTS)


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def _mask(value, about=""):
    return f"'{value[:2]}…' ({len(value)} chars{about})"


def _pattern_checks(rel):
    """(label, regex, test the match must pass to be a finding or None) for one file."""
    checks = [] if rel in DIGEST_FILES else [("CPF", CPF, lambda s: _cpf_valid(_digits(s))),
                                             ("CNPJ", CNPJ, lambda s: _cnpj_valid(_digits(s)))]
    checks += [("ACCT", ACCT, lambda s: len(set(_digits(s))) > 1),   # "0000-0" is a filler
               ("EMAIL", EMAIL, None)]
    if rel not in NAME_EXEMPT_PATHS:
        checks += [("NAME", rx, None) for rx in NAME_PATTERNS]
    return checks


def _pattern_failures(rel, text, where):
    return [f"{label}: {where(_line(text, m.start()))} -> {_mask(m.group(0))}"
            for label, rx, keep in _pattern_checks(rel)
            for m in rx.finditer(text)
            if keep is None or keep(m.group(0))]
