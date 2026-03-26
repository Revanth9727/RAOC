"""RAOC entry point — wires all components together and starts the bot.

Run with: uv run python -m raoc.main
"""

import logging
from pathlib import Path

from raoc import config
from raoc.coordinator import PipelineCoordinator
from raoc.db.schema import create_tables
from raoc.gateway.telegram_bot import TelegramGateway
from raoc.substrate.command_wrapper import CommandWrapper
from raoc.substrate.host_sampler import HostSampler
from raoc.substrate.llm_client import LLMClient
from raoc.substrate.secret_broker import SecretBroker
from raoc.substrate.status_narrator import StatusNarrator


def main() -> None:
    """Initialise all components and start the Telegram gateway."""
    # 1. Logging — console + file
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.LOG_PATH),
        ],
    )

    # 2. Database
    create_tables()

    # 3–6. Substrate components
    broker = SecretBroker()
    llm = LLMClient(broker)
    sampler = HostSampler()
    cmd = CommandWrapper()
    narrator = StatusNarrator(llm)  # uses NARRATOR_MODEL (Haiku) per call

    # 7. Gateway (callbacks wired after coordinator is created)
    gateway = TelegramGateway(broker, on_message=None, on_approval=None)

    # 7.5 Policy substrate
    from raoc.agents.policy_agent import PolicyAgent
    from raoc.substrate.zone_resolver import ZoneResolver
    zone_resolver = ZoneResolver(config.ZONE_CONFIG)
    policy_agent = PolicyAgent(db=None, zone_resolver=zone_resolver)

    # 8. Coordinator
    coordinator = PipelineCoordinator(
        db=None,
        llm=llm,
        sampler=sampler,
        command_wrapper=cmd,
        gateway=gateway,
        narrator=narrator,
        policy_agent=policy_agent,
    )

    # 10. Wire callbacks
    gateway.on_message = coordinator.handle_new_message
    gateway.on_approval = coordinator.handle_approval

    # 11. Start
    logging.getLogger(__name__).info("RAOC controller starting...")

    # 12. Run (blocks)
    gateway.run()


if __name__ == "__main__":
    main()
