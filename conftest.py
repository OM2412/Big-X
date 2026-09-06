# tests/e2e/conftest.py
#
# Real HTTP calls against your actually-running stack — no mocks. Run
# api-gateway, agent-orchestrator, and Anvil before running this suite.
# Uses Anvil's well-known dev account #0 to sign a real SIWE message —
# same key used in db/database.py's docstrings elsewhere in this build,
# never use this key for anything but a local throwaway chain.

import time
import pytest
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

API_GATEWAY_URL = "http://localhost:8000"
ORCHESTRATOR_URL = "http://localhost:8001"

DEV_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEV_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


@pytest.fixture(scope="session")
def dev_account():
    return Account.from_key(DEV_PRIVATE_KEY)


@pytest.fixture(scope="session")
def auth_token(dev_account) -> str:
    nonce_resp = httpx.get(f"{API_GATEWAY_URL}/v1/auth/nonce?address={dev_account.address.lower()}", timeout=5.0)
    nonce_resp.raise_for_status()
    nonce = nonce_resp.json()["nonce"]

    message = (
        f"localhost wants you to sign in with your Ethereum account:\n"
        f"{dev_account.address}\n\n"
        f"Sign in to Agentic DeFi Platform.\n\n"
        f"URI: http://localhost:3000\nVersion: 1\nChain ID: 31337\n"
        f"Nonce: {nonce}\nIssued At: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    signature = dev_account.sign_message(encode_defunct(text=message)).signature.hex()

    with httpx.Client(base_url=API_GATEWAY_URL, timeout=10.0) as client:
        response = client.post("/v1/auth/siwe", json={"message": message, "signature": f"0x{signature}"})

    assert response.status_code == 200, f"SIWE login failed: {response.status_code} {response.text}"
    token = response.json()["access_token"]
    assert token, "Login succeeded but returned no access_token"
    return token


@pytest.fixture
def api_client(auth_token):
    return httpx.Client(base_url=API_GATEWAY_URL, headers={"Authorization": f"Bearer {auth_token}"}, timeout=15.0)


@pytest.fixture
def orchestrator_client(auth_token):
    return httpx.Client(base_url=ORCHESTRATOR_URL, headers={"Authorization": f"Bearer {auth_token}"}, timeout=30.0)