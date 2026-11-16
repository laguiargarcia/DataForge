"""scripts/beads/check.py: the bead check (df-m60c) refuses a claim without an acceptance criterion
and a close without the labels and the note the measurement needs. bd is faked, except in the
end-to-end tests at the bottom (skipped where bd is not installed, as in CI)."""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from tests.sessions_repo import HAS_BD, add_worktree, bd_init, bd_json, git, make_repo, write

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "beads" / "check.py"
_spec = importlib.util.spec_from_file_location("bead_check", SCRIPT)
check = importlib.util.module_from_spec(_spec)
sys.modules["bead_check"] = check  # dataclasses look their module up
_spec.loader.exec_module(check)

NEEDS_BD = pytest.mark.skipif(not HAS_BD, reason="needs the bd CLI (not installed in CI)")
NEW = "2026-11-20T10:00:00Z"
OLD = "2026-11-01T10:00:00Z"
NOTE = "Conferi: os testes. Não conferi: nada. Assumi: que o bd está na versão 1.0.3."
BUG_LABELS = ["vol:1", "inc:2", "vol-real:1", "inc-real:2", "origem:descoberto", "camada:propriedade",
              "achado-por:teste", "furo-de:nenhum"]


def issue(bead="df-bug", kind="bug", labels=BUG_LABELS, created=NEW):
    return {"id": bead, "issue_type": kind, "labels": list(labels), "created_at": created}


def without(labels, *prefixes):
    return [lab for lab in labels if not lab.startswith(prefixes)]


def close(issue_, reason=NOTE, extra=(), known=("df-src",), candidates=lambda _b: []):
    return check.close_problems(issue_, reason, frozenset(extra), lambda b: b in known, candidates)


def kinds(steps):
    return [(s.kind, s.bead) for s in steps]


class FakeBd:
    def __init__(self, *issues, lint=None):
        self.issues = {i["id"]: i for i in issues}
        self.lint = lint or {}
        self.calls = []

    def show(self, bead):
        self.calls.append(("show", bead))
        return self.issues.get(bead)

    def exists(self, bead):
        return bead in self.issues

    def lint_missing(self, bead):
        self.calls.append(("lint", bead))
        return self.lint.get(bead, [])


# --------------------------------------------------------------------------- reading the command


def test_a_command_without_bd_has_nothing_to_check():
    assert check.read_line("git status && pytest -q") == []
    assert check.read_line('echo "bd close df-x" > notes.txt') == []
    assert check.read_line("grep -rn bdclose .") == []


@pytest.mark.parametrize("command", [
    'bd close df-x --reason "r"',
    "bd close df-x -r r",
    "bd close df-x --reason=r",
    "bd -q --actor someone close df-x --reason r",
    "bd done df-x --reason r",
    "BEADS_ACTOR=a env bd close df-x --reason r",
    "cd /tmp && bd close df-x --reason r 2>&1 | tail -3",
    "bd close df-x --reason r > /dev/null",
    "for i in 1; do bd close df-x --reason r; done",
])
def test_every_spelling_of_a_close_is_read(command):
    steps = check.read_line(command)
    assert kinds(steps) == [("close", "df-x")]
    assert steps[0].reason == "r"


def test_arrows_in_a_quoted_reason_are_not_redirections():
    assert check.read_line('bd close df-x --reason "Conferi: a -> b <c>" 2>/dev/null')[0].reason == "Conferi: a -> b <c>"


def test_a_close_of_several_beads_checks_each():
    assert kinds(check.read_line("bd close df-a df-b --reason r")) == [("close", "df-a"), ("close", "df-b")]


def test_labels_and_acceptance_set_earlier_in_the_line_count():
    steps = check.read_line(
        "bd label add df-x achado-por:teste && bd update df-x --add-label furo-de:nenhum,etapa:plano\n"
        "bd close df-x --reason r")
    assert steps[0].labels == {"achado-por:teste", "furo-de:nenhum", "etapa:plano"}
    claim = check.read_line('bd update df-y --acceptance "passa o teste X" --claim')
    assert kinds(claim) == [("claim", "df-y")]
    assert claim[0].acceptance


def test_labels_of_another_bead_do_not_count():
    steps = check.read_line("bd label add df-other achado-por:teste; bd close df-x --reason r")
    assert steps[0].labels == frozenset()


def test_a_claim_of_several_beads_checks_each():
    assert kinds(check.read_line("bd update df-a df-b --claim")) == [("claim", "df-a"), ("claim", "df-b")]


def test_an_update_without_claim_is_not_a_step():
    assert check.read_line("bd update df-a --notes 'x' --status in_progress") == []


