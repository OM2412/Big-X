import os
import logging
from fastapi import FastAPI
from pydantic import BaseModel

from agent_registry import AgentRegistryClient
from erc7857_client import ERC7857Client
from metadata_resolver import MetadataResolver

logger = logging.getLogger(__name__)

app = FastAPI(title="Intelligent Asset Service")

registry = AgentRegistryClient()
nft_client = ERC7857Client()
metadata_resolver = MetadataResolver()


class MintRequest(BaseModel):
    agent_id: str
    metadata_uri: str
    owner: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/mint")
def mint_agent(request: MintRequest):
    metadata = metadata_resolver.resolve(request.metadata_uri)
    tx = nft_client.mint(request.owner, request.metadata_uri, metadata)
    registry.register_agent(request.agent_id, tx.contract_address)
    return {"tx_hash": tx.transactionHash.hex()}


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    return registry.get_agent(agent_id)
