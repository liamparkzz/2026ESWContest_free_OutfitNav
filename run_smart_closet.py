#!/usr/bin/env python3
from __future__ import annotations

import argparse
import uvicorn

from closet_system.config import load_config
from closet_system.factory import build_controller
from closet_system.web.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Closet integrated web + hardware runtime")
    parser.add_argument("--config", default="config.smart_closet.yaml")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mock", action="store_true", help="실제 HW 없이 UI/API 테스트")
    args = parser.parse_args()

    cfg = load_config(args.config)
    controller = build_controller(cfg, mock=args.mock)
    app = create_app(controller, cfg)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
