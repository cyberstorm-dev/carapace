import argparse
import logging
import os
import sys
import time
from typing import List, Optional

import redis
from carapace.cli.gt import GiteaClient
from carapace.core.scheduler import Scheduler
from carapace.worker.pool import WorkerPool, APIKeyPool
from carapace.worker.host import HostWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_manager(gitea_url: str, token: str, repo: str, redis_url: str, poll_interval: int):
    client = GiteaClient(gitea_url, token, repo)
    # Pass a dummy pool; we just need the scheduler to compute the queue
    worker_pool = WorkerPool(HostWorker(), APIKeyPool([]), 1)
    scheduler = Scheduler(client, worker_pool, milestone=None)
    
    r = redis.from_url(redis_url, decode_responses=True)
    queue_key = f"carapace:queue:{repo}"
    
    logging.info(f"Starting Redis Queue Manager for {repo}")
    logging.info(f"Redis URL: {redis_url}")
    logging.info(f"Gitea URL: {gitea_url}")
    
    while True:
        try:
            # compute_ready_queue returns a list of issues sorted by topological priority
            ready_issues = scheduler.compute_ready_queue()
            
            if not ready_issues:
                logging.info("Ready queue is empty.")
                r.delete(queue_key)
            else:
                logging.info(f"Found {len(ready_issues)} ready issues.")
                
                zadd_args = {}
                count = len(ready_issues)
                # Assign scores so the highest priority (first item) has the highest score
                for idx, issue in enumerate(ready_issues):
                    issue_num = str(issue["number"])
                    score = float(count - idx)
                    zadd_args[issue_num] = score
                
                # Replace the zset entirely to ensure stale items drop out
                pipe = r.pipeline()
                pipe.delete(queue_key)
                if zadd_args:
                    pipe.zadd(queue_key, zadd_args)
                pipe.execute()
                
                logging.info(f"Updated Redis zset '{queue_key}' with items: {list(zadd_args.keys())}")
                
        except Exception as e:
            logging.error(f"Error during queue update: {e}", exc_info=True)
            
        time.sleep(poll_interval)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Redis-backed Priority Queue Manager")
    parser.add_argument("--gitea-url", default=os.environ.get("GITEA_URL", "http://100.73.228.90:3000"))
    parser.add_argument("--token", default=os.environ.get("GITEA_TOKEN"))
    parser.add_argument("--repo", default=os.environ.get("GITEA_REPO", "openclaw/nisto-home"))
    parser.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://cyberstorm-citadel:6379/0"))
    parser.add_argument("--poll-interval", type=int, default=int(os.environ.get("POLL_INTERVAL", "15")))
    
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
