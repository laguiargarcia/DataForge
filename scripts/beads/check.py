"""Conferência do bead (df-m60c): a bead starts with an acceptance criterion and closes with the
labels and the note the measurement needs.

Three bd commands are checked, nothing else:

- `bd create` (`bd new`, `bd q`): needs a `c4:` label, and each `c4:<v>` it carries names an element
  of the architecture model on origin/main (`arquitetura/modelo.json`, its fqn) or `transversal`; the
  refusal lists the valid ids. Session beads are free; a batch (`--file`, `--graph`) is refused. A box
  made on a branch counts only after its merge; no model on origin/main, no check (df-8x0m.23.36).
- `bd create --parent <id>`: needs `--no-inherit-labels`, so the child carries only the labels its
  creator typed and a missing estimate shows as missing instead of as the parent's (df-081f).
- `bd update <id> --claim`: the bead needs an acceptance criterion (what `bd lint` asks of its type)
  and an estimate, `vol:` and `inc:` (df-ugnz): without one it never reaches the measurement.
- Both: at most one value of each of `vol:`, `inc:`, `origem:`, `vol-real:` and `inc-real:` (a child
  made with `bd create --parent` inherits the parent's labels; df-b89w).
- `bd close <id> --reason "..."`: real difficulty (`vol-real:`, `inc-real:`), `origem:`; for bugs
  `camada:`, `erro:`, `achado-por:` and `furo-de:` (a bead id or `nenhum`), and `etapa:` whenever `furo-de:`
  names a bead; and a reason carrying the three marks `Conferi:`, `Não conferi:` and `Assumi:`.
  When `furo-de:` is missing, the message suggests the beads cited by the recent commits of the
  files the bug changed. And, for any bead, no mark of the architecture target on origin/main
  (`arquitetura/modelo.json`) may still name it: the mark goes, or moves to the bead that carries
  the change on, in a PR merged before the close (df-8x0m.23.37; no modelo.json, no check).

Passes untouched: session beads and epics at close, a reason starting with `descartado:` or
`duplicado:`, a bead labelled `fechamento:dispensado` (the escape hatch, counted by the
measurement). The architecture mark check is the exception: it still covers sessions, epics and
`descartado:`/`duplicado:` closes (a dropped bead must not stay as a mark); only the label skips it.
A bead created before CUTOFF is held only to the older rules (real difficulty, origem, camada and
erro) and its claim is not checked. Labels and the acceptance criterion given earlier in
the same command line (`bd label add ... && bd close ...`) count as present.

Usage:
    check.py hook                               PreToolUse hook: the event on stdin, a deny on stdout
    check.py close <id> --reason "..."          by hand, with bd's own arguments; exit 1 lists gaps
    check.py update <id> --claim
    check.py create "Título" -l c4:<caixa>,...

Fails open: an internal error, a missing bd or an unknown bead lets the command through (bd itself
complains about the last). The one exception is a `bd close` or claim this script cannot read
(unbalanced quotes): it is refused with a request to pass the reason as a quoted string.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/: shellwords

from arquitetura.marcas import marked_beads
from shellwords import split_commands, strip_prefix

CUTOFF = "2026-11-16"  # ISO date: beads created from this day on get every check
WAIVER = "fechamento:dispensado"
NO_WORK_PREFIXES = ("descartado:", "duplicado:")
CLOSE_SKIPPED_TYPES = frozenset({"session", "epic"})
CLAIM_SKIPPED_TYPES = frozenset({"session"})
LEVELS = ("1", "2", "3")
ORIGENS = ("planejado", "descoberto", "defeito")
SINGLE_VALUED = ("vol", "inc", "origem", "vol-real", "inc-real")  # two values: the measurement drops the bead
# The check that should have caught a bug: the claudemain base list (DataForge adds none). The kind
# of error (dados, crash, windows...) goes in the free `erro:` label.
CAMADAS = ("teste", "propriedade", "gui", "estatica", "tipos", "mutacao", "fim-a-fim", "sonar", "nenhuma")
ACHADO_POR = ("teste", "portao", "ci", "sonar", "revisao", "leitura", "lucas", "uso")
ETAPAS = ("investigacao", "plano", "implementacao", "revisao", "teste")
NO_FURO = "nenhum"
MAX_CANDIDATES = 5
COMMITS_PER_FILE = 3
MAX_FILES = 40
BD_TIMEOUT = 20
GIT_TIMEOUT = 10
MODEL_ON_MAIN = "origin/main:arquitetura/modelo.json"

BD_WORD = re.compile(r"(?:^|[\s;&|(`])bd(?:$|\s)")
BEAD_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.\d+)*$")
NOTE_MARK = re.compile(r"\b(n[ãa]o\s+conferi|conferi|assumi)\s*:", re.IGNORECASE)
NOTE_NAMES = {"conferi": "Conferi:", "nao conferi": "Não conferi:", "assumi": "Assumi:"}
GLOBAL_BOOL_FLAGS = frozenset({"--global", "--json", "--profile", "-q", "--quiet", "--readonly",
                               "--sandbox", "-v", "--verbose", "-h", "--help"})
SUBCOMMAND_BOOL_FLAGS = {
    "create": frozenset({"--dry-run", "--ephemeral", "--force", "--no-history", "--no-inherit-labels",
                         "--silent", "--stdin", "--validate"}),
    "close": frozenset({"--claim-next", "--continue", "-f", "--force", "--no-auto", "--suggest-next"}),
    "update": frozenset({"--claim", "--allow-empty-description", "--ephemeral", "--history",
                         "--no-history", "--persistent", "--stdin"}),
}
SHORT_FLAGS = {"-r": "--reason", "-s": "--status", "-f": "--force"}
CREATE_SUBCOMMANDS = frozenset({"create", "new", "q"})
CREATE_SHORT_FLAGS = {"-f": "--file", "-l": "--labels", "-t": "--type"}  # `-f` is `--force` everywhere else
BATCH_FLAGS = ("--file", "--graph")  # beads come from a file: their labels cannot be read here
C4 = "c4"
C4_ANYWHERE = "transversal"  # the box of work that is not in one part: how we work, several parts
LABEL_FLAGS = ("--add-label", "--set-labels")
SAME_LINE_STEPS = ("[bd update ", "[bd close ")  # gaps that labels set earlier in the line can fill
LABEL_ADD_WORDS = 3  # `label add <id> <label>`: add, at least one id, the label


# --------------------------------------------------------------------------- reading the command


@dataclass
class BdCall:
    sub: str
    positionals: list[str] = field(default_factory=list)
    options: dict[str, list[str]] = field(default_factory=dict)

    def values(self, flag: str) -> list[str]:
        return self.options.get(flag, [])

    def has(self, flag: str) -> bool:
        return flag in self.options


def _read_args(call: BdCall, rest: list[str]) -> None:
    """Fill the call's positionals and options from the words after its subcommand."""
    create = call.sub in CREATE_SUBCOMMANDS
    bool_flags = SUBCOMMAND_BOOL_FLAGS.get("create" if create else call.sub, frozenset()) | GLOBAL_BOOL_FLAGS
    short_flags = SHORT_FLAGS | CREATE_SHORT_FLAGS if create else SHORT_FLAGS
    words = iter(rest)
    for word in words:
        if word == "--":
            call.positionals += list(words)
        elif not word.startswith("-") or word == "-":
            call.positionals.append(word)
        else:
            flag, eq, value = word.partition("=")
            flag = short_flags.get(flag, flag)
            if not eq and flag not in bool_flags:
                value = next(words, "")
            call.options.setdefault(flag, []).append(value)


