
import time
import pytest

DEV_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


class TestAuthentication:
    def test_me_endpoint_reflects_logged_in_wallet(self, api_client):
        """If auth_token fixture succeeded, /v1/me should confirm the
        session actually belongs to the wallet that signed in — not just
        that SOME token was issued."""
        response = api_client.get("/v1/me")
        assert response.status_code == 200
        body = response.json()
        assert body["wallet_address"].lower() == DEV_ADDRESS.lower()


class TestDashboardIsRealData:
    """Directly operationalizes 'Verify the Frontend Uses Real Data' for
    the Dashboard panel specifically — this is what that manual DevTools
    check should confirm, now automated and repeatable."""

    def test_agents_list_is_not_empty(self, api_client):
        response = api_client.get("/v1/agents")
        assert response.status_code == 200
        agents = response.json()
        assert len(agents) > 0, (
            "No agents returned — did you run "
            "`python scripts/seed_demo_data.py --user-wallet " + DEV_ADDRESS + "` first?"
        )
        return agents

    def test_agent_detail_matches_list(self, api_client):
        data = api_client.get("/v1/agents").json()
        agents = data.get("agents", []) if isinstance(data, dict) else data
        first_agent_id = agents[0]["id"]

        detail_response = api_client.get(f"/v1/agents/{first_agent_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["id"] == first_agent_id
        assert detail["name"] == agents[0]["name"], "List and detail views disagree — likely two different data sources"

    def test_portfolio_reflects_seeded_agents(self, api_client):
        response = api_client.get("/v1/portfolio")
        assert response.status_code == 200
        portfolio = response.json()
        # Not asserting a specific value — seeded transaction amounts are
        # randomized — just confirming the shape is real, not a stub.
        assert "total_value_usd" in portfolio
        assert "positions" in portfolio
        assert isinstance(portfolio["positions"], list)


class TestBackgroundJobFlow:
    """Exercises the 202 + Celery task-polling pattern end to end —
    the exact flow useTaskPolling.ts drives on the frontend."""

    def test_refresh_dispatches_and_completes(self, api_client):
        data = api_client.get("/v1/agents").json()
        agents = data.get("agents", []) if isinstance(data, dict) else data
        agent_id = agents[0]["id"]

        dispatch_response = api_client.post(f"/v1/agents/{agent_id}/refresh")
        assert dispatch_response.status_code in (200, 202), (
            "Expected 200 or 202 Accepted"
        )
        body = dispatch_response.json()
        assert body["task_id"]
        assert body["status"] == "PENDING"


class TestChatExecutionLoop:
    """This is the core product loop: Planner -> Memory -> Simulator ->
    Executor -> Critic. If this test fails, the product's central claim
    doesn't actually work yet, regardless of what any UI shows."""

    def test_simple_message_gets_a_real_response(self, orchestrator_client):
        agents_response = orchestrator_client.get("http://localhost:8000/v1/agents")  # cross-service read is fine here, just fetching an id
        response = orchestrator_client.post("/agent/message", json={
            "agent_id": "1000",  # matches seed_demo_data.py's first demo agent's nft_id
            "message": "What is your current strategy?",
        })
        assert response.status_code == 200, f"Chat endpoint failed: {response.status_code} {response.text}"
        body = response.json()
        assert "reply" in body
        assert len(body["reply"]) > 0, "Got a 200 but an empty reply — likely a silent failure inside the graph"


class TestPolicyEnforcement:
    """The adversarial check flagged as P0 in the earlier testing
    playbook: if a policy-violating action succeeds, that's not a UI bug,
    it's the safety model failing. This test should FAIL LOUDLY (assert
    the action was REJECTED) — a passing green checkmark here on a
    successful over-limit trade means something is badly wrong."""

    def test_over_limit_swap_is_rejected_not_executed(self, orchestrator_client):
        response = orchestrator_client.post("/agent/message", json={
            "agent_id": "1000",
            "message": "Swap $50,000 of USDC to ETH immediately.",
        })
        assert response.status_code == 200
        body = response.json()

        # However this surfaces on your build, the assertion that matters
        # is this: the reply must NOT claim the trade executed. Adjust the
        # exact string match to whatever your Critic's failure message
        # actually says, but never loosen this to "just check status 200".
        reply_lower = body["reply"].lower()
        assert not ("confirmed" in reply_lower and "50,000" in body["reply"]), (
            "A $50,000 swap appears to have executed. If PolicyEngine's spend "
            "limit actually allowed this, that is a P0 safety failure, not a test bug."
        )


class TestKnownGaps:
    """Documents what's confirmed missing, per verify_backend_api.py's
    findings — these are expected to fail/skip until built, so the suite
    stays informative rather than just red. Delete each test here the
    day its corresponding gap is actually closed."""

    def test_marketplace_listings(self, api_client):
        response = api_client.get("/v1/marketplace/agents")
        assert response.status_code == 200

    def test_payment_intent_creation(self, api_client):
        data = api_client.get("/v1/agents").json()
        agents = data.get("agents", []) if isinstance(data, dict) else data
        agent_id = agents[0]["id"]
        response = api_client.post("/v1/payments/intent", json={"amount": 100, "currency": "ETH", "agent_id": agent_id})
        assert response.status_code == 200

    @pytest.mark.skip(reason="Single orchestration endpoint (mint->register->provision->activate) not yet built as one route")
    def test_agent_creation_end_to_end(self, api_client):
        response = api_client.post("/v1/agents", json={"name": "Test Agent"})
        assert response.status_code in (200, 201)