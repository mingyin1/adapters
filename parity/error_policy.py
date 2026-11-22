"""Apply Harbor's error policy to the original side's answers before they are scored.

    "$(uv tool dir)/harbor/bin/python" parity/error_policy.py \
        --run-agent autotavern/tavernbench/tavernbench/run_agent.py \
        --answers out/original/run1/shard1/answers.jsonl \
        --out out/original/run1/shard1/graded_answers.jsonl

Harbor grades an agent that timed out or exited with an error on the answer it left,
except when the run ended in a model-API error (rate limit, overload, lost connection,
failed authentication and so on): that trial gets no valid reward and counts as 0. To
count the same runs as 0 on the original side, this script classifies each original run
the way Harbor classifies a trial: a timeout is AgentTimeoutError, a non-zero exit of
the launch command goes through the claude-code agent's own `_classify_exec_error` with
the run's Claude Code output (logs/<id>/claude-code.txt, the stdout Harbor would see),
and Harbor's policy table says whether the reward stays valid. Rows whose reward Harbor
would not keep get an `error` field, which the unchanged upstream scorer scores as 0.
Every row gets `harbor_exception`, `harbor_graded` (false after a setup or configuration
error, such as a rejected API key) and `harbor_reward_valid`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.models.trial.error_policy import policy_for


def load_run_agent(path: Path):
    spec = importlib.util.spec_from_file_location("run_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify(row: dict, agent: ClaudeCode, launch_script: str, logs: Path) -> str | None:
    if row.get("runner_error"):
        return "RunnerError"  # the container never ran; there is no answer either way
    if row.get("agent_timed_out"):
        return "AgentTimeoutError"
    code = row.get("agent_exit_code")
    if code in (0, None):
        return None
    log = logs / row["id"] / "claude-code.txt"
    stdout = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    result = SimpleNamespace(return_code=code, stdout=stdout, stderr="")
    return type(agent._classify_exec_error(launch_script, result)).__name__


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-agent", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--logs", type=Path, help="default: logs/ next to --answers")
    args = parser.parse_args()

    ra = load_run_agent(args.run_agent)
    _, launch_script = ra.exec_commands(ra.DEFAULT_CLI_FLAGS, "CLAUDE_CODE_INSTRUCTION_X")
    agent = ClaudeCode(logs_dir=Path(tempfile.mkdtemp()), model_name="anthropic/model")
    logs = args.logs or args.answers.parent / "logs"

    counts: Counter = Counter()
    rows = []
    if args.answers.is_file():
        rows = [json.loads(line) for line in args.answers.read_text(encoding="utf-8").splitlines() if line.strip()]
    with open(args.out, "w", encoding="utf-8") as out:
        for row in rows:
            exception = classify(row, agent, launch_script, logs)
            policy = policy_for(exception) if exception not in (None, "RunnerError") else None
            valid = exception is None or (policy is not None and policy.has_valid_reward)
            row["harbor_exception"] = exception
            # Harbor does not grade a trial after a setup or configuration error.
            row["harbor_graded"] = exception is None or (policy is not None and policy.should_grade)
            row["harbor_reward_valid"] = valid
            if not valid and not row.get("runner_error"):
                row["error"] = f"{exception}: Harbor gives this run no valid reward"
            counts[f"{exception or 'none'} ({'valid' if valid else 'not valid'})"] += 1
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(rows)} rows: " + (", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none"))


if __name__ == "__main__":
    main()