def parse_bd(words: list[str]) -> BdCall | None:
    """The subcommand, positionals and options of a `bd ...` word list; None if it is not bd."""
    if not words or os.path.basename(words[0]) != "bd":
        return None
    i = 1
    while i < len(words) and words[i].startswith("-"):  # global flags before the subcommand
        i += 1 if words[i] in GLOBAL_BOOL_FLAGS or "=" in words[i] else 2
    if i >= len(words):
        return None
    call = BdCall(words[i])
    _read_args(call, words[i + 1 :])
    return call


@dataclass
class Step:
    """One thing the line does that this script checks, in order."""

    kind: str  # "claim", "close", "create" or "refuse"
    bead: str = ""
    reason: str = ""
    message: str = ""
    # claim and close: the labels the line added to this bead before the step; create: the labels the
    # create itself passes (-l, --labels); refuse: none
    labels: frozenset[str] = frozenset()
    acceptance: bool = False  # an acceptance criterion given earlier in the line, or in the claim
    title: str = ""  # a create's title
    issue_type: str = ""  # a create's --type, empty for bd's default
    batch: bool = False  # a create from a file (--file, --graph)


class LineState:
    """What the line has already set before each step: labels and acceptance criteria per bead."""

    def __init__(self) -> None:
        self.labels: dict[str, set[str]] = {}
        self.acceptance: set[str] = set()
        self.steps: list[Step] = []

    def add_labels(self, beads: list[str], labels: list[str]) -> None:
        for bead in beads:
            self.labels.setdefault(bead, set()).update(lab.strip() for lab in labels if lab.strip())

    def step(self, kind: str, bead: str, **kw) -> None:
        self.steps.append(Step(kind, bead, labels=frozenset(self.labels.get(bead, ())),
                               acceptance=bead in self.acceptance, **kw))

    def refuse(self, message: str) -> None:
        self.steps.append(Step("refuse", message=message))

    def unresolved(self, beads: list[str], what: str) -> bool:
        bad = [b for b in beads if "$" in b or "`" in b]
        if bad:
            self.refuse(f"`{what} {' '.join(bad)}`: o id vem de uma variável, que não dá para conferir "
                        "antes de rodar. Escreva o id literal.")
        return bool(bad)

    def update(self, call: BdCall) -> None:
        beads = call.positionals
        labels = [lab for flag in LABEL_FLAGS for v in call.values(flag) for lab in v.split(",")]
        self.add_labels(beads, labels)
        if any(v.strip() for v in call.values("--acceptance")):
            self.acceptance.update(beads)
        if any(v.strip().lower() == "closed" for v in call.values("--status")):
            self.refuse("`bd update --status closed` fecha sem conferência. Feche com "
                        "`bd close <id> --reason \"...\"`.")
        if call.has("--claim") and not self.unresolved(beads, "bd update --claim"):
            for bead in beads:
                self.step("claim", bead)

    def create(self, call: BdCall) -> None:
        parents = [v for v in call.values("--parent") if v.strip()]
        if parents and not call.has("--no-inherit-labels"):
            self.refuse(f"`bd create --parent {parents[0]}` sem `--no-inherit-labels`: a filha copia as "
                        "etiquetas da mãe e a estimativa dela (`vol:`, `inc:`, `origem:`) passa por "
                        "estimativa da filha. Acrescente `--no-inherit-labels` e dê à filha as "
                        "etiquetas dela em `-l`.")
        if call.has("--help") or call.has("-h"):
            return  # bd only prints the help
        labels = frozenset(lab.strip() for v in call.values("--labels") for lab in v.split(",") if lab.strip())
        types = call.values("--type")
        title = (call.positionals or call.values("--title") or [""])[0]
        self.steps.append(Step("create", labels=labels, title=title, issue_type=types[-1].strip() if types else "",
                               batch=any(call.has(f) for f in BATCH_FLAGS)))

    def close(self, call: BdCall) -> None:
        if not call.positionals:
            self.refuse("`bd close` sem id fecha o último bead tocado. Diga qual: `bd close <id> ...`.")
            return
        if call.has("--claim-next"):
            self.refuse("`bd close --claim-next` reivindica outro bead sem conferir o critério de "
                        "aceite. Feche e depois reivindique com `bd update <id> --claim`.")
            return
        if self.unresolved(call.positionals, "bd close"):
            return
        reason = "\n".join(call.values("--reason"))
        for bead in call.positionals:
            self.step("close", bead, reason=reason)

    def feed(self, call: BdCall) -> None:
        if call.sub == "label" and call.positionals[:1] == ["add"] and len(call.positionals) >= LABEL_ADD_WORDS:
            self.add_labels(call.positionals[1:-1], [call.positionals[-1]])
        elif call.sub == "update":
            self.update(call)
        elif call.sub in CREATE_SUBCOMMANDS:
            self.create(call)
        elif call.sub in {"close", "done"}:
            self.close(call)


