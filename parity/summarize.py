"""Summarize the parity results under one label.

    python3 parity/summarize.py <parity-results checkout>/runs/<LABEL>

Reads <side>/run<N>/shard<K>/ for both sides and writes summary.md and summary.json
into the label directory. For each run and side: accuracy over the run's tasks (a task
without a result, or whose Harbor reward is not valid, counts as 0, as in Harbor's
mean), errors and cost. For each side: mean ± sample SEM over the complete runs, where
SEM = sqrt(sum((x - mean)^2) / (n (n - 1))), and whether the two sides' run-score ranges
overlap. Per task: how many runs each side got right.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

SIDES = ("original", "harbor")
SIDE_NAMES = {
    "original": "Original (AutoTavern `run_agent.py` + upstream scorer)",
    "harbor": "Harbor (adapter + `claude-code` agent)",
}
MAX_LISTED = 40


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def last_by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["id"]: row for row in rows}


def load_harbor(shard: Path) -> tuple[dict, Counter, float | None]:
    outcome, errors, cost = {}, Counter(), None
    for task_id, row in last_by_id(read_jsonl(shard / "trials.jsonl")).items():
        valid = bool(row.get("reward_valid"))
        outcome[task_id] = float(row.get("reward") or 0) if valid else 0.0
        types = [e["type"] for e in row.get("exceptions", [])]
        for t in types:
            errors[f"exception: {t}"] += 1
        if "AgentTimeoutError" in types:
            errors["agent timed out"] += 1
        if row.get("agent_is_error"):
            errors["agent ended with an error result"] += 1
        if not valid:
            errors["reward not valid (counted as 0)"] += 1
        if row.get("verifier_status") == "answer file not found":
            errors["no answer file"] += 1
        if row.get("collect_error"):
            errors["unreadable trial result"] += 1
        if row.get("cost_usd") is not None:
            cost = (cost or 0.0) + row["cost_usd"]
    return outcome, errors, cost


def load_original(shard: Path) -> tuple[dict, Counter, float | None]:
    """graded_answers.jsonl is answers.jsonl with Harbor's error policy applied
    (parity/error_policy.py); score.json is the upstream scorer's output for it."""
    outcome, errors, cost = {}, Counter(), None
    score_path = shard / "score.json"
    scored = {}
    if score_path.is_file() and score_path.stat().st_size:
        scored = {i["id"]: i["correct"] for i in json.loads(score_path.read_text())["items"]}
    rows = read_jsonl(shard / "graded_answers.jsonl") or read_jsonl(shard / "answers.jsonl")
    for task_id, row in last_by_id(rows).items():
        outcome[task_id] = float(scored.get(task_id, 0))
        if row.get("runner_error"):
            errors["container could not be run"] += 1
            continue
        if row.get("harbor_exception"):
            errors[f"exception: {row['harbor_exception']}"] += 1
        if row.get("agent_timed_out"):
            errors["agent timed out"] += 1
        if row.get("agent_is_error"):
            errors["agent ended with an error result"] += 1
        if row.get("harbor_reward_valid") is False:
            errors["reward not valid (counted as 0)"] += 1
        if row.get("response") is None:
            errors["no answer file"] += 1
        if row.get("cost_usd") is not None:
            cost = (cost or 0.0) + row["cost_usd"]
    return outcome, errors, cost


