from dataforge.deploy.models import (
    Kind, Action, PairRule, PipelineModel, ItemChange, DeployPlan,
)


def test_pairrule_defaults():
    r = PairRule(source="jobs/**", target="jobs/")
    assert r.promote_active is False  # law #1 default


def test_pipeline_defaults():
    p = PipelineModel(target="Prod")
    assert p.pairs == []
    assert p.prune is False


def test_deployplan_writable_excludes_skips():
    plan = DeployPlan(
        source="Dev", target="Prod", pipeline="to-prod",
        items=[
            ItemChange(source_path="a", target_path="a", kind=Kind.script, action=Action.add),
            ItemChange(source_path="b", target_path="b", kind=Kind.config, action=Action.skip_ignored),
            ItemChange(source_path="c", target_path="c", kind=Kind.trigger, action=Action.protected),
            ItemChange(source_path="d", target_path="d", kind=Kind.script, action=Action.modify),
        ],
    )
    assert [i.target_path for i in plan.writable()] == ["a", "d"]


def test_action_values_are_stable_strings():
    assert Action.add.value == "ADD"
    assert Action.skip_ignored.value == "SKIP_IGNORED"
    assert Action.protected.value == "PROTECTED"


def test_auto_deploy_status_values():
    from dataforge.deploy.models import AutoDeployStatus
    assert AutoDeployStatus.applied.value == "APPLIED"
    assert AutoDeployStatus.nothing_to_apply.value == "NOTHING_TO_APPLY"


def test_auto_deploy_result_defaults():
    from dataforge.deploy.models import AutoDeployResult, AutoDeployStatus
    r = AutoDeployResult(source="Dev", target="Prod", pipeline="to-prod",
                         status=AutoDeployStatus.nothing_to_apply)
    assert r.applied == 0
    assert r.protected == 0
    assert r.ignored == 0
    assert r.total == 0
    assert r.entry_id is None
    # str-enum serializes to its value (json/model_dump friendly for the --json surface)
    assert r.model_dump(mode="json")["status"] == "NOTHING_TO_APPLY"