def read_line(command: str) -> list[Step]:
    """The claims and closes a shell line will run, each with what the line set before it."""
    if not BD_WORD.search(command):
        return []
    line = LineState()
    try:
        commands = split_commands(command)
    except ValueError:
        if re.search(r"\bbd\s+(close|done|update)\b", command):
            line.refuse("não consegui ler este comando (aspas sem par?). Passe o motivo entre aspas "
                        "simples ou duplas, sem heredoc: `bd close <id> --reason \"...\"`.")
        return line.steps
    return read_commands(commands, line)


def read_commands(commands: list[list[str]], line: LineState | None = None) -> list[Step]:
    """The claims and closes of already split simple commands (word lists), in order."""
    line = line or LineState()
    for words in commands:
        call = parse_bd(strip_prefix(words))
        if call is not None:
            line.feed(call)
    return line.steps


# --------------------------------------------------------------------------- the rules


def label_values(labels: set[str] | frozenset[str], key: str) -> list[str]:
    prefix = key + ":"
    return [lab[len(prefix):] for lab in labels if lab.startswith(prefix)]


def need(labels, key: str, allowed: tuple[str, ...], bead: str, what: str) -> str | None:
    """A gap if the bead has no `key:<v>` with v in `allowed`; the message lists the values."""
    found = label_values(labels, key)
    if any(v in allowed for v in found):
        return None
    wrong = f" (`{key}:{found[0]}` não vale)" if found else ""
    return (f"`{key}:` — {what}{wrong}. Valores: {', '.join(allowed)}. "
            f"`bd label add {bead} {key}:<valor>`")


