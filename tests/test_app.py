"""The app, built offline. No network, no key.

`build(offline=True)` is fully constructible without credentials, which is what
makes these possible — and is itself the property worth guarding.
"""

import pytest
from starlette.testclient import TestClient

import config
from web.app import build


@pytest.fixture(scope="module")
def client():
    return TestClient(build(offline=True))


@pytest.fixture(scope="module")
def public_client():
    return TestClient(build(offline=True, public=True))


def test_the_wall_renders(client):
    page = client.get("/")
    assert page.status_code == 200
    for lane_id in ("lane-approved", "lane-bounced", "lane-pen"):
        assert lane_id in page.text
    # hdrs must reach the head, or there is no stylesheet and no ws extension
    assert "--bg:#0f1115" in page.text
    assert 'hx-ext="ws"' in page.text


def test_offline_hides_the_two_features_that_need_the_model(client):
    page = client.get("/")
    assert "Replaying recorded verdicts" in page.text
    assert 'id="tester"' not in page.text
    assert 'id="rubric-drawer"' not in page.text


def test_offline_refuses_a_rubric_edit_rather_than_returning_stale_numbers(client):
    response = client.post(
        "/rubric",
        data={"axis": "contempt", "question": "q", "level0": "a", "level1": "b"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Restart without --offline" in response.text


def test_offline_refuses_a_typed_comment(client):
    response = client.post(
        "/test", data={"comment": "you are a moron", "context": ""},
        headers={"HX-Request": "true"},
    )
    assert "needs the model" in response.text


def test_health_reports_the_exposure_controls(client):
    body = client.get("/health").json()
    assert body["offline"] is True
    assert body["public"] is False
    assert body["drip_ceiling"] == config.DRIP_RATE_MAX
    # Spec §9 named /test; /rubric is the route that actually costs money.
    assert body["rejudge_budget_left"] == config.REJUDGE_TOTAL_CAP


def test_public_mode_clamps_the_drip_ceiling(public_client):
    """The largest exposure is not a route that spends money directly, it is the
    slider that sets the burn rate."""
    assert public_client.get("/health").json()["drip_ceiling"] == (
        config.DRIP_RATE_MAX_PUBLIC
    )
    assert config.DRIP_RATE_MAX_PUBLIC < config.DRIP_RATE_MAX


def test_tune_clamps_rather_than_trusting_the_client(public_client):
    public_client.post(
        "/tune",
        data={"rate": "9999", "floor": "0.85", "severity": "6.0", "gate": "decision"},
        headers={"HX-Request": "true"},
    )
    assert public_client.get("/health").json()["drip_ceiling"] == (
        config.DRIP_RATE_MAX_PUBLIC
    )


def test_retuning_works_offline(client):
    """The point of faking at the client boundary: the policy controls are the
    real ones, because a threshold change is a pure recompute."""
    response = client.post(
        "/tune",
        data={"rate": "60", "floor": "0.55", "severity": "6.0", "gate": "decision"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert client.get("/health").json()["api_errors"] == 0


def test_the_calibration_tab_renders_without_a_key(client):
    page = client.get("/calibration")
    assert page.status_code == 200
    assert "auto-handled" in page.text
    assert "agreement on those" in page.text
    # the caveat about which axes the label can score is on the screen
    assert "insult and abuse" in page.text


def test_the_curve_shows_what_agreement_is_measured_against(client):
    """The rising agreement line is mostly the subset shedding its toxic
    comments (RESEARCH.md §5). Without the baseline beside it the chart claims
    more than the data supports, so guard all three of the places that say so.
    """
    page = client.get("/calibration")
    assert "approve everything" in page.text          # legend and line label
    assert "stroke-dasharray" in page.text            # the reference line itself
    assert "Read the gap, not the orange line" in page.text
    # and the shipped floor is not the best point on the chart — say why
    assert "lift peaks" in page.text


def test_the_table_carries_the_base_rate_immune_column(client):
    page = client.get("/calibration")
    for header in ("approve-all", "lift", "balanced acc"):
        assert header in page.text


def test_an_unknown_gate_falls_back_rather_than_erroring(client):
    assert client.get("/calibration?gate=nonsense").status_code == 200
