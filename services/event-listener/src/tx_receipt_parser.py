# tx_receipt_parser.py
#
# Layer 11 (Event Listener) — decodes raw transaction receipts from any of
# your contracts (AgentRegistry, ERC7857IntelligentNFT, BridgeContract,
# Marketplace) into structured events other services can act on: sync a DB
# row, fire a notification, update a dashboard.

import json
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

ABI_DIR = Path(__file__).parent / "abi"

# Contract address -> (name, abi) — populated at startup from env/config so
# this file doesn't hard-code deployment addresses per network.
_CONTRACT_REGISTRY: dict[str, tuple[str, list]] = {}


@dataclass
class ParsedEvent:
    contract_name: str
    event_name: str
    args: dict[str, Any]
    tx_hash: str
    block_number: int
    log_index: int


def register_contract(address: str, name: str, abi_filename: str) -> None:
    """Call once per contract at startup, e.g.:
    register_contract(AGENT_REGISTRY_ADDRESS, "AgentRegistry", "AgentRegistry.json")"""
    with open(ABI_DIR / abi_filename) as f:
        abi = json.load(f)
    _CONTRACT_REGISTRY[Web3.to_checksum_address(address)] = (name, abi)


def parse_receipt(w3: Web3, receipt: TxReceipt) -> list[ParsedEvent]:
    """Decodes every log in a receipt that matches a registered contract.
    Logs from unregistered contracts (unrelated txs sharing a block) are
    silently skipped rather than raising."""
    parsed_events: list[ParsedEvent] = []

    for log in receipt["logs"]:
        contract_address = Web3.to_checksum_address(log["address"])
        entry = _CONTRACT_REGISTRY.get(contract_address)
        if entry is None:
            continue

        contract_name, abi = entry
        contract = w3.eth.contract(address=contract_address, abi=abi)

        event = _decode_log(contract, log)
        if event is None:
            continue

        parsed_events.append(ParsedEvent(
            contract_name=contract_name,
            event_name=event.event,
            args=dict(event.args),
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            log_index=log["logIndex"],
        ))

    return parsed_events


def _decode_log(contract, log) -> Any:
    """Tries every event ABI on the contract until one matches this log's
    topic0 — web3.py doesn't give you a single "decode any event" call."""
    for event_abi in contract.events:
        try:
            return event_abi().process_log(log)
        except Exception:
            continue
    logger.debug("No matching event ABI for log in tx %s", log["transactionHash"].hex())
    return None