from carapace.ci_metrics import (
    PipelineContext,
    collect_context,
    format_metrics,
    should_skip_push,
    status_to_value,
)


def test_format_metrics_includes_status_and_duration():
    ctx = PipelineContext(
        repo="openclaw/nisto-home",
        owner="openclaw",
        name="nisto-home",
        branch="dev",
        event="push",
        status="success",
        pipeline="42",
        started=1_700_000_000,
        finished=1_700_000_123,
        duration_seconds=123,
    )

    text = format_metrics(ctx)

    assert "woodpecker_pipeline_status" in text
    assert 'woodpecker_pipeline_status{branch="dev",event="push",owner="openclaw",pipeline="42",repo="openclaw/nisto-home"} 1' in text
    assert 'woodpecker_pipeline_duration_seconds{branch="dev",event="push",owner="openclaw",pipeline="42",repo="openclaw/nisto-home"} 123' in text


def test_status_to_value_handles_variants():
    assert status_to_value("success") == 1
    assert status_to_value("passed") == 1
    assert status_to_value("failure") == 0
    assert status_to_value("error") == 0
    assert status_to_value("skipped") == -1
    assert status_to_value("canceled") == -1



def test_collect_context_prefers_ci_vars_and_computes_duration():
    env = {
        "CI_REPO": "openclaw/nisto-home",
        "CI_REPO_OWNER": "openclaw",
        "CI_REPO_NAME": "nisto-home",
        "CI_COMMIT_BRANCH": "feature",
        "CI_PIPELINE_STATUS": "success",
        "CI_PIPELINE_NUMBER": "99",
        "CI_PIPELINE_EVENT": "push",
        "CI_PIPELINE_STARTED": "100",
        "CI_PIPELINE_FINISHED": "250",
    }

    ctx = collect_context(env)

    assert ctx.repo == "openclaw/nisto-home"
    assert ctx.owner == "openclaw"
    assert ctx.branch == "feature"
    assert ctx.pipeline == "99"
    assert ctx.status == "success"
    assert ctx.duration_seconds == 150


def test_should_skip_respects_owner_allowlist_and_opt_out():
    env = {"CI_METRICS_OPTOUT": "true", "CI_REPO_OWNER": "fork"}

    assert should_skip_push(env, allow_owners={"openclaw"}) is True

    env_no_opt_out = {"CI_REPO_OWNER": "fork"}
    assert should_skip_push(env_no_opt_out, allow_owners={"openclaw"}) is True

    env_allowed = {"CI_REPO_OWNER": "openclaw"}
    assert should_skip_push(env_allowed, allow_owners={"openclaw"}) is False
