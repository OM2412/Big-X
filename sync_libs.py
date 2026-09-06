#!/usr/bin/env python3
# scripts/sync_abis.py
#
# Hardhat version. Full pipeline: Hardhat build artifact -> schema-
# validated ABI -> hash-checked atomic write -> deployed address sync
# from hardhat-deploy's deployments/ folder -> on-chain verification via
# eth_getCode. Structured JSON logging, contracts synced/verified in
# parallel.
#
# Two structural differences from a Foundry-based sync script:
#   1. Artifact layout: artifacts/contracts/<File>.sol/<Contract>.json,
#      and `bytecode` is a plain hex STRING field, not a nested
#      {"object": "..."} dict like Foundry's.
#   2. No built-in broadcast log — deployment addresses come from the
#      hardhat-deploy plugin's deployments/<network>/<Contract>.json
#      convention. If you're not using hardhat-deploy, replace
#      load_deployed_address()'s body with wherever your deploy script
#      actually writes addresses — the rest of the pipeline (hash
#      validation, atomic writes, eth_getCode verification) is unaffected.
#
# Usage:
#   python scripts/sync_abis.py                  # sync + verify
#   python scripts/sync_abis.py --check           # CI mode: fail on drift, write nothing
#   python scripts/sync_abis.py --skip-verify      # sync without on-chain eth_getCode checks

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from jsonschema import validate, ValidationError
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[1]
HARDHAT_ARTIFACTS = REPO_ROOT / "contracts" / "artifacts" / "src"
HARDHAT_DEPLOYMENTS = REPO_ROOT / "contracts" / "deployments"
NETWORK_NAME = os.environ.get("HARDHAT_NETWORK", "base")     # matches the network name in hardhat.config.ts, NOT a numeric chain id
CHAIN_ID = int(os.environ.get("CHAIN_ID", "8453"))             # used for RPC verification + output labeling only
RPC_URL = os.environ.get("CHAIN_RPC_URL")

ADDRESSES_OUT = REPO_ROOT / "services" / "shared" / "config" / f"deployed_addresses.{NETWORK_NAME}.json"

# contract_name -> (source file, [backend targets])
ABI_TARGETS: dict[str, tuple[str, list[Path]]] = {
    "AgentRegistry": ("AgentRegistry.sol", [
        REPO_ROOT / "services/intelligent-asset-service/src/abi/AgentRegistry.json",
        REPO_ROOT / "services/event-listener/src/abi/AgentRegistry.json",
        REPO_ROOT / "services/shared/abi/AgentRegistry.json",
    ]),
    "ERC7857IntelligentNFT": ("ERC7857IntelligentNFT.sol", [
        REPO_ROOT / "services/intelligent-asset-service/src/abi/ERC7857IntelligentNFT.json",
        REPO_ROOT / "services/event-listener/src/abi/ERC7857IntelligentNFT.json",
        REPO_ROOT / "services/shared/abi/ERC7857IntelligentNFT.json",
    ]),
    "BridgeContract": ("BridgeContract.sol", [
        REPO_ROOT / "services/bridging-service/src/abi/BridgeContract.json",
        REPO_ROOT / "services/event-listener/src/abi/BridgeContract.json",
        REPO_ROOT / "services/shared/abi/BridgeContract.json",
    ]),
    "PolicyEngine": ("PolicyEngine.sol", [
        REPO_ROOT / "services/risk-policy-engine/src/abi/PolicyEngine.json",
        REPO_ROOT / "services/shared/abi/PolicyEngine.json",
    ]),
    "Marketplace": ("Marketplace.sol", [
        REPO_ROOT / "services/tool-router/src/tools/abi/Marketplace.json",
        REPO_ROOT / "services/event-listener/src/abi/Marketplace.json",
        REPO_ROOT / "services/shared/abi/Marketplace.json",
    ]),
    "AgentAccount": ("AgentAccount.sol", [
        REPO_ROOT / "services/tool-router/src/abi/AgentAccount.json",
        REPO_ROOT / "services/shared/abi/AgentAccount.json",
    ]),
    "CapabilityRegistry": ("CapabilityRegistry.sol", [
        REPO_ROOT / "services/intelligent-asset-service/src/abi/CapabilityRegistry.json",
        REPO_ROOT / "services/shared/abi/CapabilityRegistry.json",
    ]),
}

# Hardhat's schema differs from Foundry's: bytecode is a hex STRING here,
# not {"object": "..."}. Also has contractName/sourceName fields Foundry
# doesn't.
HARDHAT_ARTIFACT_SCHEMA = {
    "type": "object",
    "required": ["abi", "bytecode", "contractName"],
    "properties": {
        "contractName": {"type": "string"},
        "abi": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type"],
                "properties": {"type": {"type": "string"}, "name": {"type": "string"}},
            },
        },
        "bytecode": {"type": "string", "pattern": "^0x"},
    },
}

logger = logging.getLogger("sync_abis")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname, "message": record.getMessage(),
            "logger": record.name, **getattr(record, "context", {}),
        }
        return json.dumps(payload)


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class AbiSyncError(Exception):
    pass


