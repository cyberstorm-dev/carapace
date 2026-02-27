import subprocess
import json
import tempfile
import os
from .base import Worker, WorkerConfig, WorkerResult


class ContainerWorker(Worker):
    def __init__(self, image: str = "openclaw/nisto-worker:latest"):
        self.image = image

    def run(self, config: WorkerConfig) -> WorkerResult:
        print(f"🚀 Spawning ContainerWorker for issue #{config.issue_id} using model {config.model}...")
        
        # We will inject the configuration via environment variables
        env_vars = {
            "GITEA_TOKEN": config.api_token,
            "GEMINI_API_KEY": config.gemini_api_key or "",
            "ISSUE_NUMBER": str(config.issue_id),
            "TARGET_REPO": config.repo,
            "MODEL_ID": config.model,
        }
        
        docker_cmd = ["docker", "run", "--rm"]
        for k, v in env_vars.items():
            docker_cmd.extend(["-e", f"{k}={v}"])
            
        docker_cmd.append(self.image)
        # Assuming the container entrypoint handles the actual pulling of the issue
        # and execution of the builder agent.
        
        try:
            # Run the container synchronously for this worker thread
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                check=False
            )
            
            ok = result.returncode == 0
            
            # Parse the structured metrics block
            prompt_tokens = 0
            comp_tokens = 0
            tools = {}
            
            try:
                if "--- METRICS_START ---" in result.stdout and "--- METRICS_END ---" in result.stdout:
                    metrics_str = result.stdout.split("--- METRICS_START ---")[1].split("--- METRICS_END ---")[0]
                    metrics = json.loads(metrics_str)
                    prompt_tokens = metrics.get("tokens", {}).get("prompt", 0)
                    comp_tokens = metrics.get("tokens", {}).get("completion", 0)
                    tools = metrics.get("tool_calls", {})
            except Exception as e:
                print(f"Warning: Failed to parse container metrics: {e}")
            
            return WorkerResult(
                ok=ok,
                output=result.stdout[-1000:], # Return last 1000 chars of logs
                tokens_prompt=prompt_tokens,
                tokens_completion=comp_tokens,
                tool_calls=tools
            )
            
        except FileNotFoundError:
            return WorkerResult(
                ok=False, 
                output="Docker executable not found. Is Docker installed and running?"
            )
        except Exception as e:
            return WorkerResult(
                ok=False,
                output=f"Container execution failed: {str(e)}"
            )