@pytest.mark.parametrize("command", [
    "bd update df-x --status closed",
    "bd update df-x -s closed",
    "bd update df-x --status=closed",
    "bd close",
    "bd close --reason 'x'",
    "bd close df-x --claim-next --reason r",
    'bd close "$ID" --reason r',
    "bd update `cat id` --claim",
    'bd close df-x --reason "unbalanced',
])
def test_shortcuts_around_the_check_are_refused(command):
    steps = check.read_line(command)
    assert [s.kind for s in steps] == ["refuse"]
    assert steps[0].message


def test_an_unreadable_line_without_a_close_or_claim_passes():
    assert check.read_line("bd list --status open 'unbalanced") == []




# Excluded: a backslash-newline (read as a line continuation) and a reason made only of operators
# (shlex reads it as one; split_commands says why that is on the safe side).
@given(st.text().filter(lambda r: "\\\n" not in r and not (r and set(r) <= set(check.OPERATOR_CHARS))))
def test_any_quoted_reason_reaches_the_check_unchanged(reason):
    steps = check.read_line(f"bd close df-x --reason {shlex.quote(reason)}")
    assert steps[0].reason == reason


# --------------------------------------------------------------------------- the note


def test_a_note_with_the_three_marks_passes_in_any_order_and_case():
    assert check.note_problem(NOTE) is None
    assert check.note_problem("assumi: x\nNAO CONFERI: y\nconferi: z") is None


@pytest.mark.parametrize(("reason", "missing"), [
    ("Não conferi: a. Assumi: b.", "Conferi:"),
    ("Conferi: a. Assumi: b.", "Não conferi:"),
    ("Conferi: a. Não conferi: b.", "Assumi:"),
    ("Conferi: Não conferi: b. Assumi: c.", "Conferi:"),
    ("Conferi: a. Não conferi: b. Assumi: .", "Assumi:"),
    ("", "Conferi:, Não conferi:, Assumi:"),
])
def test_a_note_without_a_mark_or_its_text_is_a_gap(reason, missing):
    assert f"Falta: {missing}." in check.note_problem(reason)


MARK = st.sampled_from(["Conferi:", "Não conferi:", "Assumi:"])
TEXT = st.text(alphabet="abc xyz.", min_size=1).filter(lambda t: any(c.isalpha() for c in t))


@given(st.permutations(["Conferi:", "Não conferi:", "Assumi:"]), st.lists(TEXT, min_size=3, max_size=3),
       st.integers(0, 2))
def test_dropping_any_mark_is_noticed(order, texts, drop):
    full = " ".join(f"{m} {t}" for m, t in zip(order, texts, strict=True))
    assert check.note_problem(full) is None
    partial = " ".join(f"{m} {t}" for i, (m, t) in enumerate(zip(order, texts, strict=True)) if i != drop)
    assert order[drop] in check.note_problem(partial)


# --------------------------------------------------------------------------- closing


def test_a_bug_with_everything_closes():
    assert close(issue()) == []


@pytest.mark.parametrize(("dropped", "named"), [
    ("achado-por:", "`achado-por:`"),
    ("furo-de:", "`furo-de:`"),
    ("camada:", "`camada:`"),
    ("origem:", "`origem:`"),
    ("vol-real:", "`vol-real:`"),
    ("inc-real:", "`inc-real:`"),
])
def test_a_bug_missing_a_label_names_it_with_the_fix(dropped, named):
    found = close(issue(labels=without(BUG_LABELS, dropped)))
    assert len(found) == 1
    assert named in found[0]
    assert "bd label add df-bug" in found[0]


def test_a_bug_without_the_note_is_refused():
    found = close(issue(), reason="consertado")
    assert len(found) == 1
    assert "nota de fechamento" in found[0]


def test_a_value_outside_the_list_is_a_gap_that_shows_the_list():
    found = close(issue(labels=[*without(BUG_LABELS, "achado-por:"), "achado-por:sorte"]))
    assert "`achado-por:sorte` não vale" in found[0]
    assert all(v in found[0] for v in check.ACHADO_POR)


def test_a_missing_furo_de_suggests_the_candidates():
    found = close(issue(labels=without(BUG_LABELS, "furo-de:")), candidates=lambda _b: ["df-a", "df-b"])
    assert "Candidatos (commits recentes dos arquivos alterados): df-a, df-b." in found[0]


