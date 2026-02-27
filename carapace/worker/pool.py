import concurrent.futures
import json
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from .base import Worker, WorkerConfig, WorkerResult


@dataclass
class APIKey:
    label: str
    gemini_key: str
    gitea_token: str
    model: str = "gemini-1.5-pro"
    rate_limit_reset: datetime = field(default_factory=datetime.now)

    def is_available(self) -> bool:
        return datetime.now() >= self.rate_limit_reset


class APIKeyPool:
    def __init__(self, keys: List[APIKey]):
        self.keys = keys
        self._usage_counts: Dict[str, int] = {k.label: 0 for k in keys}

    @classmethod
    def from_file(cls, path: str) -> "APIKeyPool":
        with open(path, "r") as f:
            data = json.load(f)
        keys = [APIKey(**k) for k in data]
        return cls(keys)

    def get_next_available(self, model_preference: Optional[str] = None) -> Optional[APIKey]:
        """Simple least-used selection logic, considering rate limits."""
        available_keys = [k for k in self.keys if k.is_available()]
        
        if model_preference:
            model_keys = [k for k in available_keys if k.model == model_preference]
            if model_keys:
                available_keys = model_keys

        if not available_keys:
            return None
            
        # Sort by usage count
        sorted_keys = sorted(available_keys, key=lambda k: self._usage_counts[k.label])
        key = sorted_keys[0]
        self._usage_counts[key.label] += 1
        return key

    def mark_rate_limited(self, label: str, reset_after_seconds: int = 60):
        for key in self.keys:
            if key.label == label:
                key.rate_limit_reset = datetime.now() + timedelta(seconds=reset_after_seconds)
                print(f"Key '{label}' marked as rate limited until {key.rate_limit_reset}")
                break


class WorkerPool:
    def __init__(self, worker_impl: Worker, key_pool: APIKeyPool, max_parallel: int = 5):
        self.worker_impl = worker_impl
        self.key_pool = key_pool
        self.max_parallel = max_parallel

    def _run_single(self, issue_id: int, repo: str, model_preference: Optional[str] = None) -> WorkerResult:
        key = self.key_pool.get_next_available(model_preference=model_preference)
        if not key:
            return WorkerResult(ok=False, output="No API keys available for this model/tier")
        
        config = WorkerConfig(
            issue_id=issue_id,
            api_token=key.gitea_token,
            gemini_api_key=key.gemini_key,
            model=key.model,
            repo=repo
        )
        
        print(f"Dispatching Issue #{issue_id} using key '{key.label}' ({key.model})...")
        result = self.worker_impl.run(config)
        
        # Heuristic for rate limit detection
        if not result.ok and ("429" in result.output or "rate limit" in result.output.lower()):
            self.key_pool.mark_rate_limited(key.label)
            
        return result

    def dispatch(self, issue_ids: List[int], repo: str, model_preference: Optional[str] = None) -> List[WorkerResult]:
        """Dispatch work for multiple issues in parallel using ThreadPoolExecutor."""
        results = []
        limit = min(len(issue_ids), self.max_parallel)
        targets = issue_ids[:limit]
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
            future_to_issue = {executor.submit(self._run_single, iid, repo, model_preference): iid for iid in targets}
            for future in concurrent.futures.as_completed(future_to_issue):
                issue_id = future_to_issue[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    print(f"Issue #{issue_id} generated an exception: {exc}")
                    results.append(WorkerResult(ok=False, output=str(exc)))
            
        return results
