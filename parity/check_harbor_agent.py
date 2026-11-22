"""Check that the original side runs Claude Code exactly as Harbor's claude-code agent does.

Run with Harbor's Python, before any run that spends API budget:

    "$(uv tool dir)/harbor/bin/python" parity/check_harbor_agent.py \
        --run-agent autotavern/tavernbench/tavernbench/run_agent.py \
        --tasks datasets/tavernbench >> "$GITHUB_OUTPUT"

The agent is built the way `harbor run -a claude-code -m <provider>/<model> --ak ...`
builds it, for the settings in parity/config.env. Its run() goes to a stand-in
environment that records every `exec` call Harbor would send to the task container (the
bash script, the environment, the user and the working directory). These calls are
compared with what tavernbench.run_agent sends with `docker exec`, under every
credential setup (API key or auth token, default or custom endpoint), with dummy
credentials and a clean environment. With --tasks, the instruction Harbor gives each task
is also compared with the text run_agent reads.

Prints agent_flags, agent_model and harbor_agent_args (for $GITHUB_OUTPUT) and exits 0
when everything matches; prints the differences to stderr and exits 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan  # noqa: E402

from harbor.agents.factory import AgentFactory  # noqa: E402
from harbor.cli.utils import parse_kwargs  # noqa: E402
from harbor.models.agent.context import AgentContext  # noqa: E402
from harbor.models.task.task import Task  # noqa: E402
from harbor.models.trial.config import AgentConfig  # noqa: E402

INSTRUCTION = 'Line one with "quotes", $dollar, `ticks`, \\backslash and {braces}.\nLine two.\n'
DUMMY_KEY = "sk-parity-check-dummy-key-0123456789"
DUMMY_URL = "https://gateway.parity-check.invalid"
SCENARIOS = {
    "API key, default endpoint": {"ANTHROPIC_API_KEY": DUMMY_KEY},
    "API key, custom endpoint": {"ANTHROPIC_API_KEY": DUMMY_KEY, "ANTHROPIC_BASE_URL": DUMMY_URL},
    "auth token, default endpoint": {"ANTHROPIC_AUTH_TOKEN": DUMMY_KEY},
    "auth token, custom endpoint": {
        "ANTHROPIC_AUTH_TOKEN": DUMMY_KEY,
        "ANTHROPIC_BASE_URL": DUMMY_URL,
    },
}
# Everything else is removed from the environment while a scenario runs, as the workflow
# removes ANTHROPIC_*, CLAUDE_* and AWS_* variables before it starts either side.
KEEP_ENV = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR")
PLACEHOLDER_MODEL = "claude-model-placeholder"
NORMALIZED_VAR = "CLAUDE_CODE_INSTRUCTION_X"


class RecordingEnvironment:
    """Stands in for the task container and records what the agent runs in it."""

    default_user = None

    def __init__(self) -> None:
        self.calls: list[SimpleNamespace] = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.calls.append(SimpleNamespace(command=command, cwd=cwd, env=dict(env or {}), user=user))
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    def __getattr__(self, name):
        raise AttributeError(f"the agent used environment.{name}, which this check does not model")


def load_run_agent(path: Path):
    spec = importlib.util.spec_from_file_location("run_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def agent_kwargs(cfg: dict[str, str]) -> list[str]:
    """The --ak options of the Harbor side, in the order the workflow passes them."""
    kwargs = [f"version={cfg['CLAUDE_CODE_VERSION'] or '0.0.0'}", "disable_web_search=true"]
    if cfg["REASONING_EFFORT"]:
        kwargs.append(f"reasoning_effort={cfg['REASONING_EFFORT']}")
    return kwargs


def set_clean_environment(values: dict[str, str]) -> None:
    kept = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    os.environ.clear()
    os.environ.update(kept)
    os.environ.update(values)


def normalize(command: str, env: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Give the random instruction variable one fixed name, in the script and the env."""
    for name in [k for k in env if k.startswith("CLAUDE_CODE_INSTRUCTION_")]:
        command = command.replace(name, NORMALIZED_VAR).replace(name.lower(), NORMALIZED_VAR.lower())
        env = {(NORMALIZED_VAR if k == name else k): v for k, v in env.items()}
    return command, env


