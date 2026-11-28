"""scripts/beads/check.py: the bead check (df-m60c) refuses a claim without an acceptance criterion,
a close without the labels and the note the measurement needs, and a child created with its
parent's labels. bd is faked, except in the
end-to-end tests at the bottom (skipped where bd is not installed, as in CI)."""

import importlib.util
import io
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
shellwords = sys.modules["shellwords"]  # scripts/shellwords.py, imported by check.py

NEEDS_BD = pytest.mark.skipif(not HAS_BD, reason="needs the bd CLI (not installed in CI)")
NEW = "2026-11-20T10:00:00Z"
OLD = "2026-11-01T10:00:00Z"
NOTE = "Conferi: os testes. Não conferi: nada. Assumi: que o bd está na versão 1.0.3."
BUG_LABELS = ["vol:1", "inc:2", "vol-real:1", "inc-real:2", "origem:descoberto", "camada:propriedade",
              "erro:dados",
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


# A small architecture model: three levels under casaHoje, one under casaAlvo, two loose boxes.
MODEL = {"elements": [{"id": i} for i in (
    "agendadorHoje", "casaAlvo", "casaAlvo.servidorAlvo", "casaHoje", "casaHoje.ninhoHoje",
    "casaHoje.ninhoHoje.listaHoje", "casaHoje.pipelineHoje", "donoAlvo")], "relations": []}
BOXES = check.box_ids(MODEL)


def refusals(command):
    """The messages of the line's refuse steps (the parent rule, the shortcuts), without the model."""
    return [s.message for s in check.read_line(command) if s.kind == "refuse"]


def create_gaps(command, monkeypatch, model=MODEL):
    """Every gap of the line with origin/main's model faked as `model`."""
    monkeypatch.setattr(check, "model_on_main", lambda _repo: model)
    return check.check_steps(check.read_line(command), FakeBd(), Path("."))


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


@pytest.mark.parametrize("command", [
    "bd create 'Filha' --parent df-a -l vol:1,inc:1,origem:planejado",
    "bd create 'Filha' --parent=df-a",
    "bd new 'Filha' -t bug --silent --parent df-a",
    "bd create --title 'Filha' --dry-run --parent df-a -f plano.md",
    'cd ~/src && mk(){ bd create "$1" --parent df-a -l "vol:$2"; }; mk Filha 1',
    "bd create 'Mãe' && bd create 'Filha' --parent df-a",
])
def test_a_child_created_with_the_parent_labels_is_refused(command):
    refused = refusals(command)
    assert len(refused) == 1
    assert "--no-inherit-labels" in refused[0]
    assert "df-a" in refused[0]


@pytest.mark.parametrize("command", [
    "bd create 'Filha' --parent df-a --no-inherit-labels -l vol:1,c4:transversal",
    "bd create 'Filha' --no-inherit-labels --parent=df-a -l c4:casaHoje",
    "bd new 'Filha' --silent --no-inherit-labels --parent df-a --labels c4:transversal",
    "bd create 'Sem mãe' -l vol:1,inc:1,origem:planejado,c4:casaHoje.ninhoHoje",
    "bd create 'Sem mãe' --parent '' -l c4:transversal",
    "bd create 'Fala de --parent df-a no título' -l c4:transversal",
    "bd create 'Sem mãe' -d 'criada com --parent df-a' -l c4:transversal",
    "bd list --parent df-a",
])
def test_a_create_that_copies_no_labels_passes(command, monkeypatch):
    assert create_gaps(command, monkeypatch) == []


CREATE_FLAGS = st.lists(st.sampled_from([
    ["-l", "vol:1,inc:2"], ["-t", "bug"], ["-p", "1"], ["--acceptance", "o teste passa"], ["-d", "porque sim"],
    ["--silent"], ["--dry-run"], ["--validate"], ["--force"], ["--json"], ["-f", "plano.md"],
]), max_size=6)


@given(CREATE_FLAGS, st.booleans(), st.sampled_from(["create", "new"]), st.randoms())
def test_a_create_with_a_parent_is_refused_exactly_when_it_inherits(flags, own_labels_only, sub, rnd):
    groups = [["Filha"], ["--parent", "df-a"], *flags, *([["--no-inherit-labels"]] if own_labels_only else [])]
    rnd.shuffle(groups)
    command = shlex.join(["bd", sub, *(word for group in groups for word in group)])
    assert len(refusals(command)) == (0 if own_labels_only else 1)


def test_an_unreadable_line_without_a_close_or_claim_passes():
    assert check.read_line("bd list --status open 'unbalanced") == []




# Excluded: a backslash-newline (read as a line continuation) and a reason made only of operators
# (shlex reads it as one; split_commands says why that is on the safe side).
@given(st.text().filter(lambda r: "\\\n" not in r and not (r and set(r) <= set(shellwords.OPERATOR_CHARS))))
def test_any_quoted_reason_reaches_the_check_unchanged(reason):
    steps = check.read_line(f"bd close df-x --reason {shlex.quote(reason)}")
    assert steps[0].reason == reason


# --------------------------------------------------------------------------- creating: the C4 box (df-8x0m.23.36)


def test_a_create_without_a_c4_label_is_refused_with_the_boxes(monkeypatch):
    found = create_gaps("bd create 'Nova' -l vol:1,inc:1", monkeypatch)
    assert len(found) == 1
    assert found[0].startswith("[bd create Nova]")
    for box in ("c4:transversal", "agendadorHoje", "donoAlvo", "casaHoje", "ninhoHoje.{listaHoje}",
                "pipelineHoje", "casaAlvo", "servidorAlvo"):
        assert box in found[0]
    assert "depois do merge" in found[0]


def test_the_boxes_are_grouped_by_their_first_segment():
    assert check.box_listing(BOXES) == [
        "soltos: agendadorHoje, donoAlvo",
        "casaAlvo.{servidorAlvo}",
        "casaHoje.{ninhoHoje.{listaHoje}, pipelineHoje}",
    ]


@pytest.mark.parametrize("labels, named", [
    ("c4:naoExiste", "`c4:naoExiste`"),
    ("c4:transversal,c4:casaHoje.nada", "`c4:casaHoje.nada`"),
    ("c4:", "`c4:`"),
    ("c4:ninhoHoje", "`c4:ninhoHoje`"),  # an id below the top needs its full path
])
def test_a_c4_label_that_is_not_a_box_is_refused_by_name(labels, named, monkeypatch):
    found = create_gaps(f"bd create Nova -l {labels}", monkeypatch)
    assert len(found) == 1
    assert named in found[0]
    assert "casaHoje" in found[0]


@pytest.mark.parametrize("command", [
    "bd create Nova -l c4:transversal",
    "bd create Nova -l c4:casaHoje.ninhoHoje.listaHoje",
    "bd create Nova -l c4:casaHoje,c4:casaAlvo.servidorAlvo",  # a bead that touches two boxes
    "bd create Nova -l vol:1 -l c4:donoAlvo",
    "bd create Nova --labels=c4:agendadorHoje",
    "bd create Nova -l=c4:casaAlvo",
    "bd create Nova --labels ' c4:casaHoje , vol:1'",
    "bd new Nova -l c4:transversal",
    "bd q Nova -l c4:transversal",
    "bd create Épico -t epic -l c4:casaHoje",
    "bd create Sessão -t session",
    "bd create Sessão --type session",
    "bd q Sessão --type=session -l c4:naoExiste",
    "bd create --help",
    "bd update df-x --claim --add-label c4:naoExiste",
])
def test_a_create_with_valid_boxes_or_of_a_session_passes(command, monkeypatch):
    assert create_gaps(command, monkeypatch) == []


@pytest.mark.parametrize("command", [
    "bd q Nova",
    "bd q Nova -l vol:1",
    "bd create Épico -t epic",
    "bd create Nova -t session -t task",  # the last --type is the one bd uses
    "bd create Nova -l c4:transversal --type=bug -l c4:naoExiste",
])
def test_every_spelling_of_a_create_is_checked(command, monkeypatch):
    assert len(create_gaps(command, monkeypatch)) == 1


@pytest.mark.parametrize("command", [
    "bd create -f plano.md -l c4:transversal",
    "bd create --file=plano.md",
    "bd create --graph plano.json",
])
def test_a_batch_create_is_refused_one_create_at_a_time(command, monkeypatch):
    found = create_gaps(command, monkeypatch)
    assert len(found) == 1
    assert "um por vez" in found[0]


@pytest.mark.parametrize("model", [None, {"elements": []}, {"elements": "x"}, {"other": 1}])
def test_without_a_readable_model_every_create_passes(model, monkeypatch):
    assert create_gaps("bd create Nova -l vol:1", monkeypatch, model) == []
    assert create_gaps("bd create -f plano.md", monkeypatch, model) == []


def test_box_ids_keep_only_string_ids_of_elements():
    model = {"elements": [{"id": "a"}, {"id": 3}, {"title": "no id"}, "x", {"id": ""}, {"id": "a.b"}]}
    assert check.box_ids(model) == {"a", "a.b"}
    assert check.box_ids(None) is None
    assert check.box_ids({"elements": [{"id": 1}]}) is None


def test_the_model_is_read_once_and_only_for_a_create(monkeypatch):
    reads = []
    monkeypatch.setattr(check, "model_on_main", lambda repo: reads.append(repo) or MODEL)
    assert check.check_steps(check.read_line("bd update df-x --claim; bd list"), FakeBd(), Path(".")) == []
    assert reads == []
    found = check.check_steps(check.read_line("bd create A -l vol:1 && bd create B"), FakeBd(), Path("."))
    assert [f.split("]")[0] for f in found] == ["[bd create A", "[bd create B"]
    assert len(reads) == 1


def test_the_boxes_come_from_origin_main(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "arquitetura/modelo.json", json.dumps(MODEL), "Model (df-t)")
    assert check.check_line("bd create Nova -l vol:1", FakeBd(), repo) == []  # not merged yet: fails open
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    assert check.check_line("bd create Nova -l c4:casaHoje.pipelineHoje", FakeBd(), repo) == []
    assert "c4:naoExiste" in check.check_line("bd create Nova -l c4:naoExiste", FakeBd(), repo)[0]


LABEL_SPELLINGS = st.sampled_from(["-l", "--labels", "-l=", "--labels="])
C4_VALUES = st.sampled_from(sorted(BOXES) + ["transversal", "naoExiste", "casaHoje.nada", "", "listaHoje"])
OTHER_LABELS = st.sampled_from(["vol:1", "inc:2", "origem:planejado", "c4x:casaHoje", "modo"])


@given(st.lists(st.tuples(LABEL_SPELLINGS, st.one_of(C4_VALUES.map(lambda v: f"c4:{v}"), OTHER_LABELS)),
                max_size=5),
       st.sampled_from([None, "task", "bug", "epic", "session"]), st.sampled_from(["create", "new", "q"]),
       st.randoms())
def test_a_create_is_refused_exactly_when_it_lacks_a_valid_box(spelled, kind, sub, rnd):
    labels = [lab for _, lab in spelled]
    groups = [[f"{s}{lab}"] if s.endswith("=") else [s, lab] for s, lab in spelled]
    groups += [["Nova"], *([["-t", kind]] if kind else [])]
    rnd.shuffle(groups)
    steps = check.read_line(shlex.join(["bd", sub, *(word for group in groups for word in group)]))
    assert [s.kind for s in steps] == ["create"]
    values = [lab[len("c4:"):] for lab in labels if lab.startswith("c4:")]
    ok = kind == "session" or (values and all(v in BOXES | {"transversal"} for v in values))
    assert (check.c4_problems(steps[0], BOXES) == []) == bool(ok)


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
    ("erro:", "`erro:`"),
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
    old_bug = issue(labels=["origem:descoberto", "camada:propriedade", "erro:dados"], created=OLD)
    assert close(old_bug, reason="") == []
    assert "`camada:`" in close(issue(labels=["origem:descoberto"], created=OLD), reason="")[0]


# df-ygqm: these were layers before; they are kinds of error now, and go in `erro:`.
@pytest.mark.parametrize("old", ["dados", "crash", "windows", "instavel", "dependencias"])
def test_an_old_layer_is_refused_as_camada_and_points_to_erro(old):
    found = close(issue(labels=[*without(BUG_LABELS, "camada:"), f"camada:{old}"]))
    assert len(found) == 1
    assert f"`camada:{old}` não vale" in found[0]
    assert "`erro:`" in found[0]


# df-xm62: CI is where a check runs, not which check it is; SonarQube is one.
def test_sonar_is_a_layer_and_ci_is_not():
    assert close(issue(labels=[*without(BUG_LABELS, "camada:"), "camada:sonar"])) == []
    found = close(issue(labels=[*without(BUG_LABELS, "camada:"), "camada:ci"]))
    assert len(found) == 1
    assert "`camada:ci` não vale" in found[0]


@pytest.mark.parametrize("kind", ["dados", "crash", "qualquer-coisa"])
def test_erro_takes_any_kind(kind):
    assert close(issue(labels=[*without(BUG_LABELS, "erro:"), f"erro:{kind}"])) == []


def test_an_empty_erro_is_a_gap():
    found = close(issue(labels=[*without(BUG_LABELS, "erro:"), "erro:"]))
    assert len(found) == 1
    assert "`erro:`" in found[0]


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
ESTIMATE = ["vol:1", "inc:2"]


def test_a_new_bead_without_acceptance_criteria_cannot_be_claimed():
    found = check.claim_problems(issue("df-f", "feature", ESTIMATE), CRITERIA, acceptance=False)
    assert len(found) == 1
    assert 'bd update df-f --acceptance "..."' in found[0]


@pytest.mark.parametrize(("issue_", "lint", "acceptance"), [
    (issue("df-f", "feature", ESTIMATE), [], False),                      # lint is happy
    (issue("df-f", "feature", ESTIMATE), CRITERIA, True),                 # given in the same line
    (issue("df-f", "feature", ESTIMATE), None, False),                    # lint failed: fail open
    (issue("df-f", "feature", [], created=OLD), CRITERIA, False),         # before the cutoff
    (issue("df-b", "bug", ESTIMATE), ["## Steps to Reproduce"], False),   # only criteria block
    (issue("df-s", "session", []), CRITERIA, False),
    (issue("df-e", "epic", []), [], False),                               # an epic is not estimated
])
def test_a_claim_passes(issue_, lint, acceptance):
    assert check.claim_problems(issue_, lint, acceptance) == []


LEVEL = st.sampled_from(check.LEVELS)
NOT_A_LEVEL = st.text(max_size=3).filter(lambda v: v not in check.LEVELS)


@given(st.one_of(st.none(), LEVEL, NOT_A_LEVEL), st.one_of(st.none(), LEVEL, NOT_A_LEVEL),
       st.sampled_from([[], None, CRITERIA]), st.booleans(), st.sampled_from(["task", "bug", "feature", "decision"]))
def test_a_claim_names_exactly_the_axes_of_the_estimate_that_are_missing(vol, inc, lint, in_the_line, kind):
    given_ = {"vol": vol, "inc": inc}
    labels = [f"{axis}:{value}" for axis, value in given_.items() if value is not None]
    bead = issue("df-t", kind, [] if in_the_line else labels)
    found = check.claim_problems(bead, lint, True, labels if in_the_line else ())
    missing = [axis for axis, value in given_.items() if value not in check.LEVELS]
    assert len(found) == len(missing)
    for axis, gap in zip(missing, found, strict=True):
        assert gap.startswith(f"`{axis}:`")
        assert f"bd label add df-t {axis}:<valor>" in gap


def test_an_estimate_given_in_the_claim_itself_counts():
    bd = FakeBd(issue("df-t", "task", []))
    assert check.check_line("bd update df-t --add-label vol:1,inc:2 --claim", bd, Path()) == []
    assert len(check.check_line("bd update df-t --add-label vol:1 --claim", bd, Path())) == 1


def test_an_epic_needs_its_success_criteria():
    assert "Success Criteria" in check.claim_problems(issue("df-e", "epic", []), ["## Success Criteria"], False)[0]


# --------------------------------------------------------------------------- one value per label (df-b89w)


SINGLE = st.sampled_from(check.SINGLE_VALUED)
VALUE = st.sampled_from(["1", "2", "3", "planejado", "descoberto", "defeito"])


@given(SINGLE, st.lists(VALUE, min_size=2, max_size=3, unique=True))
def test_two_values_of_a_single_valued_label_stop_claim_and_close(key, values):
    doubled = [*without(BUG_LABELS, key + ":"), *(f"{key}:{v}" for v in values)]
    for found in (check.claim_problems(issue(labels=doubled), [], False), close(issue(labels=doubled))):
        named = [f for f in found if "em dobro" in f]
        assert len(named) == 1
        assert f"bd label remove df-bug {key}:" in named[0]
        assert all(f"`{key}:{v}`" in named[0] for v in values)


@given(st.lists(st.tuples(SINGLE, VALUE), max_size=8))
def test_one_value_per_label_is_never_a_duplicate(pairs):
    labels = [f"{k}:{v}" for k, v in dict(pairs).items()]
    assert check.duplicate_problems({*labels, "ci", "furo-de:df-a", "furo-de:df-b"}, "df-x") == []


def test_a_second_value_set_in_the_same_line_counts_and_old_beads_are_checked_too():
    labels = ["vol:2", "inc:2", "origem:descoberto"]
    old = issue("df-t", "task", labels, created=OLD)
    assert "`vol:` em dobro" in check.claim_problems(old, None, True, {"vol:1"})[0]
    steps = check.read_line("bd update df-t --add-label inc:1 --claim")
    assert "`inc:` em dobro" in check.check_steps(steps, FakeBd(issue("df-t", "task", labels)), Path())[0]


def test_session_beads_and_the_waiver_skip_the_duplicate_check():
    doubled = ["vol:1", "vol:2"]
    assert check.claim_problems(issue("df-s", "session", doubled), [], False) == []
    assert close(issue(labels=[*doubled, check.WAIVER])) == []


@NEEDS_BD
def test_end_to_end_a_child_that_inherits_the_parent_estimate_cannot_be_claimed(bdrepo):
    parent = bd_create(bdrepo, "Mãe", "-l", "vol:2,inc:2,origem:descoberto", "--acceptance", "x")
    child = bd_create(bdrepo, "Filha", "--parent", parent, "-l", "vol:1", "--acceptance", "x")
    assert "`vol:` em dobro" in run_hook(bdrepo, f"bd update {child} --claim")
    bd_json(bdrepo, "label", "remove", child, "vol:2")
    assert run_hook(bdrepo, f"bd update {child} --claim") is None
    clean = bd_create(bdrepo, "Outra", "--parent", parent, "--no-inherit-labels", "-l", "vol:1", "--acceptance", "x")
    assert "`inc:`" in run_hook(bdrepo, f"bd update {clean} --claim")
    bd_json(bdrepo, "label", "add", clean, "inc:1")
    assert run_hook(bdrepo, f"bd update {clean} --claim") is None


@NEEDS_BD
def test_end_to_end_a_child_is_created_only_without_the_parent_labels(bdrepo):
    parent = bd_create(bdrepo, "Mãe", "-l", "vol:2,inc:2,origem:descoberto", "--acceptance", "x")
    assert "--no-inherit-labels" in run_hook(bdrepo, f"bd create Filha --parent {parent} -l vol:1")
    allowed = ["create", "Filha", "--parent", parent, "--no-inherit-labels", "-l", "vol:1"]
    assert run_hook(bdrepo, shlex.join(["bd", *allowed])) is None
    assert bd_json(bdrepo, "show", bd_json(bdrepo, *allowed)["id"])[0]["labels"] == ["vol:1"]


# --------------------------------------------------------------------------- the whole line


def test_check_line_prefixes_each_gap_with_its_command(tmp_path):
    bd = FakeBd(issue(labels=without(BUG_LABELS, "achado-por:")), issue("df-f", "feature", ESTIMATE),
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


def test_the_same_line_hint_shows_only_for_a_claim_or_close():
    hint = "etiquetas na mesma linha"
    assert hint in check.render(["[bd close df-x] nota de fechamento"])
    assert hint in check.render(["[bd create A] falta `c4:`", "[bd update df-x --claim] `vol:`"])
    assert hint not in check.render(["[bd create A] falta `c4:`"])
    assert hint not in check.render(["`bd create --parent df-a` sem `--no-inherit-labels`"])


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


# --------------------------------------------------------------------------- marks of the architecture target


TASK_DONE = ["vol:1", "inc:1", "vol-real:1", "inc-real:1", "origem:planejado"]


def marked_model(bead):
    return json.dumps({"elements": [{"id": "casaAlvo.app", "tags": ["muda"], "metadata": {"bead": bead}}],
                       "relations": []})


@pytest.fixture
def marked_main(tmp_path):
    """origin/main whose arquitetura/modelo.json marks casaAlvo.app with df-t."""
    repo = make_repo(tmp_path)
    commit(repo, "arquitetura/modelo.json", marked_model("df-t"), "Mark (df-t)")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    return repo


def test_a_close_is_refused_while_origin_main_still_marks_the_bead(marked_main):
    bd = FakeBd(issue("df-t", "task", TASK_DONE))
    found = check.check_line(f'bd close df-t --reason "{NOTE}"', bd, marked_main)
    assert len(found) == 1
    assert "casaAlvo.app" in found[0]
    assert "arquitetura/alvo.c4" in found[0]


def test_the_mark_counts_even_for_a_dropped_bead_but_not_with_the_waiver(marked_main):
    bd = FakeBd(issue("df-t", "task", TASK_DONE))
    assert check.check_line('bd close df-t --reason "descartado: x"', bd, marked_main) != []
    waived = FakeBd(issue("df-t", "task", [check.WAIVER]))
    assert check.check_line('bd close df-t --reason "x"', waived, marked_main) == []


def test_only_origin_main_counts_not_the_local_checkout(marked_main):
    commit(marked_main, "arquitetura/modelo.json", marked_model("df-other"), "Move the mark (df-t)")
    assert check.mark_problems(check.model_on_main(marked_main), "df-t") != []
    git(marked_main, "push", "-q", "origin", "main")
    git(marked_main, "fetch", "-q", "origin")
    assert check.mark_problems(check.model_on_main(marked_main), "df-t") == []
    assert check.mark_problems(check.model_on_main(marked_main), "df-other") != []


@pytest.mark.parametrize("text", [None, "", "{not json", "[1, 2]"])
def test_no_model_or_an_unreadable_one_lets_the_close_through(tmp_path, text):
    repo = make_repo(tmp_path)
    if text is not None:
        commit(repo, "arquitetura/modelo.json", text, "Broken model")
        git(repo, "push", "-q", "origin", "main")
        git(repo, "fetch", "-q", "origin")
    assert check.model_on_main(repo) is None
    assert check.mark_problems(check.model_on_main(repo), "df-t") == []


def test_model_on_main_reads_the_model_of_origin_main(marked_main):
    assert check.model_on_main(marked_main) == json.loads(marked_model("df-t"))


# --------------------------------------------------------------------------- served by prod


SERVED_MODEL = {"elements": [{"id": i, "tags": t} for i, t in (
    ("casaHoje", []), ("casaHoje.ninhoHoje", []), ("casaHoje.ninhoHoje.listaHoje", ["servidoPelaProd"]),
    ("casaHoje.ninhoHoje.listaHoje.itensHoje", []), ("casaHoje.ninhoHoje.pessoasCliHoje", []),
    ("casaHoje.ninhoHojeVelho", []), ("casaHoje.pipelineHoje", []), ("casaHoje.lagoHoje", ["servidoPelaProd"]),
    ("casaAlvo", []), ("casaAlvo.servidorAlvo", ["muda"]),
    ("casaAlvo.servidorAlvo.nucleoAlvo", ["muda", "servidoPelaProd"]))], "relations": []}
SERVED_PROOF = "Servido pela prod: Invoke-WebRequest .../tables/transacoes 200."
CODE_CHANGED = {"ninho/lista/rotas.py", "arquitetura/hoje.c4"}


def served_task(*c4, labels=TASK_DONE, kind="task", created=NEW):
    return issue("df-t", kind, [*labels, *(f"c4:{v}" for v in c4)], created)


def prod(issue_, reason=NOTE, model=SERVED_MODEL, extra=(), changed=CODE_CHANGED):
    return check.prod_problems(model, issue_, reason, frozenset(extra), lambda _b: set(changed))


@pytest.mark.parametrize("box", [
    "casaHoje.ninhoHoje.listaHoje", "casaHoje.lagoHoje",  # tagged itself
    "casaHoje.ninhoHoje.listaHoje.itensHoje",  # under a tagged element
    "casaHoje.ninhoHoje", "casaAlvo.servidorAlvo",  # a container holding a tagged component
])
def test_a_box_prod_serves_its_child_or_its_container_needs_the_prod_check(box):
    found = prod(served_task(box))
    assert len(found) == 1
    assert f"`c4:{box}`" in found[0]
    assert "Invoke-WebRequest" in found[0]
    assert "8100" in found[0]
    assert "Servido pela prod:" in found[0]


@pytest.mark.parametrize("boxes", [
    ("casaHoje",), ("casaAlvo",),  # a top box does not reach down to its tagged elements
    ("casaHoje.ninhoHoje.pessoasCliHoje",), ("casaHoje.ninhoHojeVelho",), ("casaHoje.pipelineHoje",),
    ("transversal",), (),
])
def test_a_box_outside_what_prod_serves_passes(boxes):
    assert prod(served_task(*boxes)) == []


@pytest.mark.parametrize("reason", [
    f"{NOTE} {SERVED_PROOF}", f"{SERVED_PROOF} {NOTE}",
    "Conferi: x. servido  PELA prod : app aberto, 200. Não conferi: y. Assumi: z.",
    f"Servido pela prod: {NOTE} {SERVED_PROOF}",  # an empty mark before a filled one
    f"Não servido pela prod: falta o deploy. {SERVED_PROOF} {NOTE}",
])
def test_any_filled_prod_mark_in_the_reason_passes(reason):
    assert prod(served_task("casaHoje.lagoHoje"), reason) == []


@pytest.mark.parametrize("reason", [f"{NOTE} Servido pela prod:", f"Servido pela prod: {NOTE}",
                                    f"{NOTE} Servido pela prod: ... ;",
                                    f"{NOTE} Servido pela prod: Servido pela prod:"])
def test_the_prod_mark_without_text_is_refused(reason):
    found = prod(served_task("casaHoje.lagoHoje"), reason)
    assert len(found) == 1
    assert "sem texto" in found[0]


@pytest.mark.parametrize("reason", [f"{NOTE} Não servido pela prod: falta o deploy.",
                                    f"{NOTE} nao servido pela prod: x"])
def test_a_negated_prod_mark_is_no_mark(reason):
    found = prod(served_task("casaHoje.lagoHoje"), reason)
    assert len(found) == 1
    assert "falta `Servido pela prod:`" in found[0]


def test_a_label_added_in_the_same_line_counts():
    assert prod(served_task(), extra={"c4:casaHoje.lagoHoje"}) != []


@pytest.mark.parametrize("changed", [{"arquitetura/hoje.c4"}, {"arquitetura/hoje.c4", "arquitetura/modelo.json"}])
def test_a_bead_that_changed_only_the_drawing_passes(changed):
    assert prod(served_task("casaHoje.lagoHoje"), changed=changed) == []


@pytest.mark.parametrize("changed", [set(), {"arquitetura/hoje.c4", "ninho/lista/rotas.py"}, {"arquitetura.md"}])
def test_no_changed_files_or_code_outside_the_drawing_is_still_asked(changed):
    assert prod(served_task("casaHoje.lagoHoje"), changed=changed) != []


@pytest.mark.parametrize("passes", [
    {"reason": "descartado: virou df-x"}, {"reason": "Duplicado: df-y"},
    {"issue_": served_task("casaHoje.lagoHoje", labels=[*TASK_DONE, check.WAIVER])},
    {"issue_": served_task("casaHoje.lagoHoje", kind="epic")},
    {"issue_": served_task("casaHoje.lagoHoje", kind="session")},
    {"issue_": served_task("casaHoje.lagoHoje", kind="decision")},
    {"issue_": served_task("casaHoje.lagoHoje", created=OLD)},
    {"model": None}, {"model": {"elements": "x"}},
    {"model": {"elements": [{"id": "casaHoje.lagoHoje", "tags": "servidoPelaProd"}, {"tags": ["servidoPelaProd"]}]}},
])
def test_dropped_waived_session_epic_decision_old_beads_and_no_model_pass(passes):
    args = {"issue_": served_task("casaHoje.lagoHoje"), **passes}
    assert prod(**args) == []


def test_the_tag_sits_on_the_smallest_elements_of_the_real_model():
    model = json.loads((SCRIPT.parents[2] / "arquitetura" / "modelo.json").read_text(encoding="utf-8"))
    served = check.served_boxes(model)
    assert check.SERVED_TAG in model["specification"]["tags"]
    assert {"casaHoje.dataforgeHoje.apiHoje", "casaHoje.ninhoHoje.servidorHoje", "casaHoje.lagoHoje",
            "casaAlvo.servidorAlvo.nucleoAlvo"} <= served
    assert not {"casaHoje.dataforgeHoje", "casaHoje.ninhoHoje", "casaAlvo.servidorAlvo"} & served
    assert check.served_hits({"c4:casaHoje.dataforgeHoje.cliHoje", "c4:casaHoje.dataforgeHoje"}, served) == [
        "casaHoje.dataforgeHoje"]


def test_a_module_screen_of_the_real_model_needs_the_prod_check():
    # df-2z3v: each module screen builds its route and reads the answer, so it is tagged too.
    model = json.loads((SCRIPT.parents[2] / "arquitetura" / "modelo.json").read_text(encoding="utf-8"))
    screen = served_task("casaHoje.telasNinhoHoje.financasTelasHoje")
    found = prod(screen, model=model)
    assert found
    assert "Servido pela prod:" in found[0]
    assert prod(screen, f"{NOTE} {SERVED_PROOF}", model=model) == []
    assert {"casaHoje.telasNinhoHoje.listaTelasHoje", "casaHoje.telasNinhoHoje.desejosTelasHoje",
            "casaAlvo.appCelularAlvo.listaTelasAlvo", "casaAlvo.appCelularAlvo.desejosTelasAlvo",
            "casaAlvo.appCelularAlvo.financasTelasAlvo",
            "casaAlvo.appCelularAlvo.agendaTelasAlvo", "casaHoje.frontFinancasHoje.cascaFinancasHoje",
            "casaHoje.frontFinancasHoje.telasFinancasHoje"} <= check.served_boxes(model)


def test_a_close_line_without_the_prod_check_is_refused_and_with_it_passes(monkeypatch):
    reads = []
    monkeypatch.setattr(check, "model_on_main", lambda repo: reads.append(repo) or SERVED_MODEL)
    monkeypatch.setattr(check, "changed_files", lambda _repo, _bead: set(CODE_CHANGED))
    bd = FakeBd(served_task("casaHoje.lagoHoje"), issue("df-u", "task", [*TASK_DONE, "c4:casaHoje.lagoHoje"]))
    found = check.check_steps(check.read_line(f'bd close df-t df-u --reason "{NOTE}"'), bd, Path("."))
    assert [f.split("]")[0] for f in found] == ["[bd close df-t", "[bd close df-u"]
    assert "Invoke-WebRequest" in found[0]
    assert len(reads) == 1  # one git show for the whole line, marks and prod check together
    proof = f'bd close df-t --reason "{NOTE} {SERVED_PROOF}"'
    assert check.check_steps(check.read_line(proof), bd, Path(".")) == []


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
    bead = bd_create(bdrepo, "Sem critério", "-t", "feature", "-l", "vol:1,inc:1")
    assert "critério de aceite" in run_hook(bdrepo, f"bd update {bead} --claim")
    bd_json(bdrepo, "update", bead, "--acceptance", "o teste X passa")
    assert run_hook(bdrepo, f"bd update {bead} --claim") is None


@NEEDS_BD
def test_end_to_end_close_of_a_bug(bdrepo):
    bead = bd_create(bdrepo, "Um bug", "-t", "bug", "-l", "origem:descoberto,camada:propriedade,erro:dados")
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


@pytest.mark.parametrize("bead", ["--db=/tmp/other", "-x", "df x", "DF-1", ""])
def test_a_word_that_is_not_a_bead_id_never_reaches_bd(bead, monkeypatch, tmp_path):
    def no_run(*_a, **_k):
        raise AssertionError("bd was called")
    monkeypatch.setattr(check.subprocess, "run", no_run)
    assert check.Bd(tmp_path).show(bead) is None


@pytest.mark.parametrize("bead", ["df-m60c", "df-ahua.23.4", "df-sess-1a2b3c4d"])
def test_bead_ids_bd_writes_are_valid(bead):
    assert check.BEAD_ID.match(bead)


# --------------------------------------------------------------------------- context at claim (df-8x0m.23.40)

CTX_MODEL = {
    "elements": [{"id": i, "title": i.upper()} for i in ("a", "a.x", "a.x.sub", "a.y", "b", "c", "d")],
    "relations": [{"source": "a.x", "target": "b"}, {"source": "c", "target": "a.x"},
                  {"source": "a.x.sub", "target": "d"}, {"source": "a.y", "target": "c"}],
}
CTX_DOCS = {"a.x": "docs/x.md", "a": "docs/a.md", "b": "docs/b.md"}
CTX_FRONT = {"docs/x.md": {"topic": "Tópico X", "read_when": "ao mexer em X"}, "docs/a.md": {"topic": "A"}}


def ctx_event(command, cwd, response=None):
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(cwd),
            "tool_input": {"command": command},
            "tool_response": response or {"stdout": "ok", "stderr": "", "interrupted": False}}


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(check, "model_on_main", lambda _repo: CTX_MODEL)
    monkeypatch.setattr(check.arq_docs, "docs_por_elemento", lambda _m: CTX_DOCS)
    monkeypatch.setattr(check, "front_on_main", lambda _repo, path: CTX_FRONT.get(path, {}))

    def use(*issues):
        monkeypatch.setattr(check, "Bd", lambda _cwd: FakeBd(*issues))
    return use


def ctx_text(out):
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    return out["hookSpecificOutput"]["additionalContext"]


def test_neighbours_are_the_box_its_parent_and_its_own_relations():
    assert check.neighbours(CTX_MODEL, "a.x") == ["a.x", "a", "b", "c"]  # not d: a.x.sub's relation
    assert check.neighbours(CTX_MODEL, "transversal") == []


def test_a_claim_gives_the_docs_of_the_box_and_its_neighbours(ctx, tmp_path):
    ctx(issue("df-k", "task", ["c4:a.x"]))
    text = ctx_text(check.contexto(ctx_event("bd update df-k --claim", tmp_path), tmp_path))
    assert "- a.x (A.X): docs/x.md · topic: Tópico X · read_when: ao mexer em X" in text
    assert "- a (A): docs/a.md · topic: A" in text
    assert "- b (B): docs/b.md" in text
    assert "- c (C): sem doc" in text
    assert "d (D)" not in text
    assert "a.y" not in text


def test_the_context_is_capped(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(check, "MAX_CONTEXT", 2)
    ctx(issue("df-k", "task", ["c4:a.x"]))
    text = ctx_text(check.contexto(ctx_event("bd update df-k --claim", tmp_path), tmp_path))
    assert text.count("\n- ") == 3
    assert text.endswith("- ... e mais 2")


@pytest.mark.parametrize("labels", [["c4:transversal"], [], ["c4:nao.existe"]])
def test_a_transversal_bead_or_one_without_a_box_gets_nothing(ctx, tmp_path, labels):
    ctx(issue("df-k", "task", labels))
    assert check.contexto(ctx_event("bd update df-k --claim", tmp_path), tmp_path) is None


@pytest.mark.parametrize("command", ["bd show df-k", "bd update df-k --status open", "ls", "bd close df-k"])
def test_no_claim_gives_nothing(ctx, tmp_path, command):
    ctx(issue("df-k", "task", ["c4:a.x"]))
    assert check.contexto(ctx_event(command, tmp_path), tmp_path) is None


def test_a_failed_or_unknown_bd_gives_nothing(ctx, tmp_path):
    ctx()  # bd cannot answer
    assert check.contexto(ctx_event("bd update df-k --claim", tmp_path), tmp_path) is None
    ctx(issue("df-k", "task", ["c4:a.x"]))
    interrupted = {"stdout": "", "stderr": "", "interrupted": True}
    assert check.contexto(ctx_event("bd update df-k --claim", tmp_path, interrupted), tmp_path) is None


def test_no_model_or_no_docs_module_gives_nothing(ctx, tmp_path, monkeypatch):
    ctx(issue("df-k", "task", ["c4:a.x"]))
    monkeypatch.setattr(check, "arq_docs", None)
    assert check.contexto(ctx_event("bd update df-k --claim", tmp_path), tmp_path) is None


def test_the_front_matter_is_read_from_origin_main(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "docs/x.md", "---\ntopic: X\nread_when: sempre\ncode:\n  - a.py\nstatus: esboço\n---\n# X\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "doc")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert check.front_on_main(repo, "docs/x.md")["topic"] == "X"
    assert check.front_on_main(repo, "docs/nao.md") == {}


def test_the_contexto_mode_fails_open(tmp_path):
    r = subprocess.run([sys.executable, "-S", str(SCRIPT), "contexto"], input="not json", capture_output=True,
                       text=True, cwd=tmp_path, env={"PATH": "/nonexistent", "CLAUDE_PROJECT_DIR": str(tmp_path)},
                       check=False)
    assert (r.returncode, r.stdout) == (0, "")


def test_the_cut_keeps_every_own_box_before_parents_and_far_ends(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(check, "MAX_CONTEXT", 3)
    ctx(issue("df-k", "task", ["c4:a.y", "c4:a.x"]))
    text = ctx_text(check.contexto(ctx_event("bd update df-k --claim", tmp_path), tmp_path))
    assert [line.split(" ")[1] for line in text.splitlines()[1:4]] == ["a.x", "a.y", "a"]
    assert text.endswith("- ... e mais 2")  # b and c, the far ends of both boxes, once each


def test_no_model_on_main_gives_nothing(ctx, tmp_path, monkeypatch):
    ctx(issue("df-k", "task", ["c4:a.x"]))
    monkeypatch.setattr(check, "model_on_main", lambda _repo: None)
    assert check.contexto(ctx_event("bd update df-k --claim", tmp_path), tmp_path) is None


def test_a_line_with_two_claims_gives_both(ctx, tmp_path):
    ctx(issue("df-k", "task", ["c4:a.x"]), issue("df-j", "task", ["c4:b"]))
    text = ctx_text(check.contexto(ctx_event("bd update df-k --claim && bd update df-j --claim", tmp_path), tmp_path))
    assert text.index("df-k:") < text.index("df-j:")


def test_main_contexto_dispatches_to_the_context(ctx, tmp_path, monkeypatch, capsys):
    ctx(issue("df-k", "task", ["c4:a.x"]))
    monkeypatch.setattr(check.sys, "stdin", io.StringIO(
        json.dumps(ctx_event("bd update df-k --claim", tmp_path))))
    assert check.main(["contexto"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "docs/x.md" in out["hookSpecificOutput"]["additionalContext"]
