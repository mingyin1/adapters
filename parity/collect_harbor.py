"""Collect one Harbor job into trials.jsonl, one row per trial, for the parity summary.

    "$(uv tool dir)/harbor/bin/python" parity/collect_harbor.py \
        --job jobs/harbor-run1-shard1 --out out/harbor/run1/shard1 [--keep-logs]

Each trial's result.json is read with Harbor's own TrialResult model, so `reward_valid`
and the exception policies are Harbor's. A trial whose reward is not valid counts as 0,
as in Harbor's mean. The job's result.json and config.json are copied next to
trials.jsonl. With --keep-logs, each trial's agent log, verifier output, trial log and
result go to logs/<task id>/ (Claude Code's session files are left out).
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from harbor.models.trial.result import TrialResult


def last_result_event(log: Path) -> dict:
    """The final `result` event of Claude Code's stream-json output, if any."""
    found: dict = {}
    if log.is_file():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("{") and '"type":"result"' in line.replace(" ", ""):
                try:
                    found = json.loads(line)
                except json.JSONDecodeError:
                    pass
    return found


def seconds(timing) -> float | None:
    if timing is None or timing.started_at is None or timing.finished_at is None:
        return None
    return round((timing.finished_at - timing.started_at).total_seconds(), 2)


def row_for(trial_dir: Path) -> dict:
    trial = TrialResult.model_validate_json((trial_dir / "result.json").read_text(encoding="utf-8"))
    rewards = trial.verifier_result.rewards if trial.verifier_result else None
    n_input, n_cache, n_output, cost = trial.compute_token_cost_totals()
    details_path = trial_dir / "verifier" / "score.json"
    details = json.loads(details_path.read_text(encoding="utf-8")) if details_path.is_file() else {}
    result = last_result_event(trial_dir / "agent" / "claude-code.txt")
    model_info = trial.agent_info.model_info
    return {
        "id": trial.trial_name.split("__", 1)[0].upper(),
        "trial_name": trial.trial_name,
        "reward": (rewards or {}).get("reward"),
        "reward_valid": trial.reward_valid,
        "exceptions": [
            {
                "type": e.exception_type,
                "graded": e.policy.should_grade,
                "valid_reward": e.policy.has_valid_reward,
                "message": e.exception_message[:300],
            }
            for e in trial.recorded_exceptions()
        ],
        "verifier_status": details.get("status"),
        "verifier_answer": details.get("answer"),
        "agent_version": trial.agent_info.version,
        "model": model_info.name if model_info else None,
        "agent_result_subtype": result.get("subtype"),
        "agent_is_error": result.get("is_error"),
        "num_turns": result.get("num_turns"),
        "n_input_tokens": n_input,
        "n_cache_tokens": n_cache,
        "n_output_tokens": n_output,
        "cost_usd": cost,
        "agent_seconds": seconds(trial.agent_execution),
        "started_at": trial.started_at.isoformat() if trial.started_at else None,
        "finished_at": trial.finished_at.isoformat() if trial.finished_at else None,
    }


def keep_logs(trial_dir: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("result.json", "trial.log", "exception.txt"):
        if (trial_dir / name).is_file():
            shutil.copy2(trial_dir / name, dest / name)
    if (trial_dir / "agent" / "claude-code.txt").is_file():
        shutil.copy2(trial_dir / "agent" / "claude-code.txt", dest / "claude-code.txt")
    if (trial_dir / "verifier").is_dir():
        shutil.copytree(trial_dir / "verifier", dest / "verifier", dirs_exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", type=Path, required=True, help="Harbor job directory")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--keep-logs", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    trial_dirs = sorted(p.parent for p in args.job.glob("*/result.json")) if args.job.is_dir() else []
    rows = []
    for trial_dir in trial_dirs:
        try:
            rows.append(row_for(trial_dir))
        except Exception as exc:  # a broken result file must not hide the other trials
            rows.append({"id": trial_dir.name.split("__", 1)[0].upper(), "trial_name": trial_dir.name,
                         "reward": None, "reward_valid": False,
                         "collect_error": f"{type(exc).__name__}: {exc}"[:300]})
        if args.keep_logs:
            keep_logs(trial_dir, args.out / "logs" / rows[-1]["id"])
    with open(args.out / "trials.jsonl", "w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    for name, target in (("result.json", "job_result.json"), ("config.json", "job_config.json")):
        if (args.job / name).is_file():
            shutil.copy2(args.job / name, args.out / target)
    valid = sum(1 for r in rows if r.get("reward_valid"))
    correct = sum(1 for r in rows if r.get("reward_valid") and r.get("reward") == 1)
    print(f"{len(rows)} trials, {valid} with a valid reward, {correct} correct")


if __name__ == "__main__":
    main()