def need_free(labels, key: str, bead: str, what: str) -> str | None:
    """A gap if the bead has no `key:<v>` with a non-empty v (any value goes)."""
    if any(v.strip() for v in label_values(labels, key)):
        return None
    return f"`{key}:` — {what}. Valor livre. `bd label add {bead} {key}:<valor>`"


def duplicate_problems(labels, bead: str) -> list[str]:
    """A gap for each single-valued label the bead carries with two different values (df-b89w)."""
    out = []
    for key in SINGLE_VALUED:
        values = sorted(set(label_values(labels, key)))
        if len(values) > 1:
            both = ", ".join(f"`{key}:{v}`" for v in values)
            out.append(f"`{key}:` em dobro ({both}) — fique só com o valor deste bead (uma filha herda as "
                       f"etiquetas da mãe; crie com `--no-inherit-labels`). Antes, em outro comando: "
                       f"`bd label remove {bead} {key}:<o que sobra>`")
    return out


def note_problem(reason: str) -> str | None:
    """A gap unless the reason carries Conferi:, Não conferi: and Assumi:, each followed by text."""
    marks = list(NOTE_MARK.finditer(reason))
    filled = set()
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(reason)
        if re.search(r"\w", reason[m.end():end]):
            key = re.sub(r"\s+", " ", m.group(1).lower()).replace("ã", "a")
            filled.add(key)
    missing = [name for key, name in NOTE_NAMES.items() if key not in filled]
    if not missing:
        return None
    return ("nota de fechamento — o `--reason` traz `Conferi:`, `Não conferi:` e `Assumi:`, cada um "
            f"com texto (pode ser \"nada\"). Falta: {', '.join(missing)}. Reescreva o `--reason`.")


def difficulty_problems(labels, bead: str) -> list[str]:
    out = []
    for axis, what in (("vol", "quanto trabalho deu de fato"), ("inc", "quanto precisou investigar de fato")):
        if label_values(labels, axis):
            out.append(need(labels, f"{axis}-real", LEVELS, bead, what))
    return out


def estimate_problems(labels, bead: str) -> list[str | None]:
    """A gap for each axis of the estimate (`vol:`, `inc:`) the bead does not have (df-ugnz)."""
    return [need(labels, axis, LEVELS, bead, what)
            for axis, what in (("vol", "quanto trabalho deve dar"), ("inc", "quanto deve precisar investigar"))]


def furo_problems(labels, bead: str, exists, candidates) -> list[str]:
    """`furo-de:` (a bead id or `nenhum`) on every bug, and `etapa:` when it names a bead."""
    furos = label_values(labels, "furo-de")
    if not furos:
        found = candidates(bead)
        hint = (f" Candidatos (commits recentes dos arquivos alterados): {', '.join(found)}."
                if found else " Nenhum candidato nos commits dos arquivos alterados.")
        return [f"`furo-de:` — de qual bead este bug escapou, ou `nenhum` se não dá para apontar "
                f"(código anterior aos beads, causa externa).{hint} `bd label add {bead} furo-de:<id>` "
                f"ou `bd label add {bead} furo-de:{NO_FURO}`"]
    target = furos[0]
    if target == NO_FURO:
        return []
    if target == bead or not BEAD_ID.match(target) or not exists(target):
        return [f"`furo-de:{target}` não é um bead existente (nem `{NO_FURO}`). "
                f"`bd label remove {bead} furo-de:{target}` e ponha o certo."]
    return [need(labels, "etapa", ETAPAS, bead, f"a etapa de {target} que deveria ter pego o problema")]


