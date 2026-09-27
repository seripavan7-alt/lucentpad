"""LLM gateway: Claude Code and Copilot point their base URL here; requests are forwarded to the
provider unchanged (streams chunk by chunk) and recorded as spans off the response path.

``GatewayProxy`` is the seam the routes depend on (part of the API contract). The app's lifespan
puts an implementation on ``app.state.gateway``; without one the gateway routes answer 501.
Client API keys are forwarded, never stored or logged.
"""

from __future__ import annotations

from typing import Protocol

from starlette.requests import Request
from starlette.responses import Response

from lucentpad_server.schema import GatewayProvider


class GatewayProxy(Protocol):
    async def forward(self, provider: GatewayProvider, path: str, request: Request) -> Response:
        """Forward ``request`` to ``provider``'s API at ``path`` (e.g. ``v1/messages``) and return
        its response, streaming when the upstream streams. Unknown paths answer 404."""
        ...
