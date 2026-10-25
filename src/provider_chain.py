"""Provider chain-order parsing (v0.9.18.0) — ZERO side effects.

Shared parser for the two INDEPENDENT provider chains (user request:
chat 主鏈與 vision OCR 鏈是兩個不同的應用需求,各自可在 .env 調整
鏈則順序):

  * ``LLM_PROVIDER_CHAIN``    — chat LLM primary + failover chain
    (``Agent._resolved_provider_order``);
  * ``VISION_PROVIDER_CHAIN`` — vision OCR chain
    (``ImageDescriber._chain_names``).

This module deliberately does NOT import ``src.config`` (whose import
runs ``load_dotenv`` and would re-inject .env values that a test
fixture just wiped). Both consumers import the parser from HERE so a
first-touch import can never mutate ``os.environ``.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def parse_provider_chain(
    raw: str,
    valid_names: tuple[str, ...],
    default_names: tuple[str, ...] | None = None,
    aliases: dict[str, str] | None = None,
    warn_label: str = "provider chain",
) -> tuple[str, ...]:
    """Parse a comma-separated provider-chain env value into a tuple
    of canonical names.

    Grammar: comma-separated provider names (case-insensitive,
    whitespace-tolerant; ``;`` accepted as a separator too). Rules:

      1. empty / unset  -> ``default_names`` (legacy order, zero
         behaviour change);
      2. unknown tokens -> SKIPPED with a warning (a typo in one
         token must never take the chain down);
      3. duplicates     -> first occurrence wins;
      4. partial lists  -> the listed names take priority in the
         given order, then the remaining default names are appended
         in their default relative order. The chain defines
         PRIORITY, never exclusion — failover completeness is
         preserved (to drop a provider entirely, leave its key
         empty in .env).
    """
    defaults = tuple(default_names) if default_names else tuple(valid_names)
    if not raw or not raw.strip():
        return defaults
    alias_map = aliases or {}
    seen: list[str] = []
    invalid: list[str] = []
    for token in raw.replace(";", ",").split(","):
        tok = token.strip().lower()
        if not tok:
            continue
        name = alias_map.get(tok, tok)
        if name not in valid_names:
            invalid.append(tok)
            continue
        if name not in seen:
            seen.append(name)
    if invalid:
        logger.warning(
            "[config] %s: unknown provider token(s) skipped: %s "
            "(valid: %s)",
            warn_label, ", ".join(invalid), ", ".join(valid_names),
        )
    if not seen:
        logger.warning(
            "[config] %s: no valid provider token in %r — using the "
            "default order %s", warn_label, raw, list(defaults),
        )
        return defaults
    return tuple(seen) + tuple(n for n in defaults if n not in seen)
