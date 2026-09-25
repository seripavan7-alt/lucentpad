"""Run the API: ``python -m lucentpad_server`` (listens on 0.0.0.0:$PORT, default 8000)."""

from __future__ import annotations

import logging
import os

import uvicorn

from lucentpad_server.app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port, proxy_headers=True)  # noqa: S104


if __name__ == "__main__":
    main()
