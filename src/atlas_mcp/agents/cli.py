"""CLI entry point for the support copilot (scaffold)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    """CLI entry point for ``atlas-copilot``."""
    logging.basicConfig(level=logging.INFO)
    logger.info("atlas-copilot scaffold ready — agent layer not yet wired")
