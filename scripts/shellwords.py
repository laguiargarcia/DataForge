"""Reading a Bash command line into simple commands, for the PreToolUse hooks that look at Bash
calls: the session guard (scripts/sessions/guard.py) and the bead check (scripts/beads/check.py).

Each hook runs as a loose script (`python3 -S <script>`) and puts this folder on sys.path to
import it. Standard library only.
"""

from __future__ import annotations

import re
import shlex

OPERATOR_CHARS = ";&|()<>\n"
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
PREFIX_WORDS = frozenset({"env", "command", "exec", "nohup", "time", "nice", "sudo", "do", "then",
                          "else", "{", "!"})


def split_commands(command: str) -> list[list[str]]:
    """Simple commands of a shell line, as word lists. Operators and newlines split them;
    redirections are dropped. Raises ValueError on a line shlex cannot read.

    Only a token made of operator characters is an operator: a quoted `"a -> b"` stays a word.
    shlex cannot tell a quoted argument made only of operators (`';'`) from the operator itself;
    such an argument splits the command, which only makes a hook look at more commands."""
    lex = shlex.shlex(command.replace("\\\n", " "), posix=True, punctuation_chars=OPERATOR_CHARS)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    out, cur, skip = [], [], False
    for tok in lex:
        operator = bool(tok) and set(tok) <= set(OPERATOR_CHARS)
        if skip:
            skip = False
        elif operator and ("<" in tok or ">" in tok):
            if cur and cur[-1].isdigit():
                cur.pop()  # the fd of `2>&1`, split off by shlex
            skip = True
        elif operator:
            out.append(cur)
            cur = []
        else:
            cur.append(tok)
    out.append(cur)
    return [c for c in out if c]


def strip_prefix(words: list[str]) -> list[str]:
    """Drop leading VAR=value assignments, wrappers like `env` (and its options) and shell keywords
    like `then`, so the first word left is the program."""
    i = 0
    while i < len(words) and (ASSIGNMENT.match(words[i]) or words[i] in PREFIX_WORDS
                              or (i and words[i - 1] == "env" and words[i].startswith("-"))):
        i += 1
    return words[i:]
