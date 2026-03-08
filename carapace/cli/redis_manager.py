import argparse
import logging
import os
import sys
from typing import List, Optional

from carapace.core.queue import run_daemon

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_manager(gitea_url: str, token: str, repo: str, redis_url: str, poll_interval: int):
    """Backward-compatible wrapper around the queue daemon helper."""
    return run_daemon(gitea_url, token, repo, redis_url, poll_interval)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Redis-backed Priority Queue Manager")
    parser.add_argument("--gitea-url", default=os.environ.get("GITEA_URL", "http://100.73.228.90:3000"))
    parser.add_argument("--token", default=os.environ.get("GITEA_TOKEN"))
    parser.add_argument("--repo", default=os.environ.get("GITEA_REPO", "openclaw/nisto-home"))
    parser.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://cyberstorm-citadel:6379/0"))
    parser.add_argument("--poll-interval", type=int, default=int(os.environ.get("POLL_INTERVAL", "60")))

    args = parser.parse_args(argv or sys.argv[1:])

    if not args.token:
        print("Error: GITEA_TOKEN is required", file=sys.stderr)
        return 1

    try:
        run_manager(args.gitea_url, args.token, args.repo, args.redis_url, args.poll_interval)
    except KeyboardInterrupt:
        logging.info("Exiting...")
        return 0
    return 1

if __name__ == "__main__":
    sys.exit(main())
