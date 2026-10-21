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
