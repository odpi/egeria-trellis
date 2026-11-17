"""`studio web [--host H] [--port 8840] [--reload]`: serve Trellis Studio.

Port 8840 is laz's (D2); a freshstart deploy passes --port 7840.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from trellis_studio.config import DEFAULT_PORT


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="studio", description="Trellis Studio")
    sub = parser.add_subparsers(dest="command", required=True)
    web = sub.add_parser("web", help="serve the Studio web app")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT} (freshstart: 7840)")
    web.add_argument("--reload", action="store_true", help="development only")
    args = parser.parse_args(argv)

    if args.command == "web":
        import uvicorn
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        uvicorn.run("trellis_studio.app:create_app", factory=True, host=args.host, port=args.port,
                    reload=args.reload)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
