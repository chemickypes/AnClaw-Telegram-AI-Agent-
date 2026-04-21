import logging
import sys

from anclaw_telegram_agent.agent import AIAgent
from anclaw_telegram_agent.bot import TelegramBot
from anclaw_telegram_agent.config import BotMode, Config


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    setup_logging()

    config = Config.from_env()
    logger.info(f"Modalità: {config.mode.value.upper()}")

    agent = AIAgent()
    bot = TelegramBot(config=config, agent=agent)
    bot.run()


logger = logging.getLogger(__name__)

if __name__ == "__main__":
    main()
