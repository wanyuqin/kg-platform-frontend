"""飞书 MQ consumer 进程入口（feishu-sync §11）。"""

from __future__ import annotations

import asyncio
import logging

from app.storage.mq.consumer import FeishuEventConsumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _main() -> None:
    consumer = FeishuEventConsumer()
    logger.info("feishu mq worker starting")
    await consumer.run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