def load_run(side: str, run_dir: Path) -> dict:
    shards = sorted(p for p in run_dir.glob("shard*") if (p / "meta.json").is_file())
    metas = [json.loads((p / "meta.json").read_text()) for p in shards]
    ids: list[str] = sorted({i for m in metas for i in m["ids"]})
    outcome, errors, cost = {}, Counter(), None
    for shard in shards:
        o, e, c = (load_harbor if side == "harbor" else load_original)(shard)
        outcome.update(o)
        errors.update(e)
        if c is not None:
            cost = (cost or 0.0) + c
    missing = [i for i in ids if i not in outcome]
    if missing:
        errors["no result (counted as 0)"] += len(missing)
    expected = metas[0]["shards"] if metas else 0
    statuses = sorted({m.get("job_status", "unknown") for m in metas})
    correct = sum(outcome.get(i, 0.0) for i in ids)
    return {
        "run": int(run_dir.name[3:]),
        "complete": bool(metas) and len(shards) == expected and statuses == ["success"],
        "shards": f"{len(shards)}/{expected}",
        "job_status": statuses,
        "n": len(ids),
        "correct": correct,
        "accuracy": correct / len(ids) if ids else None,
        "outcome": {i: outcome.get(i, 0.0) for i in ids},
        "errors": dict(sorted(errors.items())),
        "cost_usd": round(cost, 4) if cost is not None else None,
        "workflow_runs": sorted({m["workflow_run"] for m in metas if m.get("workflow_run")}),
        "shas": {k: sorted({m["shas"][k] for m in metas if k in m.get("shas", {})}) for k in
                 sorted({k for m in metas for k in m.get("shas", {})})},
        "tasks": {k: v for m in metas for k, v in m.get("tasks", {}).items()},
        "fingerprint": metas[0]["fingerprint"] if metas else None,
        "dry_run": any(m.get("dry_run") for m in metas),
    }


def stats(values: list[float]) -> dict:
    n = len(values)
    if not n:
        return {"n": 0}
    mean = sum(values) / n
    sem = math.sqrt(sum((x - mean) ** 2 for x in values) / (n * (n - 1))) if n > 1 else None
    return {"n": n, "mean": mean, "sem": sem, "min": min(values), "max": max(values)}


def pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.1f}"


def summarize(label_dir: Path) -> tuple[dict, str]:
    sides = {}
    for side in SIDES:
        runs = [load_run(side, d) for d in sorted((label_dir / side).glob("run*"), key=lambda p: int(p.name[3:]))]
        complete = [r for r in runs if r["complete"]]
        sides[side] = {"runs": runs, "stats": stats([r["accuracy"] for r in complete])}

    o, h = sides["original"]["stats"], sides["harbor"]["stats"]
    overlap = None
    if o["n"] and h["n"]:
        overlap = max(o["min"], h["min"]) <= min(o["max"], h["max"])

    # Per task: share of complete runs each side got right.
    per_task: dict[str, dict] = defaultdict(dict)
    task_info: dict[str, dict] = {}
    for side in SIDES:
        for run in sides[side]["runs"]:
            task_info.update(run["tasks"])
            if run["complete"]:
                for task_id, value in run["outcome"].items():
                    per_task[task_id].setdefault(side, []).append(value)
    both = [t for t in sorted(per_task) if len(per_task[t]) == 2]
    disagree = [t for t in both
                if sum(per_task[t]["original"]) / len(per_task[t]["original"])
                != sum(per_task[t]["harbor"]) / len(per_task[t]["harbor"])]

    by_scenario: dict[str, dict] = defaultdict(lambda: {s: [] for s in SIDES})
    for task_id, values in per_task.items():
        scenario = (task_info.get(task_id) or {}).get("scenario") or task_id.split("-")[0]
        for side, outcomes in values.items():
            by_scenario[scenario][side].extend(outcomes)

    fingerprints = {json.dumps(r["fingerprint"], sort_keys=True)
                    for s in SIDES for r in sides[s]["runs"] if r["fingerprint"]}
    fp = json.loads(next(iter(fingerprints))) if len(fingerprints) == 1 else None
    data = {
        "label": label_dir.name,
        "dry_run": any(r["dry_run"] for s in SIDES for r in sides[s]["runs"]),
        "settings": fp,
        "settings_consistent": len(fingerprints) <= 1,
        "sides": {s: {"stats": sides[s]["stats"],
                      "runs": [{k: v for k, v in r.items() if k not in ("outcome", "tasks")}
                               for r in sides[s]["runs"]]} for s in SIDES},
        "range_overlap": overlap,
        "per_task": {t: per_task[t] for t in sorted(per_task)},
        "disagreements": disagree,
        "by_scenario": {k: {s: stats(v[s]) for s in SIDES} for k, v in sorted(by_scenario.items())},
    }
    return data, render(data, sides, task_info)


