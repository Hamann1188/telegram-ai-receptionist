import asyncio
import logging

from receptionist.config import Settings
from receptionist.telegram.bot import run_polling


def main() -> None:
    # aiogram logs update ids and timings at INFO, never message text.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
    asyncio.run(run_polling(Settings()))


if __name__ == "__main__":
    main()
