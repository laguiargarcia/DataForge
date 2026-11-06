"""gate: judge the last mutate result of a module against its `min_score` in testloop.toml.

Exit 0 when score >= min_score, 1 when below, 2 when there is nothing trustworthy to judge
(no min_score, no mutate result, a partial one, or one measured on older code).
"""

import json
import sys

from common import ToolError, result_path, source_sha, write_result


def main(config, module: str):
    target = config.targets.get(module) or {}
    min_score = target.get("min_score")
    if min_score is None:
        raise ToolError(f"{module} has no `min_score` in its testloop.toml target; add one")

    path = result_path(config.repo, "mutate", module)
    if not path.is_file():
        raise ToolError(f"no mutate result for {module}; run `cli.py mutate {module}` first")
    measured = json.loads(path.read_text(encoding="utf-8"))
    if measured.get("source_sha") != source_sha(config.repo / module):
        raise ToolError(f"mutate result for {module} is stale: the module changed since; run mutate again")
    if not measured.get("complete") or measured.get("score") is None:
        raise ToolError(f"mutate result for {module} is partial; run mutate again")

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
    print(f"testloop: gate {module}: score {score:.4f}, min_score {min_score:.4f}: {verdict}", file=sys.stderr)
    if not passed:
        print("surviving mutants (show one with `mutmut show <id>`):", file=sys.stderr)
        for mutant in survived:
            print(f"  {mutant}", file=sys.stderr)
    elif score > min_score:
        print(f"score is above min_score; raise min_score to {score:.4f} to keep it", file=sys.stderr)
    return out, 0 if passed else 1