def describe_env_diff(harbor: dict[str, str], ours: dict[str, str]) -> str:
    def shown(value: str) -> str:
        return "<dummy credential>" if value == DUMMY_KEY else repr(value)

    parts = []
    for key in sorted(harbor.keys() | ours.keys()):
        if harbor.get(key) != ours.get(key):
            parts.append(
                f"{key}: harbor={shown(harbor[key]) if key in harbor else 'unset'}, "
                f"run_agent={shown(ours[key]) if key in ours else 'unset'}"
            )
    return "; ".join(parts)


def check_scenario(ra, model_name: str, kwargs: dict, values: dict[str, str]):
    """Return (problems, flags, model) for one credential setup."""
    set_clean_environment(values)
    config = AgentConfig(name="claude-code", model_name=model_name, kwargs=kwargs)
    agent = AgentFactory.create_agent_from_config(config, logs_dir=Path(tempfile.mkdtemp()))
    environment = RecordingEnvironment()
    asyncio.run(agent.run(INSTRUCTION, environment, AgentContext()))

    flags = agent.build_cli_flags()
    model = agent._resolved_model_name()
    problems = []
    if len(environment.calls) != 2:
        return [f"expected 2 exec calls (setup, launch), Harbor made {len(environment.calls)}"], flags, model

    setup, launch = environment.calls
    launch_command, launch_env = normalize(launch.command, launch.env)
    our_setup, our_launch = ra.exec_commands(flags, NORMALIZED_VAR)
    our_env = ra.container_env(model)

    if setup.command != our_setup:
        problems.append(f"setup script differs\n  harbor:    {setup.command}\n  run_agent: {our_setup}")
    if launch_command != our_launch:
        problems.append(
            f"launch script differs\n  harbor:    {launch_command}\n  run_agent: {our_launch}"
        )
    if setup.env != our_env:
        problems.append("setup environment differs: " + describe_env_diff(setup.env, our_env))
    if launch_env != {**our_env, NORMALIZED_VAR: INSTRUCTION}:
        problems.append(
            "launch environment differs: "
            + describe_env_diff(launch_env, {**our_env, NORMALIZED_VAR: INSTRUCTION})
        )
    for call in (setup, launch):
        # run_agent uses the image's default user and its WORKDIR (/app), as Harbor does
        # when neither is given.
        if call.user is not None or call.cwd is not None:
            problems.append(f"Harbor set user={call.user!r} cwd={call.cwd!r}; run_agent sets neither")
    return problems, flags, model


def check_instructions(ra, tasks: Path) -> list[str]:
    problems = []
    task_dirs = sorted(p for p in tasks.iterdir() if (p / "task.toml").is_file())
    for task_dir in task_dirs:
        harbor_text = Task(task_dir).instruction
        our_text = ra.read_instruction(tasks, task_dir.name)
        if harbor_text != our_text:
            problems.append(f"{task_dir.name}: Harbor's instruction differs from instruction.md")
    print(f"instructions: {len(task_dirs) - len(problems)}/{len(task_dirs)} identical", file=sys.stderr)
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-agent", type=Path, required=True, help="path of run_agent.py")
    parser.add_argument("--tasks", type=Path, help="generated Harbor task directory")
    args = parser.parse_args()

    cfg = plan.read_config()
    ra = load_run_agent(args.run_agent)
    ak = agent_kwargs(cfg)
    kwargs = parse_kwargs(ak)
    model_name = f"{cfg['MODEL_PROVIDER']}/{cfg['MODEL'] or PLACEHOLDER_MODEL}"
    print(f"Harbor agent: claude-code -m {model_name} --ak {' --ak '.join(ak)}", file=sys.stderr)

    saved = dict(os.environ)
    problems: list[str] = []
    results = set()
    try:
        for name, values in SCENARIOS.items():
            found, flags, model = check_scenario(ra, model_name, kwargs, values)
            results.add((flags, model))
            print(f"{name}: {'same' if not found else 'DIFFERENT'}", file=sys.stderr)
            problems += [f"[{name}] {p}" for p in found]
    finally:
        os.environ.clear()
        os.environ.update(saved)
    if len(results) != 1:
        problems.append(f"flags or model depend on the credential setup: {sorted(results)}")
    if args.tasks:
        problems += check_instructions(ra, args.tasks)

    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        sys.exit(1)

    ((flags, model),) = results
    print(f"flags: {flags}", file=sys.stderr)
    print(f"model: {model}", file=sys.stderr)
    print(f"agent_flags={flags}")
    print(f"agent_model={model}")
    print(f"harbor_agent_args={' '.join(f'--ak {a}' for a in ak)}")


if __name__ == "__main__":
    main()
