#!/usr/bin/env python3
# scripts/seed_demo_data.py
#
# Populates realistic-looking demo data directly into Postgres — agents
# with names/personas, execution history, portfolio positions. Run this
# against a demo/staging database, never production. This is what makes
# a first demo (investor, testnet user, hackathon judge) land on a
# populated dashboard instead of an empty one.
#
# Usage: python scripts/seed_demo_data.py --user-wallet 0xYourTestWallet

import argparse
import asyncio
import random
import sys
import uuid
from datetime import datetime, timedelta

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select

from db import session_factory, init_db_schema, close_db
from db.models.users import User
from db.models.agents import Agent, LifecycleState
from db.models.transactions import Transaction, TransactionType, TransactionStatus
from db.models.execution_history import ExecutionStep, AgentRole, StepStatus

DEMO_AGENTS = [
    {"name": "Yield Sentinel", "persona": "Conservative — Aave/Compound only, low slippage tolerance", "capabilities": 0b00101},
    {"name": "Basis Runner", "persona": "Cross-DEX arbitrage, tight risk limits", "capabilities": 0b00001},
    {"name": "Bridge Custodian", "persona": "BTC/ETH bridging specialist", "capabilities": 0b00011},
]

DEMO_TX_TEMPLATES = [
    {"type": TransactionType.SWAP, "token": "ETH", "amount_usd": (50, 400)},
    {"type": TransactionType.YIELD_DEPOSIT, "token": "USDC", "amount_usd": (200, 2000)},
    {"type": TransactionType.BRIDGE, "token": "BTC", "amount_usd": (100, 1500)},
]


async def seed(user_wallet: str, num_transactions_per_agent: int = 8):
    await init_db_schema()

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.wallet_address == user_wallet))
        user = result.scalar_one_or_none()

        if user is None:
            try:
                user = User(wallet_address=user_wallet, role="user", is_active=True)
                session.add(user)
                await session.flush()
                print(f"Created new user: {user_wallet}")
            except Exception:
                await session.rollback()
                result = await session.execute(select(User).where(User.wallet_address == user_wallet))
                user = result.scalar_one_or_none()
                if user is None:
                    raise
                print(f"User already exists (race condition): {user_wallet}")
        else:
            print(f"Using existing user: {user_wallet}")

        created_agents = 0
        for i, agent_def in enumerate(DEMO_AGENTS):
            result = await session.execute(
                select(Agent).where(
                    Agent.owner_id == user.id,
                    Agent.name == agent_def["name"],
                )
            )
            existing_agent = result.scalar_one_or_none()

            if existing_agent:
                print(f"  Agent {agent_def['name']} already exists for this user, skipping")
                continue

            max_nft_row = (await session.execute(
                select(Agent.nft_id).order_by(Agent.nft_id.desc()).limit(1)
            )).first()
            next_nft_id = (max_nft_row[0] + 1) if max_nft_row else 1000

            agent = Agent(
                id=uuid.uuid4(),
                nft_id=next_nft_id,
                chain_id=84532,
                owner_id=user.id,
                creator_wallet=user_wallet,
                name=agent_def["name"],
                persona=agent_def["persona"],
                model_version="v1",
                metadata_uri=f"ipfs://demo-{i}",
                token_bound_account=f"0x{'ab' * 19}{i:02d}",
                capabilities=agent_def["capabilities"],
                state=LifecycleState.ACTIVE,
                last_synced_at=datetime.utcnow(),
            )
            session.add(agent)
            await session.flush()
            created_agents += 1

            # Realistic-looking history, spread over the last 2 weeks,
            # mostly successful with a couple of failures — an all-green
            # history reads as fake to anyone who's seen a real trading log.
            base_time = datetime.utcnow() - timedelta(days=14)
            for j in range(num_transactions_per_agent):
                template = random.choice(DEMO_TX_TEMPLATES)
                is_success = random.random() > 0.15  # ~85% success rate, not 100%
                tx_time = base_time + timedelta(hours=random.randint(0, 14 * 24))

                tx = Transaction(
                    id=uuid.uuid4(),
                    agent_id=agent.id,
                    tx_hash=f"0x{'f' * 8}{j:056x}" if is_success else None,
                    chain_id=84532,
                    tx_type=template["type"],
                    status=TransactionStatus.CONFIRMED if is_success else TransactionStatus.REVERTED,
                    from_address=agent.token_bound_account,
                    to_address=f"0x{'cd' * 20}",
                    token_symbol=template["token"],
                    amount=round(random.uniform(0.01, 5.0), 6),
                    amount_usd=round(random.uniform(*template["amount_usd"]), 2),
                    gas_used=random.randint(80_000, 250_000),
                    policy_check_passed=is_success,
                    submitted_at=tx_time,
                    confirmed_at=tx_time + timedelta(seconds=random.randint(3, 30)) if is_success else None,
                )
                session.add(tx)

                step = ExecutionStep(
                    id=uuid.uuid4(),
                    task_id=uuid.uuid4(),
                    agent_id=agent.id,
                    transaction_id=tx.id,
                    role=AgentRole.CRITIC,
                    status=StepStatus.SUCCEEDED if is_success else StepStatus.FAILED,
                    sequence=5,
                    output_summary="Execution confirmed on-chain as expected." if is_success
                        else "Transaction reverted — slippage tolerance exceeded.",
                )
                session.add(step)

        await session.commit()
        print(f"Seeded demo user {user_wallet} with {created_agents} new agents "
              f"and {created_agents * num_transactions_per_agent} transactions.")
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-wallet", required=True, help="Wallet address to attach demo agents to")
    parser.add_argument("--tx-per-agent", type=int, default=8)
    args = parser.parse_args()

    asyncio.run(seed(args.user_wallet, args.tx_per_agent))