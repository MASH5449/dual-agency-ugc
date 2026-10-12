"""Base class shared by every LeadLoop agent (stdlib only)."""

from __future__ import annotations

import logging
from typing import Any, Iterable

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once, with a plain readable format."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S")
    else:
        root.setLevel(level)


class Agent:
    """Small base class giving every agent a tag and an error-bounded run.

    Subclasses implement :meth:`handle`, which processes a single item. The
    base :meth:`run` iterates the batch, catches per-item exceptions, and logs
    them under the agent tag — a bad item never takes the process down and
    never kills the surrounding loop.
    """

    #: Human-readable agent name, e.g. "Scraper".
    name = "Agent"
    #: Log tag used in every line this agent writes, e.g. "[Agent-Scraper]".
    tag = "[Agent]"

    def __init__(self, config: Any = None) -> None:
        self.config = config
        # The logger name *is* the tag, so every line carries it exactly once.
        self.logger = logging.getLogger(self.tag)

    # -- logging ----------------------------------------------------------
    def log(self, message: str, level: int = logging.INFO) -> None:
        """Log ``message`` under this agent's tag."""
        self.logger.log(level, message)

    def log_error(self, message: str) -> None:
        self.log(message, level=logging.ERROR)

    # -- lifecycle --------------------------------------------------------
    def handle(self, item: Any) -> Any:
        """Process one item. Subclasses override this."""
        raise NotImplementedError

    def run(self, items: Iterable[Any] | None = None) -> list[Any]:
        """Run :meth:`handle` over every item, never raising on a bad item."""
        results: list[Any] = []
        for item in items or []:
            try:
                outcome = self.handle(item)
                if outcome is not None:
                    results.append(outcome)
            except Exception as exc:  # noqa: BLE001 - deliberate safety net
                self.log_error(f"item failed ({type(exc).__name__}: {exc})")
        return results
