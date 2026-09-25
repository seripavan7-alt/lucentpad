"""Attribute keys and limits the SDK emits.

These duplicate ``lucentpad_server.schema.Attr`` (and ``PREVIEW_MAX_CHARS`` / ``MAX_BATCH_SPANS``)
on purpose: the SDK must not depend on the server package at runtime. ``sdk/tests`` asserts that
every value here equals its ``schema`` counterpart, so drift fails the build.
"""

from __future__ import annotations

from typing import Final

PREVIEW_MAX_CHARS: Final = 2000  # schema.PREVIEW_MAX_CHARS
MAX_BATCH_SPANS: Final = 1000  # schema.MAX_BATCH_SPANS
NAME_MAX_CHARS: Final = 200  # schema.Span.name max_length
STATUS_MESSAGE_MAX_CHARS: Final = 2000  # schema.Span.status_message max_length


class Attr:
    """Subset of ``lucentpad_server.schema.Attr`` used by the SDK (same names, same values)."""

    SERVICE_NAME: Final = "service.name"
    GEN_AI_SYSTEM: Final = "gen_ai.system"
    GEN_AI_OPERATION: Final = "gen_ai.operation.name"
    GEN_AI_REQUEST_MODEL: Final = "gen_ai.request.model"
    GEN_AI_RESPONSE_MODEL: Final = "gen_ai.response.model"
    GEN_AI_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
    GEN_AI_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"
    GEN_AI_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"
    GEN_AI_TOOL_NAME: Final = "gen_ai.tool.name"
    STREAMING: Final = "lucentpad.streaming"
    CLIENT: Final = "lucentpad.client"
    INPUT_PREVIEW: Final = "lucentpad.input.preview"
    OUTPUT_PREVIEW: Final = "lucentpad.output.preview"
    INPUT_TRUNCATED: Final = "lucentpad.input.truncated"
    OUTPUT_TRUNCATED: Final = "lucentpad.output.truncated"
