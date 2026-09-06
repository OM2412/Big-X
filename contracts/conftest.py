# tests/e2e/conftest.py
# E2E test fixtures: SIWE authentication, API clients, on-chain verification, async task polling

import os, time, json, logging, pytest, httpx
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from pathlib import Path

logger = logging.getLogger(__name__)

API_GATEWAY = os.getenv("API_GATEWAY_URL", "http://localhost:8000")
ORCHESTRATOR = os.getenv("ORCHESTRATOR_URL", "http://localhost:8002")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://localhost:8001")
RPC_URL = os.getenv("CHAIN_RPC_URL", "http://localhost:8545")
EXPECTED_CHAIN_ID = int(os.getenv("EXPECTED_CHAIN_ID", "31337"))
NETWORK = os.getenv("NETWORK", "base")

LOCAL_DEV_ACCOUNT = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
E2E_PRIVATE_KEY = os.environ.get("E2E_PRIVATE_KEY")
if not E2E_PRIVATE_KEY:
    if EXPECTED_CHAIN_ID in (31337, 1337):
        E2E_PRIVATE_KEY = LOCAL_DEV_ACCOUNT
    else:
        raise RuntimeError("E2E_PRIVATE_KEY is required for non-local networks")

ACCOUNT = Account.from_key(E2E_PRIVATE_KEY)

def load_abi(name):
    path = Path(__file__).resolve().parents[2] / "services" / "shared" / "abi" / f"{name}.json"
    return json.load(open(path)) if path.exists() else []

def load_addresses():
    path = Path(__file__).resolve().parents[2] / "services" / "shared" / "config" / f"deployed_addresses.{NETWORK}.json"
    return json.load(open(path)).get("addresses", {}) if path.exists() else {}

def _require_address(value, name):
    if not value or not Web3.is_address(value):
        raise ValueError(f"Invalid contract address for {name}: {value}")
    return Web3.to_checksum_address(value)

@pytest.fixture(scope="session")
def w3():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    assert w3.is_connected(), f"RPC not connected: {RPC_URL}"
    assert w3.eth.chain_id == EXPECTED_CHAIN_ID, f"Chain mismatch: expected {EXPECTED_CHAIN_ID}, got {w3.eth.chain_id}"
    return w3

@pytest.fixture(scope="session")
def account():
    return ACCOUNT

@pytest.fixture(scope="session")
def addresses():
    return load_addresses()

@pytest.fixture(scope="session")
def auth_token(account):
    with httpx.Client(base_url=AUTH_SERVICE_URL, timeout=10) as client:
        nonce_resp = client.post("/siwe/nonce", json={"address": account.address})
        assert nonce_resp.status_code == 200, f"Nonce failed: {nonce_resp.text}"
        nonce = nonce_resp.json()["nonce"]
        message = nonce_resp.json()["message"]
        signature = account.sign_message(encode_defunct(text=message)).signature.hex()
        verify_resp = client.post("/siwe/verify", json={"address": account.address, "signature": f"0x{signature}", "message": message})
        assert verify_resp.status_code == 200, f"Verify failed: {verify_resp.text}"
        return verify_resp.json()["token"]

@pytest.fixture
def api(auth_token):
    client = httpx.Client(base_url=API_GATEWAY, headers={"Authorization": f"Bearer {auth_token}"}, timeout=30)
    yield client
    client.close()

@pytest.fixture
def orch(auth_token):
    client = httpx.Client(base_url=ORCHESTRATOR, headers={"Authorization": f"Bearer {auth_token}"}, timeout=60)
    yield client
    client.close()

@pytest.fixture
def contracts(w3, addresses):
    class Contracts:
        def __init__(self):
            self.agent_registry = None
            self.nft = None
            self.policy = None
            self.account = None

            if "AgentRegistry" in addresses:
                self.agent_registry = w3.eth.contract(
                    address=_require_address(addresses["AgentRegistry"], "AgentRegistry"),
                    abi=load_abi("AgentRegistry")
                )
            if "ERC7857IntelligentNFT" in addresses:
                self.nft = w3.eth.contract(
                    address=_require_address(addresses["ERC7857IntelligentNFT"], "ERC7857IntelligentNFT"),
                    abi=load_abi("ERC7857IntelligentNFT")
                )
            if "PolicyEngine" in addresses:
                self.policy = w3.eth.contract(
                    address=_require_address(addresses["PolicyEngine"], "PolicyEngine"),
                    abi=load_abi("PolicyEngine")
                )
            if "AgentAccount" in addresses:
                self.account = w3.eth.contract(
                    address=_require_address(addresses["AgentAccount"], "AgentAccount"),
                    abi=load_abi("AgentAccount")
                )
    return Contracts()

@pytest.fixture
def create_agent(api, orch):
    def _create(name="E2E Agent", metadata_uri="ipfs://test", encrypted_hash="0x" + "0" * 64):
        resp = api.post("/v1/agents", json={"name": name, "metadata_uri": metadata_uri, "encrypted_data_hash_hex": encrypted_hash})
        assert resp.status_code == 202, f"Create failed: {resp.status_code} {resp.text}"
        task_id = resp.json()["idempotency_key"]
        for _ in range(30):
            status_resp = orch.get(f"/v1/tasks/{task_id}")
            if status_resp.status_code == 200:
                data = status_resp.json()
                if data.get("status") == "completed":
                    return data.get("result", {}).get("agent_id"), task_id, data
                if data.get("status") == "failed":
                    raise AssertionError(f"Task failed: {data}")
            time.sleep(2)
        raise TimeoutError(f"Task {task_id} timed out")
    return _create

