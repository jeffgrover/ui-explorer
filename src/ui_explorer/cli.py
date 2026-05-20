from __future__ import annotations

import argparse
import asyncio

from .crawler import CrawlConfig, Explorer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ui-explorer",
        description="Headed-only crawler for taking a first pass through a web app.",
    )
    parser.add_argument("--app-url", required=True, help="Base URL to explore.")
    parser.add_argument(
        "--llm-endpoint",
        help="Optional OpenAI-compatible /chat/completions endpoint for follow-up notes.",
    )
    parser.add_argument(
        "--llm-model",
        default="llama3.1:8b",
        help="Model name sent to the OpenAI-compatible endpoint.",
    )
    parser.add_argument(
        "--visual-exploration",
        action="store_true",
        help="Send page screenshots to a vision-capable OpenAI-compatible model for visual notes.",
    )
    parser.add_argument(
        "--vision-endpoint",
        help="Optional vision-capable /chat/completions endpoint. Defaults to --llm-endpoint.",
    )
    parser.add_argument(
        "--vision-model",
        help="Optional vision-capable model name. Defaults to --llm-model.",
    )
    parser.add_argument(
        "--profile-dir",
        default=".ui-explorer-profile",
        help="Browser profile directory. Reuse this to keep login cookies.",
    )
    parser.add_argument("--max-routes", type=int, default=25)
    parser.add_argument("--max-clicks-per-route", type=int, default=8)
    parser.add_argument("--max-seconds", type=int, default=600)
    parser.add_argument(
        "--login-timeout-seconds",
        type=int,
        default=900,
        help="How long to wait while you complete an initial login flow.",
    )
    parser.add_argument(
        "--artifact-dir",
        default="artifacts",
        help="Directory where run artifacts are written.",
    )
    return parser


async def run(args: argparse.Namespace) -> None:
    config = CrawlConfig(
        app_url=args.app_url,
        llm_endpoint=args.llm_endpoint,
        llm_model=args.llm_model,
        visual_exploration=args.visual_exploration,
        vision_endpoint=args.vision_endpoint,
        vision_model=args.vision_model,
        profile_dir=args.profile_dir,
        artifact_dir=args.artifact_dir,
        max_routes=args.max_routes,
        max_clicks_per_route=args.max_clicks_per_route,
        max_seconds=args.max_seconds,
        login_timeout_seconds=args.login_timeout_seconds,
    )
    explorer = Explorer(config)
    await explorer.run()


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