def close_problems(issue: dict, reason: str, extra_labels, exists, candidates) -> list[str]:
    """Gaps that keep `bd close` of this bead from going ahead (empty: it may close)."""
    labels = set(issue.get("labels") or ()) | set(extra_labels)
    if issue.get("issue_type") in CLOSE_SKIPPED_TYPES or WAIVER in labels:
        return []
    if reason.strip().lower().startswith(NO_WORK_PREFIXES):
        return []
    bead = issue["id"]
    bug = issue.get("issue_type") == "bug"
    found = duplicate_problems(labels, bead) + difficulty_problems(labels, bead)
    found.append(need(labels, "origem", ORIGENS, bead, "de onde veio o bead"))
    if bug:
        found.append(need(labels, "camada", CAMADAS, bead,
                          "a verificação que deveria ter pego o bug (o tipo do erro vai em `erro:`)"))
        found.append(need_free(labels, "erro", bead, "o tipo do erro (ex.: dados, crash, windows, instavel)"))
    if str(issue.get("created_at") or "")[:10] >= CUTOFF:
        if bug:
            found.append(need(labels, "achado-por", ACHADO_POR, bead, "quem pegou o problema de fato"))
            found += furo_problems(labels, bead, exists, candidates)
        elif label_values(labels, "furo-de"):
            found += furo_problems(labels, bead, exists, candidates)
        found.append(note_problem(reason))
    return [f for f in found if f]


def claim_problems(issue: dict, lint_missing: list[str] | None, acceptance: bool,
                   extra_labels=()) -> list[str]:
    """Gaps that keep `bd update --claim` of this bead from going ahead."""
    if issue.get("issue_type") in CLAIM_SKIPPED_TYPES:
        return []
    bead = issue["id"]
    labels = set(issue.get("labels") or ()) | set(extra_labels)
    found = duplicate_problems(labels, bead)
    if str(issue.get("created_at") or "")[:10] < CUTOFF:
        return found
    if issue.get("issue_type") not in CLOSE_SKIPPED_TYPES:  # an epic is not measured: no real difficulty at close
        found += [f for f in estimate_problems(labels, bead) if f]
    if acceptance or lint_missing is None:
        return found
    missing = [m.lstrip("# ").strip() for m in lint_missing if "Criteria" in m]
    if not missing:
        return found
    return [*found, f"critério de aceite — como saber que ficou pronto ({', '.join(missing)}). "
            f"`bd update {bead} --acceptance \"...\"` e depois reivindique."]


