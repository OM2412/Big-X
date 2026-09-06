import asyncio
import logging
import signal

from .transfer_listener import TransferListener
from .bridge_listener import BridgeListener
from .swap_listener import SwapListener
from .nft_listener import NFTListener
from .oracle_listener import OracleListener
from .governance_listener import GovernanceListener

logger = logging.getLogger(__name__)


class ListenerManager:
    def __init__(self):
        self.listeners = [
            TransferListener(),
            BridgeListener(),
            SwapListener(),
            NFTListener(),
            OracleListener(),
            GovernanceListener(),
        ]
        self.tasks = []

    async def start(self):
        logger.info("Starting Event Listener Service...")

        for listener in self.listeners:
            self.tasks.append(asyncio.create_task(listener.run()))

        logger.info("%d listeners started.", len(self.listeners))

        await asyncio.gather(*self.tasks)

    async def shutdown(self):
        logger.info("Stopping Event Listener Service...")

        for listener in self.listeners:
            await listener.stop()

        for task in self.tasks:
            task.cancel()

        logger.info("All listeners stopped.")


async def main():
    manager = ListenerManager()

    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(manager.shutdown())
        )

    await manager.start()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    asyncio.run(main())