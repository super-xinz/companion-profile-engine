import os

import uvicorn

from .config import get_settings


def run() -> None:
    port = int(os.getenv("PORT", str(get_settings().port)))
    # Container services must listen on every interface; authentication remains at the API layer.
    uvicorn.run("profile_engine.api:app", host="0.0.0.0", port=port, reload=False)  # nosec B104


if __name__ == "__main__":
    run()
