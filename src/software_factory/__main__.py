import logging

import uvicorn
from dotenv import load_dotenv

from software_factory.api import create_app
from software_factory.config import Settings


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
