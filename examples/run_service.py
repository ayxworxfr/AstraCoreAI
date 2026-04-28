"""Run FastAPI service."""

import logging

from dotenv import load_dotenv
import uvicorn

from astracore.service.api.app import create_app

load_dotenv()

# Enable INFO-level logs for astracore so MCP diagnostics are visible
logging.getLogger("astracore").setLevel(logging.INFO)
if not logging.getLogger("astracore").handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s: %(message)s"))
    logging.getLogger("astracore").addHandler(_handler)

if __name__ == "__main__":
    app = create_app()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
