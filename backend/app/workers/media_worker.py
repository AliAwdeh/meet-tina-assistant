import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


async def main_loop() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("media_worker_started")
    while True:
        await asyncio.sleep(60)


def main() -> None:
    asyncio.run(main_loop())


if __name__ == "__main__":
    main()
