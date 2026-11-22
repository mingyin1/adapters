"""Read parity/config.env, plan a parity run and pick each job's tasks.

    python3 parity/plan.py env                 # every setting, checked, for $GITHUB_ENV
    python3 parity/plan.py outputs [--results-ref REF]
                                               # enabled, matrix, sides, label for $GITHUB_OUTPUT
    python3 parity/plan.py select --tasks DIR --shard K --copy-to DIR --ids-out FILE
                                               # the tasks of one shard, the same on both sides
    python3 parity/plan.py meta --side S --run N --shard K --ids FILE --tasks DIR --out DIR \
        [--sha NAME=SHA ...] [--status STATUS]  # meta.json of one job's results

`outputs` reads HAS_KEY and HAS_DAYTONA ("true"/"false") from the environment to know
which repository secrets exist. With --results-ref (the parity-results branch) it also
refuses to run when results for the same label and run already exist, or when the label
already holds runs made with other settings, so no API budget is spent on a run that
could not be published. Every value is checked against a strict pattern, because the
values reach shell commands.

DRY_RUN=true runs the whole pipeline with a dummy API key instead of the secrets: every
agent stops at its first request (authentication fails), every task scores 0, and the
results go to dryrun/<LABEL>/ instead of runs/<LABEL>/. It tests the workflow without
spending API budget.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.env"
PILOT_IDS = HERE / "pilot_ids.txt"

PATTERNS = {
    "ENABLED": r"true|false",
    "DRY_RUN": r"true|false",
    "LABEL": r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}",
    "TASKS": r"pilot|all",
    "RUNS": r"[1-9][0-9]?(,[1-9][0-9]?)*",
    "SIDES": r"both|harbor|original",
    "SHARDS": r"[1-9]",
    "MODEL": r"|[A-Za-z0-9][A-Za-z0-9._:@\[\]-]{0,127}",
    "MODEL_PROVIDER": r"anthropic",
    "CLAUDE_CODE_VERSION": r"|[0-9]+\.[0-9]+\.[0-9]+",
    "REASONING_EFFORT": r"|low|medium|high|xhigh|max",
    "AUTH_VAR": r"ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN",
    "N_CONCURRENT": r"[1-9][0-9]?",
    "HARBOR_VERSION": r"[0-9]+\.[0-9]+\.[0-9]+",
    "HARBOR_ENV": r"docker|daytona",
    "ADAPTER_REF": r"[A-Za-z0-9._/-]{1,100}",
    "AUTOTAVERN_REPO": r"[A-Za-z0-9_.-]{1,39}/[A-Za-z0-9_.-]{1,100}",
    "AUTOTAVERN_REF": r"[A-Za-z0-9._/-]{1,100}",
}
REQUIRED_WHEN_ENABLED = ("MODEL", "CLAUDE_CODE_VERSION")
# Settings that change what the agent does. Runs compared under one label must share them.
FINGERPRINT_KEYS = (
    "TASKS",
    "MODEL_PROVIDER",
    "MODEL",
    "CLAUDE_CODE_VERSION",
    "REASONING_EFFORT",
    "HARBOR_VERSION",
    "HARBOR_ENV",
)


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def read_config(path: Path = CONFIG) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            fail(f"config.env line {number}: expected KEY=VALUE, got {raw.strip()!r}")
        key, value = (part.strip() for part in line.split("=", 1))
        if key not in PATTERNS:
            fail(f"config.env line {number}: unknown setting {key}")
        if key in values:
            fail(f"config.env line {number}: {key} is set twice")
        values[key] = value
    missing = sorted(set(PATTERNS) - set(values))
    if missing:
        fail(f"config.env is missing: {', '.join(missing)}")
    for key, value in values.items():
        if not re.fullmatch(PATTERNS[key], value):
            fail(f"config.env: {key}={value!r} is not allowed (expected {PATTERNS[key]})")
    return values


def fingerprint(cfg: dict[str, str]) -> dict[str, str]:
    return {key: cfg[key] for key in FINGERPRINT_KEYS}


def runs_of(cfg: dict[str, str]) -> list[int]:
    return sorted({int(r) for r in cfg["RUNS"].split(",")})


def sides_of(cfg: dict[str, str]) -> list[str]:
    return ["original", "harbor"] if cfg["SIDES"] == "both" else [cfg["SIDES"]]


def git_show(ref: str, path: str) -> str | None:
    out = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def check_results(cfg: dict[str, str], ref: str) -> None:
    """Refuse runs that could not be published next to the results already on `ref`."""
    if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], capture_output=True).returncode:
        fail(f"{ref} not found; create the parity-results branch first (see parity/README.md)")
    label = cfg["LABEL"]
    stored = git_show(ref, f"runs/{label}/fingerprint.json")
    if stored is not None and json.loads(stored) != fingerprint(cfg):
        fail(
            f"label {label!r} already holds runs made with other settings "
            f"({stored.strip()}); use a new LABEL for these settings"
        )
    for side in sides_of(cfg):
        for run in runs_of(cfg):
            path = f"runs/{label}/{side}/run{run}"
            listing = subprocess.run(
                ["git", "ls-tree", "--name-only", ref, f"{path}/"], capture_output=True, text=True
            )
            if listing.stdout.strip():
                fail(f"{path} already exists on {ref}; choose other RUNS or another LABEL")


def cmd_env(cfg: dict[str, str], _args: argparse.Namespace) -> None:
    for key, value in cfg.items():
        print(f"{key}={value}")


def results_dir(cfg: dict[str, str]) -> str:
    return f"{'dryrun' if cfg['DRY_RUN'] == 'true' else 'runs'}/{cfg['LABEL']}"


def cmd_outputs(cfg: dict[str, str], args: argparse.Namespace) -> None:
    enabled = cfg["ENABLED"] == "true"
    dry_run = cfg["DRY_RUN"] == "true"
    if enabled:
        empty = [key for key in REQUIRED_WHEN_ENABLED if not cfg[key]]
        if empty:
            fail(f"config.env: set {', '.join(empty)} before enabling a run")
        if not dry_run and os.environ.get("HAS_KEY") != "true":
            fail("add the TB_API_KEY repository secret before enabling a run")
        if cfg["HARBOR_ENV"] == "daytona" and os.environ.get("HAS_DAYTONA") != "true":
            fail("HARBOR_ENV=daytona needs the DAYTONA_API_KEY repository secret")
        if cfg["TASKS"] == "pilot" and cfg["SHARDS"] != "1":
            fail("use SHARDS=1 for the pilot")
        if args.results_ref and not dry_run:
            check_results(cfg, args.results_ref)

    shards = int(cfg["SHARDS"])
    include = [{"run": r, "shard": s} for r in runs_of(cfg) for s in range(1, shards + 1)]
    sides = sides_of(cfg)
    print(f"enabled={'true' if enabled else 'false'}")
    print(f"dry_run={'true' if dry_run else 'false'}")
    print(f"matrix={json.dumps({'include': include}, separators=(',', ':'))}")
    print(f"harbor={'true' if 'harbor' in sides else 'false'}")
    print(f"original={'true' if 'original' in sides else 'false'}")
    print(f"label={cfg['LABEL']}")
    print(f"results_dir={results_dir(cfg)}")


def all_ids(tasks: Path) -> list[str]:
    ids = sorted(p.name.upper() for p in tasks.iterdir() if (p / "task.toml").is_file())
    if not ids:
        fail(f"no tasks found in {tasks}")
    return ids


def selected_ids(cfg: dict[str, str], tasks: Path) -> list[str]:
    ids = all_ids(tasks)
    if cfg["TASKS"] == "all":
        return ids
    wanted = [line.strip().upper() for line in PILOT_IDS.read_text().splitlines()]
    wanted = [w for w in wanted if w and not w.startswith("#")]
    unknown = sorted(set(wanted) - set(ids))
    if unknown:
        fail(f"pilot ids not in the generated tasks: {', '.join(unknown)}")
    return sorted(set(wanted))


def cmd_select(cfg: dict[str, str], args: argparse.Namespace) -> None:
    shards = int(cfg["SHARDS"])
    if not 1 <= args.shard <= shards:
        fail(f"shard {args.shard} is outside 1..{shards}")
    ids = selected_ids(cfg, args.tasks)[args.shard - 1 :: shards]
    if args.copy_to.exists():
        shutil.rmtree(args.copy_to)
    args.copy_to.mkdir(parents=True)
    for task_id in ids:
        shutil.copytree(args.tasks / task_id.lower(), args.copy_to / task_id.lower())
    args.ids_out.write_text("".join(f"{i}\n" for i in ids), encoding="utf-8")
    print(f"shard {args.shard}/{shards}: {len(ids)} tasks", file=sys.stderr)


def cmd_meta(cfg: dict[str, str], args: argparse.Namespace) -> None:
    ids = [line.strip() for line in args.ids.read_text(encoding="utf-8").splitlines() if line.strip()]
    tasks = {}
    for task_id in ids:
        toml = tomllib.loads((args.tasks / task_id.lower() / "task.toml").read_text(encoding="utf-8"))
        metadata = toml.get("metadata", {})
        tasks[task_id] = {
            "scenario": metadata.get("scenario"),
            "category": metadata.get("tavernbench_category"),
            "scoring": metadata.get("scoring"),
        }
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo, run_id = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    meta = {
        "side": args.side,
        "run": args.run,
        "shard": args.shard,
        "shards": int(cfg["SHARDS"]),
        "label": cfg["LABEL"],
        "dry_run": cfg["DRY_RUN"] == "true",
        "fingerprint": fingerprint(cfg),
        "config": cfg,
        "shas": dict(item.split("=", 1) for item in args.sha),
        "job_status": args.status,
        "workflow_run": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ids": ids,
        "tasks": tasks,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("env")
    outputs = sub.add_parser("outputs")
    outputs.add_argument("--results-ref", default="")
    select = sub.add_parser("select")
    select.add_argument("--tasks", type=Path, required=True)
    select.add_argument("--shard", type=int, required=True)
    select.add_argument("--copy-to", type=Path, required=True)
    select.add_argument("--ids-out", type=Path, required=True)
    meta = sub.add_parser("meta")
    meta.add_argument("--side", choices=("harbor", "original"), required=True)
    meta.add_argument("--run", type=int, required=True)
    meta.add_argument("--shard", type=int, required=True)
    meta.add_argument("--ids", type=Path, required=True)
    meta.add_argument("--tasks", type=Path, required=True)
    meta.add_argument("--out", type=Path, required=True)
    meta.add_argument("--sha", action="append", default=[], help="NAME=SHA, repeatable")
    meta.add_argument("--status", default="unknown", help="outcome of the agent step")
    args = parser.parse_args()
    cfg = read_config()
    commands = {"env": cmd_env, "outputs": cmd_outputs, "select": cmd_select, "meta": cmd_meta}
    commands[args.command](cfg, args)


if __name__ == "__main__":
    main()
