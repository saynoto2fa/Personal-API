"""Run the API with logs written to a file, for background start (Task Scheduler + pythonw.exe).

    python -m app.serve --log-file logs/api.log     # 127.0.0.1:8000
For development, `uvicorn app.main:app --reload` is still the usual way.
"""

import argparse
from pathlib import Path

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.serve", description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-file", type=Path, default=Path("logs/api.log"), metavar="PATH")
    args = parser.parse_args(argv)

    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(args.log_file),
        "maxBytes": 1_000_000,
        "backupCount": 3,
        "encoding": "utf-8",
        "formatter": "default",
    }
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers": {"file": handler},
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["file"], "level": "WARNING"},
    }
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_config=log_config)


if __name__ == "__main__":
    main()
