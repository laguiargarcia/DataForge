"""gate: judge the last mutate result of a module against its `min_score` in testloop.toml.

Exit 0 when score >= min_score, 1 when below, 2 when there is nothing trustworthy to judge
(no min_score, no mutate result, a partial one, or one measured on older code).

A target with `shards = N` (N > 1) is judged on its N shard results summed (df-n8ac.32): each must
be there, complete and measured on the current code, or there is nothing to judge.
"""

import json
import math
import sys

from common import ToolError, mutation_score, result_path, shard_count, source_sha, write_result


def four_places_down(score: float) -> str:
    """A score as 4 decimals, rounded DOWN: 29/31 = 0.93548 is 0.9354. Rounding to nearest
    printed 0.9355, so the min_score it suggested failed the next gate (df-0kv9)."""
    return f"{math.floor(score * 10_000) / 10_000:.4f}"


def _load(config, module: str, shard: tuple[int, int] | None) -> dict:
    """One mutate result, refused when missing, of another shard, stale or partial."""
    name = f"{module} shard {shard[0]}/{shard[1]}" if shard else module
    command = f"cli.py mutate {module}" + (f" --shard {shard[0]}/{shard[1]}" if shard else "")
    path = result_path(config.repo, "mutate", module, shard)
    if not path.is_file():
        raise ToolError(f"no mutate result for {name}; run `{command}` first")
    measured = json.loads(path.read_text(encoding="utf-8"))
    if shard and measured.get("shard") != list(shard):
        raise ToolError(f"mutate result for {name} says it is shard {measured.get('shard')}; run `{command}` again")
    if measured.get("source_sha") != source_sha(config.repo / module):
        raise ToolError(f"mutate result for {name} is stale: the module changed since; run `{command}` again")
    if not measured.get("complete"):
        raise ToolError(f"mutate result for {name} is partial; run `{command}` again")
    return measured


def _summed(config, module: str, shards: int) -> dict:
    """The N shard results as one: counts summed, score by the formula mutate uses."""
    parts = [_load(config, module, (i, shards)) for i in range(1, shards + 1)]
    killed = sum(p["killed"] for p in parts)
    survived = [s for p in parts for s in p["survived"]]
    not_tested = [k for p in parts for k in p["not_tested"]]
    score = mutation_score(killed, len(survived), len(not_tested))
    if score is None:
        raise ToolError(f"the {shards} shards of {module} hold no mutant with a verdict; nothing to judge")
    return {
        "tests": parts[0]["tests"], "score": score, "killed": killed,
        "total": sum(p["total"] for p in parts), "survived": survived,
    }


def main(config, module: str):
    target = config.targets.get(module) or {}
    min_score = target.get("min_score")
    if min_score is None:
        raise ToolError(f"{module} has no `min_score` in its testloop.toml target; add one")

    shards = shard_count(config, module)
    if shards == 1:
        measured = _load(config, module, None)
        if measured.get("score") is None:
            raise ToolError(f"mutate result for {module} is partial; run mutate again")
    else:
        measured = _summed(config, module, shards)

    score = measured["score"]
    passed = score >= min_score
    survived = [s["id"] for s in measured["survived"]]
    out = write_result(config.repo, "gate", module, {
        "tests": measured["tests"],
        "score": score,
        "min_score": min_score,
        "passed": passed,
        "killed": measured["killed"],
        "total": measured["total"],
        "survived": survived,
    })

    verdict = "passed" if passed else "FAILED"
    print(f"testloop: gate {module}: score {four_places_down(score)}, min_score {min_score:.4f}: {verdict}",
          file=sys.stderr)
    if not passed:
        print("surviving mutants (show one with `mutmut show <id>`):", file=sys.stderr)
        for mutant in survived:
            print(f"  {mutant}", file=sys.stderr)
    elif float(suggested := four_places_down(score)) > min_score:
        # Only a real raise: a score just above min_score can round down to min_score itself (df-ahua.25).
        print(f"score is above min_score; raise min_score to {suggested} to keep it", file=sys.stderr)
    return out, 0 if passed else 1