def test_furo_de_a_bead_needs_the_etapa_and_the_bead_to_exist():
    base = without(BUG_LABELS, "furo-de:")
    assert "`etapa:`" in close(issue(labels=[*base, "furo-de:df-src"]))[0]
    assert close(issue(labels=[*base, "furo-de:df-src", "etapa:revisao"])) == []
    assert "não é um bead existente" in close(issue(labels=[*base, "furo-de:df-gone", "etapa:plano"]))[0]
    assert "não é um bead existente" in close(issue(labels=[*base, "furo-de:df-bug"]))[0]


def test_a_task_needs_origem_and_the_note_but_not_the_bug_labels():
    labels = ["origem:planejado"]
    assert close(issue("df-t", "task", labels)) == []
    assert "nota de fechamento" in close(issue("df-t", "task", labels), reason="feito")[0]


def test_a_task_that_points_a_furo_also_needs_the_etapa():
    assert "`etapa:`" in close(issue("df-t", "task", ["origem:defeito", "furo-de:df-src"]))[0]


def test_real_difficulty_is_asked_only_of_beads_with_an_estimate():
    assert close(issue("df-t", "task", ["origem:planejado"])) == []
    found = close(issue("df-t", "task", ["origem:planejado", "vol:2"]))
    assert len(found) == 1
    assert "`vol-real:`" in found[0]


def test_a_bead_older_than_the_cutoff_keeps_only_the_older_rules():
    old_bug = issue(labels=["origem:descoberto", "camada:dados"], created=OLD)
    assert close(old_bug, reason="") == []
    assert "`camada:`" in close(issue(labels=["origem:descoberto"], created=OLD), reason="")[0]


def test_labels_added_earlier_in_the_line_count_at_close():
    assert close(issue(labels=without(BUG_LABELS, "achado-por:")), extra={"achado-por:lucas"}) == []


@pytest.mark.parametrize("passes", [
    {"labels": [check.WAIVER]},
    {"kind": "session", "labels": []},
    {"kind": "epic", "labels": []},
])
def test_the_waiver_session_beads_and_epics_pass(passes):
    assert close(issue(**passes), reason="") == []


@pytest.mark.parametrize("reason", ["descartado: o problema sumiu com df-a", "Duplicado: df-b"])
def test_a_close_without_work_needs_only_the_reason(reason):
    assert close(issue(labels=[]), reason=reason) == []


# --------------------------------------------------------------------------- claiming


CRITERIA = ["## Acceptance Criteria"]


def test_a_new_bead_without_acceptance_criteria_cannot_be_claimed():
    found = check.claim_problems(issue("df-f", "feature", []), CRITERIA, acceptance=False)
    assert len(found) == 1
    assert 'bd update df-f --acceptance "..."' in found[0]


@pytest.mark.parametrize(("issue_", "lint", "acceptance"), [
    (issue("df-f", "feature", []), [], False),                            # lint is happy
    (issue("df-f", "feature", []), CRITERIA, True),                       # given in the same line
    (issue("df-f", "feature", []), None, False),                          # lint failed: fail open
    (issue("df-f", "feature", [], created=OLD), CRITERIA, False),         # before the cutoff
    (issue("df-b", "bug", []), ["## Steps to Reproduce"], False),         # only criteria block
    (issue("df-s", "session", []), CRITERIA, False),
])
def test_a_claim_passes(issue_, lint, acceptance):
    assert check.claim_problems(issue_, lint, acceptance) == []


def test_an_epic_needs_its_success_criteria():
    assert "Success Criteria" in check.claim_problems(issue("df-e", "epic", []), ["## Success Criteria"], False)[0]


# --------------------------------------------------------------------------- the whole line


def test_check_line_prefixes_each_gap_with_its_command(tmp_path):
    bd = FakeBd(issue(labels=without(BUG_LABELS, "achado-por:")), issue("df-f", "feature", []),
                lint={"df-f": CRITERIA})
    found = check.check_line(f"bd close df-bug --reason {shlex.quote(NOTE)}; bd update df-f --claim", bd, tmp_path)
    assert [f.split("]")[0] for f in found] == ["[bd close df-bug", "[bd update df-f --claim"]


def test_an_unknown_bead_is_left_to_bd(tmp_path):
    assert check.check_line("bd close df-nope --reason x", FakeBd(), tmp_path) == []


def test_a_line_without_bd_calls_nothing(tmp_path):
    bd = FakeBd()
    assert check.check_line("ls -la", bd, tmp_path) == []
    assert bd.calls == []


def bash_event(command, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(cwd),
            "tool_input": {"command": command}}