def render(data: dict, sides: dict, task_info: dict) -> str:
    out = [f"# TavernBench parity: `{data['label']}`", ""]
    if data["dry_run"]:
        out += ["> **Dry run.** Both sides used a dummy API key, so every agent stopped at its",
                "> first request and every task scores 0. This tests the workflow, not the adapter.", ""]
    fp = data["settings"]
    if fp:
        out += [f"Model `{fp['MODEL_PROVIDER']}/{fp['MODEL']}` · Claude Code `{fp['CLAUDE_CODE_VERSION']}` · "
                f"reasoning effort `{fp['REASONING_EFFORT'] or 'default'}` · Harbor `{fp['HARBOR_VERSION']}` "
                f"(`{fp['HARBOR_ENV']}`) · tasks: `{fp['TASKS']}`", ""]
    if not data["settings_consistent"]:
        out += ["**Warning:** the runs under this label were made with different settings.", ""]

    out += ["| Side | Complete runs | Accuracy per run (%) | Mean ± SEM (%) | Cost (USD) |",
            "|---|---|---|---|---|"]
    for side in SIDES:
        runs, st = sides[side]["runs"], sides[side]["stats"]
        per_run = ", ".join(f"run {r['run']}: {pct(r['accuracy'])}{'' if r['complete'] else ' (incomplete)'}"
                            for r in runs) or "–"
        mean = "–" if not st["n"] else (pct(st["mean"]) + (f" ± {pct(st['sem'])}" if st["sem"] is not None else ""))
        costs = [r["cost_usd"] for r in runs if r["cost_usd"] is not None]
        out.append(f"| {SIDE_NAMES[side]} | {st['n']} | {per_run} | {mean} | "
                   f"{f'{sum(costs):.2f}' if costs else '–'} |")
    out.append("")
    if data["range_overlap"] is not None:
        out += [f"Run-score ranges overlap: **{'yes' if data['range_overlap'] else 'no'}** "
                "(the match criterion, meant for three runs per side).", ""]

    out += ["## Errors", ""]
    for side in SIDES:
        for r in sides[side]["runs"]:
            listed = ", ".join(f"{k}: {v}" for k, v in r["errors"].items()) or "none"
            out.append(f"- {side}, run {r['run']} ({r['n']} tasks, shards {r['shards']}, "
                       f"job {'/'.join(r['job_status'])}): {listed}")
    out.append("")

    per_task, disagree = data["per_task"], data["disagreements"]
    both = [t for t in per_task if len(per_task[t]) == 2]
    if both:
        out += ["## Per task", "",
                f"{len(both)} tasks have complete runs on both sides; the share of runs answered "
                f"correctly differs on {len(disagree)}.", ""]
        listed = both if len(both) <= MAX_LISTED else disagree[:MAX_LISTED]
        if listed:
            out += ["| Task | Scenario | Category | Original (correct/runs) | Harbor (correct/runs) |",
                    "|---|---|---|---|---|"]
            for t in listed:
                info = task_info.get(t, {})
                o, h = per_task[t]["original"], per_task[t]["harbor"]
                mark = "" if t not in disagree else " ≠"
                out.append(f"| {t}{mark} | {info.get('scenario', '')} | {info.get('category', '')} | "
                           f"{int(sum(o))}/{len(o)} | {int(sum(h))}/{len(h)} |")
            if len(both) > MAX_LISTED and len(disagree) > MAX_LISTED:
                out.append(f"\n{len(disagree) - MAX_LISTED} more disagreements in summary.json.")
            out.append("")

        out += ["## By scenario (all complete runs pooled)", "",
                "| Scenario | Original n | Original acc. (%) | Harbor n | Harbor acc. (%) |",
                "|---|---|---|---|---|"]
        for scenario, st in data["by_scenario"].items():
            o, h = st["original"], st["harbor"]
            out.append(f"| {scenario} | {o['n']} | {pct(o.get('mean'))} | {h['n']} | {pct(h.get('mean'))} |")
        out.append("")

    links = sorted({u for s in SIDES for r in sides[s]["runs"] for u in r["workflow_runs"]})
    if links:
        out += ["Workflow runs: " + ", ".join(f"[{u.rsplit('/', 1)[-1]}]({u})" for u in links), ""]
    return "\n".join(out)


def main() -> None:
    label_dir = Path(sys.argv[1])
    data, text = summarize(label_dir)
    (label_dir / "summary.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    (label_dir / "summary.md").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
