"""What a chat model's own end-of-reply metadata says about how the reply ended.

Every provider adapter leaves the reason a reply stopped in the message's
``response_metadata``, each under its own key:

- Amazon Bedrock Converse: ``stopReason`` (``langchain-aws``).
- Anthropic Messages: ``stop_reason`` (``langchain-anthropic``).
- OpenAI-style chat, which is what LiteLLM speaks to OpenRouter: ``finish_reason``
  (``langchain-litellm`` keeps it on a complete reply and drops it on a streamed one,
  so ``litellm_chat`` carries it across).

A reply that stopped because the model ran out of room is not a failure any of them
raise: the stream ends normally, the message is well formed, and what it holds is
whatever fitted. On a reasoning model the room runs out inside the reasoning, so the
message can hold no text at all (ADR 0014 records the budget).
"""

from collections.abc import Mapping
from typing import Any

#: The metadata keys that carry a stop reason, one per provider family (see above).
STOP_REASON_KEYS = ("stopReason", "stop_reason", "finish_reason")

#: Stop reasons that mean the reply was cut off, not finished: the output budget was
#: spent (``max_tokens`` on Bedrock and Anthropic, ``length`` on OpenAI-style chat) or
#: the conversation filled the model's context window.
TRUNCATING_STOP_REASONS = frozenset({"max_tokens", "length", "model_context_window_exceeded"})

#: ``response_metadata`` key the OpenRouter model sets on a streamed reply whose usage
#: is LiteLLM's own token estimate because the provider sent none. The estimate has no
#: cache or reasoning counts, so a cost computed from it is approximate.
USAGE_ESTIMATED_KEY = "osa_usage_estimated"


def _is_repeat_of(value: str, reason: str) -> bool:
    """Whether ``value`` is ``reason`` written one or more times back to back.

    Two chunks of a stream that both carry a string in ``response_metadata`` have it
    concatenated when langchain-core merges them into the message, so a reason seen on
    more than one chunk reads ``max_tokensmax_tokens``. Adapters send it once, but what
    the merge does to it is not theirs to promise.
    """
    count, remainder = divmod(len(value), len(reason))
    return count > 0 and remainder == 0 and value == reason * count


def truncation_reason(response_metadata: Mapping[str, Any] | None) -> str | None:
    """The stop reason if it says the reply was cut off, else None.

    Args:
        response_metadata: A message's or chunk's ``response_metadata``.

    Returns:
        The stop reason as the provider named it (``max_tokens``, ``length``, ...), or
        None when the reply finished, the metadata has no stop reason, or it is not a
        mapping.
    """
    if not isinstance(response_metadata, Mapping):
        return None
    for key in STOP_REASON_KEYS:
        value = response_metadata.get(key)
        if not isinstance(value, str) or not value:
            continue
        for reason in TRUNCATING_STOP_REASONS:
            if _is_repeat_of(value, reason):
                return reason
    return None