def test_the_hook_denies_with_the_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "Bd", lambda _cwd: FakeBd(issue()))
    out = check.hook(bash_event("bd close df-bug --reason feito", tmp_path), tmp_path)
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "nota de fechamento" in decision["permissionDecisionReason"]
    assert check.hook(bash_event(f"bd close df-bug --reason {shlex.quote(NOTE)}", tmp_path), tmp_path) is None


def test_the_hook_ignores_other_tools(tmp_path):
    assert check.hook({"tool_name": "Edit", "tool_input": {"command": "bd close df-x"}}, tmp_path) is None


def test_by_hand_the_exit_code_says_whether_it_would_pass(monkeypatch, capsys):
    monkeypatch.setattr(check, "Bd", lambda _cwd: FakeBd(issue()))
    assert check.main(["close", "df-bug", "--reason", "feito"]) == 1
    assert "nota de fechamento" in capsys.readouterr().out
    assert check.main(["close", "df-bug", "--reason", NOTE]) == 0
    assert check.main([]) == 2


def test_the_hook_fails_open_on_bad_input(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": staticmethod(lambda: "not json")})())
    assert check.main(["hook"]) == 0
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- furo-de candidates


def commit(repo, path, text, message):
    write(repo / path, text)
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def history(tmp_path):
    """origin/main where a.py was touched by df-old, then df-new (twice), and b.py by df-b."""
    repo = make_repo(tmp_path)
    commit(repo, "a.py", "1\n", "Add a (df-old)")
    commit(repo, "a.py", "2\n", "Change a (df-new)")
    commit(repo, "a.py", "3\n", "Tune a\n\nFollow-up of df-new")
    commit(repo, "b.py", "1\n", "Add b (df-b)")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    return repo


def test_candidates_come_from_the_commits_of_the_files_the_bug_changed(history):
    git(history, "checkout", "-q", "-b", "fix/df-bug")
    commit(history, "a.py", "4\n", "Fix a (df-bug)")
    assert check.furo_candidates(history, "df-bug") == ["df-new", "df-old"]


def test_candidates_include_the_uncommitted_work_of_the_bug_worktree(history):
    wt = add_worktree(history, "df-bug")
    write(wt / "b.py", "changed\n")
    assert check.furo_candidates(history, "df-bug") == ["df-b"]


def test_a_bead_id_is_matched_whole_not_as_a_prefix(history):
    git(history, "checkout", "-q", "-b", "fix/df-bug.12")
    commit(history, "b.py", "2\n", "Fix b (df-bug.12)")
    assert check.furo_candidates(history, "df-bug.1") == []
    assert check.furo_candidates(history, "df-bug.12") == ["df-b"]


def test_no_work_found_means_no_candidates(history):
    assert check.furo_candidates(history, "df-bug") == []


# --------------------------------------------------------------------------- with a real bd


@pytest.fixture
def bdrepo(tmp_path):
    if not HAS_BD:
        pytest.skip("needs the bd CLI (not installed in CI)")
    repo = make_repo(tmp_path)
    bd_init(repo)
    return repo


def bd_create(repo, *args):
    return bd_json(repo, "create", *args)["id"]


def run_hook(repo, command):
    r = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=json.dumps(bash_event(command, repo)),
                       capture_output=True, text=True, cwd=repo,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}, check=True)
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if r.stdout else None


@NEEDS_BD
def test_end_to_end_claim_needs_the_acceptance_criterion(bdrepo):
    bead = bd_create(bdrepo, "Sem critério", "-t", "feature")
    assert "critério de aceite" in run_hook(bdrepo, f"bd update {bead} --claim")
    bd_json(bdrepo, "update", bead, "--acceptance", "o teste X passa")
    assert run_hook(bdrepo, f"bd update {bead} --claim") is None


@NEEDS_BD
def test_end_to_end_close_of_a_bug(bdrepo):
    bead = bd_create(bdrepo, "Um bug", "-t", "bug", "-l", "origem:descoberto,camada:propriedade")
    reason = shlex.quote(NOTE)
    refused = run_hook(bdrepo, f"bd close {bead} --reason {reason}")
    assert "`achado-por:`" in refused
    assert "`furo-de:`" in refused
    assert "nota de fechamento" in run_hook(
        bdrepo, f"bd label add {bead} achado-por:teste && bd label add {bead} furo-de:nenhum && "
                f"bd close {bead} --reason feito")
    assert run_hook(bdrepo, f"bd label add {bead} achado-por:teste && bd label add {bead} furo-de:nenhum && "
                            f"bd close {bead} --reason {reason}") is None
    bd_json(bdrepo, "label", "add", bead, check.WAIVER)
    assert run_hook(bdrepo, f"bd close {bead} --reason feito") is None
