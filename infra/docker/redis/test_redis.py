
import asyncio
import uuid
import sys
sys.path.insert(0, r'agentic-defi-platform\infra\docker\redis')
from redis_client import redis_client

async def main():
    print("\n=== Redis Integration Test ===")
    
    healthy = await redis_client.health_check()
    print("Connection Pool:", "PASS" if healthy else "FAIL")
    
    await redis_client.cache_set("price", "ethereum:ETH", {"price": 3500}, ttl=60)
    cached = await redis_client.cache_get("price", "ethereum:ETH")
    print("Cache:", "PASS" if cached and cached["price"] == 3500 else "FAIL", cached)
    
    session_id = str(uuid.uuid4())
    await redis_client.create_session(session_id, {"user_id": "user-123", "wallet": "0xABC123"}, ttl=60)
    session = await redis_client.get_session(session_id)
    print("Sessions:", "PASS" if session and session["user_id"] == "user-123" else "FAIL", session)
    
    wallet = "0xTEST123"
    period = "test"
    client = await redis_client.get_client()
    await client.delete(f"spend:{period}:{wallet.lower()}")
    spend = await redis_client.increment_spend(wallet_address=wallet, amount=100, period=period, ttl=60)
    print("Spend Counter:", "PASS" if spend == 100 else "FAIL", spend)
    
    identifier = f"test-user-{uuid.uuid4()}"
    rate_result = await redis_client.check_rate_limit(identifier=identifier, limit=5, window_seconds=60)
    print("Rate Limit:", "PASS" if rate_result["allowed"] else "FAIL", rate_result)
    
    lock = await redis_client.acquire_nft_lock(chain_id=1, nft_contract="0xABC123", token_id=100, timeout=30)
    if lock:
        print("Distributed Lock: PASS")
        await redis_client.release_lock(lock)
    else:
        print("Distributed Lock: FAIL")
    
    await redis_client.delete_session(session_id)
    await redis_client.close()
    print("\n=== Test Complete ===")

if __name__ == "__main__":
    asyncio.run(main())