def _atomic_write_json(path: Path, data) -> None:
    """Temp file in the same directory, then os.replace() — a crash or
    Ctrl-C mid-write can never leave a truncated ABI file for a backend
    client to load."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _abi_hash(abi: list) -> str:
    canonical = json.dumps(abi, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_and_validate_artifact(contract_name: str, source_file: str) -> dict:
    artifact_path = HARDHAT_ARTIFACTS / source_file / f"{contract_name}.json"
    if not artifact_path.exists():
        raise AbiSyncError(f"No Hardhat artifact for {contract_name} at {artifact_path} — run `npx hardhat compile` first")

    with open(artifact_path) as f:
        artifact = json.load(f)

    try:
        validate(instance=artifact, schema=HARDHAT_ARTIFACT_SCHEMA)
    except ValidationError as exc:
        raise AbiSyncError(f"{contract_name} artifact failed schema validation: {exc.message}")

    return artifact


def load_deployed_address(contract_name: str) -> str | None:
    """hardhat-deploy convention: deployments/<network>/<Contract>.json,
    containing {"address": "0x...", "abi": [...], ...}.

    If you're not using hardhat-deploy, replace this function's body with
    a read from wherever your deploy script actually writes addresses —
    the rest of the pipeline is unaffected by where the address comes from."""
    deployment_path = HARDHAT_DEPLOYMENTS / NETWORK_NAME / f"{contract_name}.json"
    if not deployment_path.exists():
        return None

    with open(deployment_path) as f:
        deployment = json.load(f)

    return deployment.get("address")


async def verify_onchain_code(w3: Web3, contract_name: str, address: str) -> bool:
    """eth_getCode — confirms the address actually has deployed bytecode.
    Catches an address pointed at an EOA, a self-destructed contract, or
    simply the wrong network."""
    code = await asyncio.to_thread(w3.eth.get_code, Web3.to_checksum_address(address))
    has_code = len(code) > 0
    if not has_code:
        logger.error("No bytecode found on-chain", extra={"context": {"contract": contract_name, "address": address, "network": NETWORK_NAME}})
    return has_code


async def sync_one_contract(contract_name: str, source_file: str, targets: list[Path], w3: Web3 | None, check_only: bool) -> bool:
    ctx = {"contract": contract_name}
    ok = True

    src_path = REPO_ROOT / "contracts" / "src" / source_file
    if not src_path.exists():
        logger.warning("Source file not found — skipping", extra={"context": {**ctx, "source": str(src_path.relative_to(REPO_ROOT))}})
        return True

    try:
        artifact = load_and_validate_artifact(contract_name, source_file)
    except AbiSyncError as exc:
        logger.error(str(exc), extra={"context": ctx})
        return False

    abi = artifact["abi"]
    new_hash = _abi_hash(abi)

    for target_path in targets:
        target_ctx = {**ctx, "target": str(target_path.relative_to(REPO_ROOT))}
        existing_hash = None
        if target_path.exists():
            with open(target_path) as f:
                existing_hash = _abi_hash(json.load(f))

        if existing_hash == new_hash:
            logger.info("ABI up to date", extra={"context": {**target_ctx, "hash": new_hash[:12]}})
            continue

        ok = False
        if check_only:
            logger.warning("ABI out of sync", extra={"context": {**target_ctx, "expected_hash": new_hash[:12]}})
        else:
            _atomic_write_json(target_path, abi)
            logger.info("ABI synced", extra={"context": {**target_ctx, "hash": new_hash[:12]}})

    address = load_deployed_address(contract_name)
    if address is None:
        logger.warning("No deployment found for this network", extra={"context": {**ctx, "network": NETWORK_NAME}})
        return ok  # not a hard failure — contract may simply not be deployed yet

    if w3 is not None:
        has_code = await verify_onchain_code(w3, contract_name, address)
        if not has_code:
            ok = False

    return ok


async def sync_addresses(contract_names: list[str], check_only: bool) -> dict:
    addresses = {}
    for name in contract_names:
        address = load_deployed_address(name)
        if address:
            addresses[name] = address

    if not check_only and addresses:
        payload = {"network": NETWORK_NAME, "chain_id": CHAIN_ID, "addresses": addresses}
        _atomic_write_json(ADDRESSES_OUT, payload)
        logger.info("Deployment addresses synced", extra={"context": {"path": str(ADDRESSES_OUT.relative_to(REPO_ROOT)), "count": len(addresses)}})

    return addresses


async def run(check_only: bool, skip_verify: bool) -> bool:
    w3 = None
    if not skip_verify:
        if not RPC_URL:
            logger.error("CHAIN_RPC_URL not set — pass --skip-verify to sync without on-chain verification")
            return False
        w3 = Web3(Web3.HTTPProvider(RPC_URL))

    # All contracts synced/verified concurrently — the eth_getCode calls
    # are network I/O, so this is where parallelism actually pays off;
    # sequential would mean N RPC round-trips in series for no reason.
    results = await asyncio.gather(*[
        sync_one_contract(name, source_file, targets, w3, check_only)
        for name, (source_file, targets) in ABI_TARGETS.items()
    ])

    await sync_addresses(list(ABI_TARGETS.keys()), check_only)

    return all(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="CI mode: fail on drift, write nothing")
    parser.add_argument("--skip-verify", action="store_true", help="Skip eth_getCode checks (no RPC required)")
    args = parser.parse_args()

    _setup_logging()
    ok = asyncio.run(run(check_only=args.check, skip_verify=args.skip_verify))

    if not ok:
        logger.error("Sync failed — see errors above" if not args.check else "Drift detected — run without --check to fix")
        sys.exit(1)

    logger.info("All contracts synced and verified", extra={"context": {"network": NETWORK_NAME}})


if __name__ == "__main__":
    main()