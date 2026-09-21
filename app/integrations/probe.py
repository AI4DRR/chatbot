"""Connectivity probe for the configured Azure services.

Run as ``python -m app.integrations.probe``. It reads ``Settings`` from the
process environment, makes one minimal request to each service and logs only
non-sensitive metadata. Exit status is 0 when both probes succeed, 1 otherwise.
This is an operator tool; ``/api/chat`` never calls it.
"""

from __future__ import annotations

import logging
import sys

from app.config import load_settings
from app.errors import AppError
from app.integrations.azure_openai import build_azure_openai_client, chat_deployment, probe_chat_deployment
from app.integrations.azure_search import build_search_client, probe_index
from app.log import configure_logging

logger = logging.getLogger(__name__)


def run_probes() -> bool:
    """Probe both services; return True only if both answered."""
    settings = load_settings()
    ok = True

    try:
        client = build_azure_openai_client(settings)
        probe_chat_deployment(client, chat_deployment(settings), settings.azure_openai_api_version)
    except AppError as exc:
        ok = False
        logger.error("Azure OpenAI: %s", exc.detail)

    try:
        search = build_search_client(settings)
        assert settings.azure_search_index is not None  # validated by build_search_client
        probe_index(search, settings.azure_search_index)
    except AppError as exc:
        ok = False
        logger.error("Azure AI Search: %s", exc.detail)

    return ok


def main() -> int:
    """Entry point for ``python -m app.integrations.probe``."""
    configure_logging(load_settings().log_level)
    return 0 if run_probes() else 1


if __name__ == "__main__":
    sys.exit(main())
