"""`axor-identity` console entry point."""
from __future__ import annotations

import os

import uvicorn

from axor_identity.app import create_app


def main() -> None:
    # migrations run in the app lifespan (create_app), same as axor-backend
    app = create_app()
    uvicorn.run(app, host=os.environ.get("AXOR_IDENTITY_HOST", "127.0.0.1"),
                port=int(os.environ.get("AXOR_IDENTITY_PORT", "8081")))


if __name__ == "__main__":
    main()
