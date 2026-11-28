---
topic: Bead check (a create names its C4 box; a child is created without the parent's labels; claim needs an acceptance criterion; close needs the outcome labels and the note, no architecture mark left on the bead, and the prod check of a box prod serves)
read_when: "A \"Conferência do bead\" refusal on bd create/new/q, bd update --claim or bd close; which c4: box a new bead gets; changing which labels a close needs; the cutoff date; the escape hatch; a close refused because arquitetura/ still marks the bead; a close refused for a missing `Servido pela prod:`; the docs a claim hands the session"
related_issues: [df-m60c, df-b89w, df-081f, df-ugnz, df-8x0m.23.37, df-8x0m.23.36, df-8x0m.23.42, df-8x0m.23.40]
---

# Bead check

`scripts/beads/check.py` (tests: `tests/test_beads_check.py`) checks bd's create, claim and close before
they run, from a PreToolUse hook on Bash. Every other command passes in ~30 ms, untouched. Design:
`~/claudemain/docs/superpowers/specs/2026-11-16-fechamento-do-bead-design.md`.

| Command | Needs | Applies to |
| --- | --- | --- |
| `bd create` (or `bd new`, `bd q`) | at least one `c4:<box>`, and every `c4:` a box of `arquitetura/modelo.json` on `origin/main` or `transversal`; one bead at a time (`--file`/`--graph` refused) | every create but of a session bead; nothing when `origin/main` has no readable model |
| `bd create --parent <id>` (or `bd new`) | `--no-inherit-labels` | every create with a parent |
| `bd update <id> --claim` | an acceptance criterion (the criteria `bd lint <id>` asks of the type; `--acceptance` in the same command counts) | beads created on or after the cutoff; not session beads |
| | the estimate: `vol:` and `inc:`, each 1 to 3 (`--add-label` in the same command counts) | beads created on or after the cutoff; not session beads or epics |
| `bd close <id> --reason "..."` | `vol-real:`/`inc-real:` (when it has `vol:`/`inc:`), `origem:` | every bead |
| | `camada:` (the check that should have caught it, from the base list) and `erro:` (the kind of error, free) | bugs |
| | `achado-por:`, `furo-de:` (bead id or `nenhum`) | bugs created on or after the cutoff |
| | `etapa:` | a bead whose `furo-de:` names a bead |
| | the note: `Conferi:`, `Não conferi:`, `Assumi:` in the reason, each with text | beads created on or after the cutoff |
| claim and close | at most one value of each of `vol:`, `inc:`, `origem:`, `vol-real:`, `inc-real:` | every bead; not session beads at claim |
| `bd close <id>` | no `#novo`/`#muda`/`#sai` mark of `arquitetura/modelo.json` on `origin/main` still names the bead | every bead, `descartado:`/`duplicado:` closes too |
| `bd close <id> --reason "..."` | `Servido pela prod:` with text in the reason (the command used and its status) | a bead whose `c4:` box is tagged `servidoPelaProd` on `origin/main`, sits under a tagged element, or (below the top level) holds one; beads created on or after the cutoff; not decisions, nor beads whose changes are all under `arquitetura/` |

**Every bead points at a C4 box (df-8x0m.23.36):** the architecture model
(`arquitetura/*.c4`, exported to `arquitetura/modelo.json`; `arquitetura/README.md`) is the map of the
system, and each bead says where on it the work lands, so the drawing can show the open beads and the
bugs (`camada:`, `erro:`) of each box. `bd create` without a `c4:` label, or with a `c4:` that is not
an element id, is refused with the valid ids, grouped by first segment (`casaHoje.{ninhoHoje.{listaHoje}}`:
each level is an id of its own, `casaHoje`, `casaHoje.ninhoHoje`, `casaHoje.ninhoHoje.listaHoje`).
Which box: work on what runs today (a fix, a test, a refactor of existing code) points at a box of
`hoje.c4` (ids ending in `Hoje`); work that builds the target points at a box of `alvo.c4` (ids
ending in `Alvo`). Take the smallest box that holds the whole change; a bead that touches two boxes
carries two `c4:` labels; how we work (hooks, rules, docs of the process) and work spread over the
whole system is `c4:transversal`. Labels go in the create itself (`-l`, `--labels`, repeated or with
commas); a session bead is free. A batch create (`--file`, `--graph`) is refused: its beads' labels
are in a file the hook does not read, so create them one at a time.

The ids come from `origin/main:arquitetura/modelo.json` (`model_on_main`), not from the checkout: the
hook runs the main checkout's `check.py`, whose tree only moves on Lucas's pull, while `origin/main`
moves with any `git fetch`. So a box created on your own branch only counts after it merges: until
then, use its parent box or `c4:transversal`. With no `modelo.json` on `origin/main`, or one that is
not a JSON object with element ids, the rule does not run (fail open). The main checkout's
`check.py` itself only gets this rule at the pull after the merge.

**One value per label (df-b89w):** `bd create --parent` copies the parent's labels to the child, so a
child given its own estimate ends with two (`vol:1` and `vol:2`), and the claudemain measurement
drops a bead with two estimates. The refusal shows the `bd label remove` that fixes one (run it in
a command of its own, before the claim).

**A child carries only its own labels (df-081f):** a child created without labels of its own keeps
the parent's `vol:`/`inc:`/`origem:` and, by its labels, cannot be told from one that was estimated.
So `bd create --parent` without `--no-inherit-labels` is refused, whatever labels it passes.

**The claim asks for the estimate (df-ugnz):** a bead without `vol:`/`inc:` is not asked for
`vol-real:`/`inc-real:` at close and never reaches the claudemain 3x3 table, silently. The create is
not checked for it: the claim is the last moment before the work.

The allowed values live in the script (`ORIGENS`, `CAMADAS`, `ACHADO_POR`, `ETAPAS`) and are
printed in each refusal; `erro:` takes any value. Since df-ygqm `CAMADAS` is the claudemain base
list (`teste`, `propriedade`, `gui`, `estatica`, `tipos`, `mutacao`, `fim-a-fim`, `sonar`, `nenhuma`);
the old values `dados`, `crash`, `windows`, `instavel`, `dependencias` are kinds of error now,
and closed bugs were moved to `erro:` once (camada: became the check that should have caught them).
Each refusal also shows the `bd label add ...` that fixes it. When `furo-de:` is missing the
refusal lists candidates: the beads cited by the last commits on `origin/main` of the files the bug
changed (its commits, found by its id, plus its worktree's diff).

**Pass without checks:** epics and session beads at close; a reason starting with `descartado:` or
`duplicado:`; a bead labelled `fechamento:dispensado`, the escape hatch, used only when Lucas asks
(the claudemain measurement counts these). Only the escape hatch skips the mark check below.

**Architecture marks (df-8x0m.23.37):** every element or arrow of `arquitetura/alvo.c4` that differs
from today carries `#novo`, `#muda` or `#sai` and the open bead of that change
(`arquitetura/README.md`, "Marcas do alvo"). At `bd close` the hook reads
`origin/main:arquitetura/modelo.json` (`model_on_main`) and refuses the close while a mark still
names the bead, listing what it marks. Fix: in a PR, remove the mark from `alvo.c4` or move it to
the bead that carries the change on, run `python3 scripts/arquitetura/modelo.py gerar`, merge, then
`git fetch` and close. A dropped bead (`descartado:`) is checked too: its mark would point at a
closed bead. With no `modelo.json` on `origin/main`, or one that is not a JSON object, nothing is
checked (fail open). CI has no bd, so this is the half of the rule that knows open from closed; the
CI job `arquitetura` checks only the marks' format (`scripts/arquitetura/marcas.py`), and
`marcas.py --bd` checks every mark against bd at once.

**Served by prod (df-8x0m.23.42):** what a screen or the API reads is done only after the prod
server on Windows served it (CLAUDE.md, df-p0pu): dev, CI and reviewers run on Linux, and prod's
Windows Python lacks things Linux has. The smallest elements that a screen or the API reads and that
run on Windows carry the tag `servidoPelaProd` in `arquitetura/*.c4` (`arquitetura/README.md`): the
components that serve or read (the DataForge API, the ninho routes, the Finanças backend routes), the
front components that call a server, and the data stores a screen shows. At `bd close` the hook reads
`origin/main:arquitetura/modelo.json` and asks for `Servido pela prod:` in the `--reason` when a
`c4:` label of the bead is covered: the box is tagged, sits under a tagged element
(`c4:casaHoje.ninhoHoje.listaHoje.x` under `listaHoje`), or holds one (`c4:casaHoje.ninhoHoje`, the
container of the tagged `listaHoje`). A top box (`c4:casaHoje`, `c4:casaAlvo`) does not reach down,
or every bead on the whole system would be asked.

What counts: any `Servido pela prod:` (any case, any spacing) followed by text, up to the next mark
of the note or of this rule. Write the command used and the status, e.g. `Servido pela prod:
Invoke-WebRequest .../tables/<table> 200`, or the screen opened in the app; the hook only sees that
there is text, so the honesty of the text is yours. A mark right after `não` (`Não servido pela prod:
falta o deploy`) is not one. The refusal gives how to check: the `Invoke-WebRequest` command of
CLAUDE.md on the DataForge port 8000 and the app shortcut, and for the ninho (port 8100, behind the
login) opening the screen on the phone or the PC, or the route with a session (`docs/commands.md`).

A mark in the reason, not a label, because it carries the proof. Passes: the exemptions above
(epics, sessions, `descartado:`/`duplicado:`, the escape hatch), decisions (they serve nothing),
beads created before the cutoff, beads with no covered box (`c4:transversal` included), and a bead
whose changed files (its commits, found by its id, plus its worktree's diff, as for `furo-de:`) are
all under `arquitetura/`: a change to the drawing only. No changed files found is not that: a bead
without commits of its own is still asked. With no readable model, or no element tagged, nothing is
checked (fail open). One `git show` per command line serves this check, the marks and `bd create`.

**Cutoff:** `CUTOFF` in the script, 2026-11-16. Older beads are held only to the rules that predate
it (real difficulty, origem, camada, erro), and their claims are not checked.

**Same line counts:** `bd label add <id> achado-por:teste && bd close <id> --reason "..."` passes;
so do `bd update --add-label/--set-labels/--acceptance` earlier in the line.

**Refused shortcuts:** `bd close` without an id (closes the last touched bead), `bd close
--claim-next`, `bd update --status closed`, an id from a variable (`bd close "$ID"`), and a close or
claim the script cannot read (unbalanced quotes).

## By hand

```bash
python3 scripts/beads/check.py close df-x --reason "Conferi: ... Não conferi: ... Assumi: ..."
python3 scripts/beads/check.py update df-x --claim
python3 scripts/beads/check.py create "Título" -l vol:1,inc:1,c4:casaHoje.ninhoHoje
```

Same arguments as bd. Exit 1 lists the gaps, 0 prints `ok`. Read-only (`bd --readonly`).

## Hook

In `.claude/settings.json` (private), next to the session guard (both run on each Bash call; the
guard warns, this one denies):

```json
{"matcher": "Bash", "hooks": [{"type": "command", "timeout": 60,
  "command": "f=\"$CLAUDE_PROJECT_DIR/scripts/beads/check.py\"; [ -f \"$f\" ] && python3 -S \"$f\" hook; exit 0"}]}
```

The `[ -f ]` matters: python exits 2 on a missing file, and exit 2 from a PreToolUse hook blocks
the call, so a checkout without the script would refuse every Bash command.

It covers subagents too: their Bash calls go through the project's PreToolUse hooks (verified live
on 2026-11-16, df-v06a: a subagent's `bd close` of a bug without the labels was refused).

Fails open: an internal error, no bd, or an unknown bead lets the command through. Cost: ~30 ms
for a command without `bd`, ~1 s for a checked close (one `bd show`, one more per `furo-de:` id).

## Contexto no claim (df-8x0m.23.40)

`check.py contexto` is a PostToolUse hook on Bash, so it only sees a command that ran. After a
`bd update <id> --claim` it hands the session, as `additionalContext`, one line per box: for each
`c4:` box of the bead, the box itself, its parent and the far end of every relation whose source or
target is the box (the relations of its descendants are left out: a container's would list half
the model), without repeats, at most 12 lines plus `... e mais N`. The order is fixed: the bead's own boxes, then their parents, then the far ends sorted by id, so the cut drops the far ends whose ids sort last. Each line: id, title, the doc
path (`docs_por_elemento` of `scripts/arquitetura/docs.py`) and the `topic` / `read_when` of its
front matter. The model and the doc text come from origin/main (`git show`), like the create check.
A `c4:transversal` bead, a bead without a `c4:` box, an interrupted command, an unknown bead, a
missing model or a missing `docs.py` give nothing. It never blocks: any error exits 0 silently. A line whose claim failed but that still exits 0 (`bd update x --claim; true`) gets the context too: the hook sees only the line's exit.

In `.claude/settings.json`, under `hooks.PostToolUse`:

```json
{"matcher": "Bash", "hooks": [{"type": "command", "timeout": 30, "command": "f=\"$CLAUDE_PROJECT_DIR/scripts/beads/check.py\"; [ -f \"$f\" ] && python3 -S \"$f\" contexto; exit 0"}]}
```

An older `check.py` without the mode reads `contexto` as a bd subcommand and prints `ok`: harmless.

## Participants hook (claudemain, df-9cq5)

Wired in the same `.claude/settings.json`, from claudemain's published copy of its hooks
(`~/claudemain/publicado/atual/catalogo/participar.py`, not this repo and not claudemain's working
tree, so a half-done edit there never reaches a live session; df-c1dt): it records who took part in each bead in `~/claudemain/data/participantes.jsonl`
(ids only) for the measurement.

- PostToolUse and PostToolUseFailure, matcher `Bash`: a `bd update <id> --claim` or `bd close <id>`
  that ran. PostToolUse only fires when the command exits 0; a claim inside a command that ends
  with an error reaches the hook through PostToolUseFailure. A claim or close this check refused
  never runs, so it is never recorded.
- PreToolUse, matcher `Agent|Task`, with `--exigir`: each subagent launch, filed under the bead
  cited in its description or prompt, else the one this conversation has claimed. A launch with
  neither is **refused**, except read-only types (Explore, Plan, claude-code-guide,
  statusline-setup). Cite the bead id in the description, or claim one first.

Same `[ -f ] ... ; exit 0` wrapper as above, so a machine without claudemain is not blocked.
Problems with the hook itself go to claudemain (a `cm-` bead in `~/claudemain`).

## Known limits

- A quoted argument made only of shell operators (`';'`) reads as an operator (shlex cannot tell);
  it only makes the check stricter.
- `cd` before `bd` is ignored: bd is asked from the hook's working directory.
- Not checked yet (spec's second version): running the acceptance command, proof of fix in CI,
  branch and commit citing the bead.