def box_ids(model: dict | None) -> set[str] | None:
    """The element ids (fqns) of the architecture model; None when it has none to offer."""
    elements = (model or {}).get("elements")
    ids = {e["id"] for e in elements if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"]} \
        if isinstance(elements, list) else set()
    return ids or None


def _tree(ids) -> str:
    """The ids as one line, children in braces after their parent: {a, a.b, a.b.c, d} is `a.{b.{c}}, d`."""
    children: dict[str, list[str]] = {}
    for i in sorted(ids):
        head, _, rest = i.partition(".")
        children.setdefault(head, [])
        if rest:
            children[head].append(rest)
    return ", ".join(head + (f".{{{_tree(rest)}}}" if rest else "") for head, rest in children.items())


def box_listing(ids: set[str]) -> list[str]:
    """The valid ids grouped by first segment: the loose boxes on one line, then each top box with
    everything below it (`casaHoje.{ninhoHoje.{listaHoje}}`: each level is an id of its own)."""
    tops = sorted({i.split(".", 1)[0] for i in ids})
    nested = [t for t in tops if any(i.startswith(t + ".") for i in ids)]
    loose = [t for t in tops if t not in nested]
    out = [f"soltos: {', '.join(loose)}"] if loose else []
    return out + [_tree(i for i in ids if i.split(".", 1)[0] == t) for t in nested]


def c4_problems(step: Step, ids: set[str]) -> list[str]:
    """A create needs at least one `c4:<box>` and every `c4:` it carries names a box of the model
    or `transversal`; a session bead is free (df-8x0m.23.36)."""
    if step.issue_type.lower() == "session":
        return []
    if step.batch:
        return [f"`bd create --file`/`--graph` cria em lote e o lote não dá para conferir (cada bead precisa "
                f"de `{C4}:<caixa>`). Crie um por vez: `bd create \"Título\" -l {C4}:<caixa>,...`."]
    values = sorted(set(label_values(step.labels, C4)))
    wrong = [v for v in values if v not in ids and v != C4_ANYWHERE]
    if values and not wrong:
        return []
    what = (f"{', '.join(f'`{C4}:{v}`' for v in wrong)} não é caixa do modelo" if wrong
            else f"falta `{C4}:` com a caixa do modelo C4 que o bead toca")
    return [f"{what}. Valores: `{C4}:{C4_ANYWHERE}` (modo de trabalho, várias partes) ou o id completo de uma "
            f"caixa de arquitetura/modelo.json do origin/main, de qualquer nível (`a.{{b.{{c}}}}` dá `{C4}:a`, "
            f"`{C4}:a.b` e `{C4}:a.b.c`); o trabalho sobre o que roda hoje aponta uma caixa ...Hoje, o que "
            f"constrói o alvo uma ...Alvo (docs/bead-check.md):\n  "
            + "\n  ".join(box_listing(ids))
            + "\n  Uma caixa criada no seu branch só vale depois do merge: até lá use a mãe dela ou "
            f"`{C4}:{C4_ANYWHERE}`."]


# --------------------------------------------------------------------------- bd and git


class Bd:
    """The few bd reads the checks need. Each returns None when bd cannot answer."""

    def __init__(self, cwd: Path):
        self.cwd = cwd

    def _json(self, sub: str, bead: str):
        if not BEAD_ID.match(bead):
            return None  # not an id bd could know; never passed on, so it cannot read as a flag
        try:
            r = subprocess.run(["bd", sub, "--json", "--readonly", "--", bead], cwd=self.cwd, capture_output=True,
                               text=True, timeout=BD_TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        try:
            return json.loads(r.stdout)
        except ValueError:
            return None

    def show(self, bead: str) -> dict | None:
        data = self._json("show", bead)
        return data[0] if isinstance(data, list) and data else None

    def exists(self, bead: str) -> bool:
        return self.show(bead) is not None

    def lint_missing(self, bead: str) -> list[str] | None:
        data = self._json("lint", bead)
        if not isinstance(data, dict):
            return None
        return [m for r in data.get("results") or () for m in r.get("missing") or ()]


def _git(cwd: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                           timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def cites(bead: str) -> re.Pattern[str]:
    """Bead ids with the prefix of `bead`, matched whole (df-886.1 is not df-886.14)."""
    prefix = re.escape(bead.split("-", 1)[0])
    return re.compile(rf"(?<![\w.-]){prefix}-[a-z0-9]+(?:\.\d+)*(?![\w-]|\.\d)")


def _bead_worktrees(repo: Path, bead: str) -> list[Path]:
    """Worktrees named after the bead or on a branch that carries its id."""
    out, path = [], None
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
        elif path is not None and (path.name == bead or (line.startswith("branch ")
                                                          and bead in cites(bead).findall(line))):
            out.append(path)
            path = None
    return out


def changed_files(repo: Path, bead: str) -> set[str]:
    """Files the bug's work touched: commits citing its id, plus its worktree's diff."""
    files = set()
    log = _git(repo, "log", "--all", "-F", f"--grep={bead}", "--format=%x1e%s%n%b%x1f", "--name-only")
    for record in log.split("\x1e"):
        message, _, names = record.partition("\x1f")
        if bead in cites(bead).findall(message):
            files.update(f for f in names.splitlines() if f)
    for wt in _bead_worktrees(repo, bead):
        base = _git(wt, "merge-base", "origin/main", "HEAD").strip()
        if base:
            files.update(f for f in _git(wt, "diff", "--name-only", base).splitlines() if f)
    return files


def furo_candidates(repo: Path, bead: str) -> list[str]:
    """Beads cited by the last commits on origin/main of each file the bug changed, most cited
    first. The bug's own commits are left out."""
    cite = cites(bead)
    counts: Counter[str] = Counter()
    for path in sorted(changed_files(repo, bead))[:MAX_FILES]:
        log = _git(repo, "log", "origin/main", f"-n{COMMITS_PER_FILE}", "--format=%s%n%b%x1e", "--", path)
        for message in log.split("\x1e"):
            cited = set(cite.findall(message))
            if bead not in cited:
                counts.update(sorted(cited))
    return [b for b, _ in counts.most_common(MAX_CANDIDATES)]


def model_on_main(repo: Path) -> dict | None:
    """arquitetura/modelo.json as origin/main has it; None when missing, empty, not JSON or not a dict."""
    text = _git(repo, "show", MODEL_ON_MAIN)
    try:
        model = json.loads(text) if text else None
    except ValueError:
        return None
    return model if isinstance(model, dict) else None


def mark_problems(repo: Path, bead: str) -> list[str]:
    """The marks of the architecture target on origin/main that still name this bead."""
    model = model_on_main(repo)
    where = marked_beads(model).get(bead) if model is not None else None
    if not where:
        return []
    return [f"o alvo da arquitetura no origin/main ainda marca este bead em {'; '.join(where)}. Num PR, "
            "tire a marca de arquitetura/alvo.c4 (ou passe-a para o bead que continua a mudança) e rode "
            "`python3 scripts/arquitetura/modelo.py gerar`; depois do merge, `git fetch` e feche "
            "(df-8x0m.23.37)."]


# --------------------------------------------------------------------------- running


def check_line(command: str, bd: Bd, repo: Path) -> list[str]:
    """Every gap in the line, each prefixed with the command and bead it belongs to."""
    return check_steps(read_line(command), bd, repo)


def check_steps(steps: list[Step], bd: Bd, repo: Path) -> list[str]:
    """Every gap of these steps, each prefixed with the command and bead it belongs to."""
    out = []
    boxes = functools.cache(lambda: box_ids(model_on_main(repo)))  # read at the first create, once
    for step in steps:
        if step.kind == "refuse":
            out.append(step.message)
            continue
        if step.kind == "create":
            ids = boxes()
            if ids is not None:  # no model on origin/main: the rule does not run
                what = f"bd create {step.title}".rstrip()
                out += [f"[{what}] {f}" for f in c4_problems(step, ids)]
            continue
        issue = bd.show(step.bead)
        if issue is None:
            continue  # unknown bead or bd unavailable: bd answers that itself
        if step.kind == "claim":
            found = claim_problems(issue, bd.lint_missing(step.bead), step.acceptance, step.labels)
            what = f"bd update {step.bead} --claim"
        else:
            found = close_problems(issue, step.reason, step.labels, bd.exists,
                                   lambda b: furo_candidates(repo, b))
            if WAIVER not in {*(issue.get("labels") or ()), *step.labels}:
                found += mark_problems(repo, step.bead)
            what = f"bd close {step.bead}"
        out += [f"[{what}] {f}" for f in found]
    return out


def render(problems: list[str]) -> str:
    """The refusal. The same-line hint only when a claim or close has gaps: labels set earlier in the
    line count for those, not for a create (its labels go in the create itself)."""
    same_line = any(p.startswith(SAME_LINE_STEPS) for p in problems)
    hint = " (etiquetas na mesma linha, antes do comando, também valem)" if same_line else ""
    return (f"Conferência do bead (docs/bead-check.md) barrou o comando. Conserte e rode de novo{hint}:\n- "
            + "\n- ".join(problems))


def hook(event: dict, project: Path) -> dict | None:
    """The PreToolUse answer for one event: a deny with the gaps, or None to let it run."""
    if event.get("tool_name") != "Bash":
        return None
    command = str((event.get("tool_input") or {}).get("command") or "")
    if not BD_WORD.search(command):
        return None
    cwd = Path(event.get("cwd") or project)
    problems = check_line(command, Bd(cwd if cwd.is_dir() else project), project)
    if not problems:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": render(problems)}}


def main(argv: list[str]) -> int:
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    if argv[:1] == ["hook"]:
        try:
            out = hook(json.loads(sys.stdin.read() or "{}"), project)
        except Exception:  # noqa: BLE001 -- fail open: a bug here must never stall a session
            return 0
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        return 0
    if not argv:
        sys.stderr.write(__doc__ or "")
        return 2
    problems = check_steps(read_commands([["bd", *argv]]), Bd(Path.cwd()), project)
    if problems:
        sys.stdout.write(render(problems) + "\n")
        return 1
    sys.stdout.write("ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
