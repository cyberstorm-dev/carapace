from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class WorkerConfig:
    issue_id: int
    api_token: str
    model: str
    gemini_api_key: Optional[str] = None
    repo: str = "openclaw/nisto-home"


@dataclass
class WorkerResult:
    ok: bool
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tool_calls: Dict[str, int] = None
    output: str = ""


class Worker(ABC):
    @abstractmethod
    def run(self, config: WorkerConfig) -> WorkerResult:
        """Execute a single unit of work (one issue) in an isolated environment."""
        pass
