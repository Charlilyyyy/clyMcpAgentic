"""Component 1 — Transport & Session Layer (scaffold).

Full stdio and Streamable HTTP wiring is added when the transport layer is
implemented. This module exists so the package installs and imports cleanly.
"""

from __future__ import annotations

import logging

from atlas_mcp.config import get_settings

logger = logging.getLogger(__name__)


def main() -> None:
    """CLI entry point for ``atlas-mcp``."""
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    logger.info(
        "atlas-mcp scaffold ready — transport layer not yet wired",
        extra={"transport": settings.transport, "service": settings.service_name},
    )


if __name__ == "__main__":
    main()
