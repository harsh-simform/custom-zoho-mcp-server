"""Token-budget helper: makes "token-optimized" a checked claim.

The estimator that GATES tests is a deterministic, offline proxy: ``ceil(len(text) / 4)``.

Weakness of the proxy (read before trusting a budget): it is not a tokenizer. English prose averages
about 4 characters per token, so prose is estimated well. Output that is dense in punctuation, digits,
long ids or non-ASCII text (which TOON rows with Zoho ids can be) usually costs MORE tokens per
character than 4, so the proxy can under-count such output by a wide margin; very repetitive or
whitespace-heavy output is over-counted. Treat budgets as regression guards on relative size, not as
exact billing numbers, and leave headroom when setting them.

``tiktoken`` (optional extra: ``pip install -e ".[tokens]"``) is used for REPORTING ONLY: if it is
installed, the end-of-run summary and failure messages also show its ``cl100k_base`` count. No test
outcome ever depends on it, and nothing here touches the network (tiktoken may try to download its
vocabulary the first time; any failure there is swallowed and the column is simply omitted).

Budgets are declared next to the tests of the command they guard::

    BUDGET_TASK_DEFAULT = 600
    assert_token_budget(render(result).text, BUDGET_TASK_DEFAULT, label="task default")
"""
from __future__ import annotations

import math
from typing import NamedTuple

CHARS_PER_TOKEN = 4


class Measurement(NamedTuple):
    label: str
    measured: int
    allowed: int
    chars: int
    tiktoken: int | None


# Filled by every assert_token_budget call; printed by conftest's terminal summary.
MEASUREMENTS: list[Measurement] = []


def estimate_tokens(text: str) -> int:
    """The gating proxy: characters / 4, rounded up. Deterministic and offline."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def tiktoken_count(text: str) -> int | None:
    """Optional, reporting only. None when tiktoken (or its vocabulary) is unavailable."""
    try:
        import tiktoken  # type: ignore[import-not-found]

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return None


def assert_token_budget(text: str, max_tokens: int, label: str = "") -> int:
    """Fail loudly, with measured vs allowed, when ``text`` is estimated above ``max_tokens``.
    Returns the measured estimate so callers can also log or compare it."""
    if max_tokens < 0:
        raise ValueError(f"max_tokens must be >= 0, got {max_tokens}")
    measured = estimate_tokens(text)
    MEASUREMENTS.append(Measurement(label or "(unlabeled)", measured, max_tokens, len(text), None))
    if measured > max_tokens:
        extra = tiktoken_count(text)
        report = f", tiktoken {extra}" if extra is not None else ""
        name = f" for {label}" if label else ""
        raise AssertionError(
            f"token budget exceeded{name}: measured {measured} > allowed {max_tokens} "
            f"(proxy ceil(chars/4) over {len(text)} chars{report})"
        )
    return measured
