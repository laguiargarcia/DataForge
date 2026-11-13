"""Rules of dataforge/deploy/models.py's two plan methods: writable() keeps the items that would touch
disk (ADD, MODIFY, DELETE) in plan order, and summary() names source, target and pipeline and counts
each action present, by action name, or says there are no changes."""
from hypothesis import example, given, settings, strategies as st

from dataforge.deploy.models import Action, DeployPlan, ItemChange, Kind

_WRITES = {Action.add, Action.modify, Action.delete}


def _plan(actions: list[Action]) -> DeployPlan:
    items = [ItemChange(source_path=None if a is Action.delete else f"s{i}", target_path=f"t{i}",
                        kind=Kind.script, action=a) for i, a in enumerate(actions)]
    return DeployPlan(source="Dev", target="Prod", pipeline="to-prod", items=items)


@settings(max_examples=60)
@given(st.lists(st.sampled_from(list(Action)), max_size=10))
@example([])
@example(list(Action))
def test_writable_keeps_the_disk_touching_items_in_plan_order(actions):
    """writable() is the plan's ADD/MODIFY/DELETE items, in plan order; SKIP_IGNORED and PROTECTED
    items are left out."""
    plan = _plan(actions)
    assert plan.writable() == [i for i in plan.items if i.action in _WRITES]


@settings(max_examples=60)
@given(st.lists(st.sampled_from(list(Action)), max_size=10))
@example([])
@example([Action.modify, Action.add, Action.add, Action.protected, Action.delete, Action.skip_ignored])
def test_summary_counts_each_action_present_by_name(actions):
    """summary() is 'Dev → Prod [to-prod]: ' and then '<count> <ACTION>' for each action present,
    sorted by action name and joined by ', ', or 'no changes' for an empty plan."""
    present = sorted({a.value for a in actions})
    counts = ", ".join(f"{sum(a.value == name for a in actions)} {name}" for name in present)
    assert _plan(actions).summary() == "Dev → Prod [to-prod]: " + (counts or "no changes")