@pytest.fixture
def authorize_session(account, w3, addresses, contracts):
    def _authorize(agent_id, session_key, expiry=None, limit=10**18, targets=None, any_target=False):
        if expiry is None:
            expiry = int(time.time()) + 86400
        targets = targets or []
        nft = contracts.nft
        if not nft:
            nft = w3.eth.contract(address=_require_address(addresses.get("ERC7857IntelligentNFT"), "ERC7857IntelligentNFT"), abi=load_abi("ERC7857IntelligentNFT"))
        nft_id = int(agent_id) if isinstance(agent_id, str) and agent_id.isdigit() else agent_id
        agent_account = contracts.account
        if not agent_account:
            agent_account = w3.eth.contract(address=_require_address(addresses.get("AgentAccount"), "AgentAccount"), abi=load_abi("AgentAccount"))
        hash = Web3.solidity_keccak(["uint256", "address", "uint256", "address[]", "bool"], [nft_id, session_key, expiry, targets, any_target])
        signature = account.sign_message(encode_defunct(hash)).signature.hex()
        tx = agent_account.functions.authorizeSessionKeyWithSignature(
            session_key, expiry, limit, targets, any_target, 0, f"0x{signature}"
        ).build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address)})
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        assert receipt.status == 1, f"Authorization tx failed: {tx_hash.hex()}"

        session_key_data = agent_account.functions.sessionKeys(Web3.to_checksum_address(session_key)).call()
        onchain_expiry, onchain_limit, onchain_any_target, active = session_key_data
        assert active, "Session key not active after authorization"
        assert onchain_expiry == expiry, f"Session key expiry mismatch: {onchain_expiry} != {expiry}"
        assert onchain_limit == limit, f"Session key limit mismatch: {onchain_limit} != {limit}"
        assert onchain_any_target == any_target, f"Session key anyTarget mismatch: {onchain_any_target} != {any_target}"

        return {
            "hash": hash.hex(),
            "signature": f"0x{signature}",
            "expiry": expiry,
            "limit": limit,
            "targets": targets,
            "any_target": any_target,
            "tx_hash": tx_hash.hex()
        }
    return _authorize

@pytest.fixture
def verify_onchain(w3, contracts):
    def _verify(agent_id, expected_owner, expected_metadata_uri=None, expected_hash=None):
        if contracts.nft:
            owner = contracts.nft.functions.ownerOf(int(agent_id)).call()
            assert owner.lower() == expected_owner.lower(), f"NFT owner {owner} != {expected_owner}"
            if expected_metadata_uri:
                metadata = contracts.nft.functions.getAgentMetadata(int(agent_id)).call()
                assert metadata[0] == expected_metadata_uri, f"Metadata URI mismatch: {metadata[0]} != {expected_metadata_uri}"
            if expected_hash is not None:
                metadata = contracts.nft.functions.getAgentMetadata(int(agent_id)).call()
                onchain_hash = metadata[1]
                if isinstance(onchain_hash, bytes):
                    onchain_hash = "0x" + onchain_hash.hex()
                assert onchain_hash.lower() == expected_hash.lower(), f"Encrypted hash mismatch: {onchain_hash} != {expected_hash}"
        if contracts.agent_registry:
            try:
                agent = contracts.agent_registry.functions.getAgent(int(agent_id)).call()
                assert agent is not None, "Agent not found in registry"
                assert agent.owner.lower() == expected_owner.lower(), f"Registry owner mismatch: {agent.owner} != {expected_owner}"
            except Exception as e:
                raise AssertionError(f"AgentRegistry verification failed for agent {agent_id}: {e}")
        return True
    return _verify

@pytest.fixture
def verify_policy(w3, contracts):
    def _verify(agent_id, target, value, expected_allowed, data=None):
        if contracts.policy:
            calldata = data if data is not None else b""
            allowed, reason = contracts.policy.functions.checkAction(int(agent_id), target, value, calldata).call()
            assert allowed == expected_allowed, f"Policy check failed: {reason}"
        return True
    return _verify

@pytest.fixture
def verify_events(w3, addresses):
    def _verify(tx_hash, expected_event_name, expected_args=None):
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        assert receipt.status == 1, f"Transaction failed: {tx_hash}"
        logs = receipt.logs
        assert len(logs) > 0, "No events emitted"

        matched = False
        for log in logs:
            try:
                parsed = None
                for abi_name in ("AgentRegistry", "ERC7857IntelligentNFT", "PolicyEngine", "AgentAccount"):
                    abi = load_abi(abi_name)
                    if not abi:
                        continue
                    contract = w3.eth.contract(address=_require_address(addresses.get(abi_name), abi_name), abi=abi)
                    try:
                        parsed = contract.events[expected_event_name]().process_log(log)
                        break
                    except Exception:
                        continue
                if parsed:
                    if expected_args:
                        for key, expected_value in expected_args.items():
                            actual_value = parsed.args.get(key)
                            assert actual_value == expected_value, f"Event arg {key} mismatch: {actual_value} != {expected_value}"
                    matched = True
                    break
            except Exception:
                continue

        assert matched, f"Expected event {expected_event_name} not found in transaction logs"
        return True
    return _verify

@pytest.fixture
def api_client(api):
    return api

@pytest.fixture
def orchestrator_client(orch):
    return orch

@pytest.fixture
def cleanup(api):
    def _cleanup(agent_id):
        try:
            resp = api.delete(f"/v1/agents/{agent_id}")
            if resp.status_code not in (200, 204, 404):
                logger.warning(f"Cleanup for agent {agent_id} returned {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.warning(f"Cleanup failed for agent {agent_id}: {e}")
    return _cleanup
