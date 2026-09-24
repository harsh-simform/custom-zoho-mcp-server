import logging
import sys

from .config import LOG_LEVEL, LOG_FILE

_CONFIGURED = False


def setup_logging() -> None:
    """Configure root logging for the whole server. Idempotent.

    Stdio is the MCP transport's wire — stdout must stay pure JSON-RPC, so every
    handler here targets stderr (and optionally a file), never stdout.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    handlers: list[logging.Handler] = []

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(formatter)
    handlers.append(stderr_handler)

    if LOG_FILE:
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)
    root.handlers = handlers

    # Quiet down noisy third-party loggers unless the user cranks verbosity up.
    if logging.getLevelName(LOG_LEVEL) != logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
