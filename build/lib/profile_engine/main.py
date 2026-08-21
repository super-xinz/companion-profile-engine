import os

import uvicorn

from .config import get_settings


def run() -> None:
    port = int(os.getenv("PORT", str(get_settings().port)))
    uvicorn.run("profile_engine.api:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    run()
