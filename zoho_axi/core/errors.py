"""Structured errors shared by every module. The render layer (T05) turns these into
`error / message / hint` output and maps `exit_code` onto the process exit status."""

EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_UPSTREAM = 5
EXIT_RATE_LIMITED = 6


class ZohoAxiError(Exception):
    exit_code = 1

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


class ConfigError(ZohoAxiError):
    exit_code = EXIT_AUTH


class AuthError(ZohoAxiError):
    exit_code = EXIT_AUTH


class UpstreamError(ZohoAxiError):
    exit_code = EXIT_UPSTREAM


class NotFoundError(ZohoAxiError):
    exit_code = EXIT_NOT_FOUND


class RateLimitedError(ZohoAxiError):
    exit_code = EXIT_RATE_LIMITED


class ReadOnlyError(ZohoAxiError):
    """A non-GET request was attempted. This is a programming error, never a user error, so it keeps
    the generic exit code; it is raised before any network I/O happens."""
