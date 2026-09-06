#!/usr/bin/env python3
# end_to_end testing/verify_backend_api.py
#
# Step 1: Backend API Verification. Hits every endpoint the frontend is
# expected to call and reports which ones are actually live, which 404,
# and which services aren't even running. Run this BEFORE checking the
# frontend — if an endpoint fails here, no amount of frontend debugging
# will fix it, the bug isn't in the UI.

import argparse
import sys
from dataclasses import dataclass

import httpx

@dataclass
class EndpointCheck:
    service: str
    method: str
    path: str
    requires_auth: bool = True
    expected_if_missing: str = "404 or connection refused — not built yet"
    body: dict | None = None


CHECKS = {
    "api-gateway (health)": [
        EndpointCheck("backend", "GET", "/health", requires_auth=False),
        EndpointCheck("backend", "GET", "/v1/rpc-status", requires_auth=False),
    ],
    "Authentication": [
        EndpointCheck("backend", "GET", "/v1/auth/nonce?address=0xaea2df838df0b8b6b9e8fd4e41e12e91114e15e0", requires_auth=False),
    ],
    "Dashboard": [
        EndpointCheck("backend", "GET", "/v1/portfolio"),
    ],
    "Agent Studio — listing": [
        EndpointCheck("backend", "GET", "/v1/agents"),
    ],
    "Agent Studio — creation": [
        EndpointCheck("backend", "POST", "/v1/studio/agents", body={"name": "Test Agent", "model_version": "v1"}),
    ],
    "Marketplace": [
        EndpointCheck("backend", "GET", "/v1/marketplace/agents"),
        EndpointCheck("backend", "GET", "/v1/marketplace/my-purchases"),
        EndpointCheck("backend", "POST", "/v1/marketplace/buy", body={"agent_id": "dummy-id-for-shape-check", "payment_method": "crypto"}),
    ],
    "Chat (hits agent-orchestrator)": [
        EndpointCheck("agent-orchestrator", "POST", "/agent/message", body={"agent_id": "default", "message": "hello"}),
    ],
    "Payment Flow": [
        EndpointCheck("backend", "POST", "/v1/payments/intent", body={"amount": 100, "currency": "ETH"}),
        EndpointCheck("backend", "POST", "/v1/payments/confirm", body={"payment_id": "dummy-id-for-shape-check", "tx_hash": "0x"}),
    ],
    "Smart Contracts": [
        EndpointCheck("backend", "GET", "/v1/smart-contracts/transactions/dummy-id"),
        EndpointCheck("backend", "POST", "/v1/smart-contracts/transfer?agent_id=dummy-id&to_address=0x00000000000000000000000000000000000000"),
    ],
}

SERVICE_PORTS = {
    "backend": 8000,
    "agent-orchestrator": 8001,
    "tool-router": 8002,
    "wallet-service": 8003,
    "ai-reasoning": 8004,
}


def check_one(client: httpx.Client, host: str, check: EndpointCheck, token: str | None) -> tuple[str, str]:
    port = SERVICE_PORTS.get(check.service, 8000)
    url = f"http://{host}:{port}{check.path}"
    headers = {"Authorization": f"Bearer {token}"} if (token and check.requires_auth) else {}

    try:
        kwargs: dict = {"headers": headers, "timeout": 5.0}
        if check.body:
            kwargs["json"] = check.body
        response = client.request(check.method, url, **kwargs)
    except httpx.ConnectError:
        return "SERVICE DOWN", f"{check.service} isn't running on port {port} at all"
    except httpx.TimeoutException:
        return "TIMEOUT", "no response within 5s"

    if response.status_code == 404:
        return "404 NOT BUILT", check.expected_if_missing
    if response.status_code == 401:
        return "AUTH REQUIRED", "endpoint exists — pass --token to check it properly"
    if response.status_code >= 500:
        return "SERVER ERROR", f"HTTP {response.status_code} — endpoint exists but is broken, check logs"
    if response.status_code < 400:
        return "LIVE", f"HTTP {response.status_code}"
    return f"HTTP {response.status_code}", ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--token", default=None, help="A valid JWT, if you want authenticated checks to pass rather than just confirm the route exists")
    args = parser.parse_args()

    print(f"Backend API Verification — target host: {args.host}\n")

    any_missing = False
    with httpx.Client() as client:
        for group_name, checks in CHECKS.items():
            sep = "-" * max(0, 60 - len(group_name))
            print(f"-- {group_name} {sep}")
            for check in checks:
                status, detail = check_one(client, args.host, check, args.token)
                marker = "[OK]" if status == "LIVE" else ("[~]" if status == "AUTH REQUIRED" else "[FAIL]")
                print(f"  [{marker}] {check.method:5} {check.path:40} -> {status}")
                if detail and status != "LIVE":
                    print(f"        {detail}")
                if status in ("SERVICE DOWN", "404 NOT BUILT", "SERVER ERROR"):
                    any_missing = True
            print()

    if any_missing:
        print("Result: at least one endpoint is not actually live. Fix these BEFORE checking")
        print("the frontend — a frontend bug report against a route that doesn't exist wastes time.")
        sys.exit(1)
    else:
        print("Result: every checked endpoint responded. Proceed to frontend verification.")


if __name__ == "__main__":
    main()