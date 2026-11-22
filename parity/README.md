# TavernBench parity runs on GitHub Actions

This branch (`parity-ci`) runs the parity experiment for the TavernBench adapter
([harbor-index/adapters#34](https://github.com/harbor-index/adapters/pull/34)) on GitHub-hosted
runners. It is not part of the adapter pull request.

Both sides run the same agent: Claude Code at a pinned version, with the same model, flags,
environment and instruction, in a container built from the task's image definition.

- **Original:** [`run_agent.py`](https://github.com/mingyin1/AutoTavern/blob/harbor-parity/tavernbench/tavernbench/run_agent.py)
  on the AutoTavern fork, scored by the benchmark's unchanged `tavernbench.score`.
- **Harbor:** the adapter's tasks, run with `harbor run -a claude-code`.

## Setup (once)

In the repository settings, under **Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `TB_API_KEY` | the API key for the model |
| `TB_BASE_URL` | only for a custom Anthropic-compatible endpoint: its base URL, without `/v1` |
| `DAYTONA_API_KEY` | only with `HARBOR_ENV=daytona` |

Secrets are passed to the agent steps only. Before anything is uploaded or committed, every file is
searched for the secret values and they are replaced with `[REDACTED]`.

## Running

Edit [`config.env`](config.env) on this branch and push it. The push starts the workflow
**TavernBench parity** (Actions tab). Settings, in the order the adapter docs require:

| Step | Settings |
|---|---|
| Dry run (optional; spends no API budget) | `ENABLED=true DRY_RUN=true`, with `MODEL` set to any id and `CLAUDE_CODE_VERSION` to a real version |
| 1. Pilot, both sides | `ENABLED=true DRY_RUN=false LABEL=pilot TASKS=pilot RUNS=1 SHARDS=1`, plus `MODEL`, `CLAUDE_CODE_VERSION`, `AUTH_VAR` and, if used, `REASONING_EFFORT` |
| 2. Full run 1, both sides | `LABEL=full TASKS=all RUNS=1 SHARDS=4` |
| 3. Full runs 2 and 3 | `LABEL=full TASKS=all RUNS=2,3 SHARDS=4` |

Keep `MODEL`, `CLAUDE_CODE_VERSION`, `REASONING_EFFORT` and `HARBOR_VERSION` the same for every
run under one label. The workflow refuses a run whose label already holds runs made with other
settings, or whose run number already exists there. Set `ENABLED=false` when you are done.

`SHARDS` splits the tasks of each run and side over several jobs, which run in parallel. Each job
has a six-hour limit and runs `N_CONCURRENT` agents at a time.

## What the workflow does

1. **Plan and pre-flight checks.** It reads `config.env` and checks that the secrets exist. Using
   Harbor's own code, it then checks that `run_agent.py` sends exactly the scripts, environment and
   CLI flags that Harbor's `claude-code` agent sends, for every credential setup
   (`check_harbor_agent.py`). It also checks that, for each of the 368 tasks, the instruction Harbor
   gives the agent is the text `run_agent.py` reads. A difference stops the workflow before any API
   use.
2. **Harbor side.** For each run and shard, it generates the tasks from the adapter branch and runs
   `harbor run -a claude-code -m <provider>/<model> --ak version=... --ak disable_web_search=true`.
   It then collects each trial's reward and errors with Harbor's own result model
   (`collect_harbor.py`).
3. **Original side.** For each run and shard, it builds the agent image and runs `run_agent.py` on
   the same tasks. It applies Harbor's error policy (`error_policy.py`) and scores with the unchanged
   upstream scorer. A run that ended in a model-API error counts as 0 on both sides; a timeout or
   another failed exit is graded on the answer left, as Harbor does.
4. **Publish.** It commits the results to the `parity-results` branch under `runs/<LABEL>/` (or
   `dryrun/<LABEL>/`), with `summary.md`. The summary gives each run's accuracy, mean ± sample SEM
   per side, whether the two sides' run-score ranges overlap, per-task agreement, errors and cost.
   Agent logs are committed for the pilot only. For every run they are in the workflow's artifacts,
   together with Harbor's full job directories.

## Files

| File | Purpose |
|---|---|
| `config.env` | the settings of the next run |
| `pilot_ids.txt` | the 10 pilot tasks: 10 scenarios that cover all 8 categories and all 8 scoring rules |
| `plan.py` | reads and checks the settings, plans the jobs, selects each shard's tasks, writes `meta.json` |
| `check_harbor_agent.py` | the pre-flight equivalence check |
| `agent_env.sh` | sets the same clean credential environment on both sides |
| `collect_harbor.py` | turns a Harbor job into `trials.jsonl` |
| `error_policy.py` | applies Harbor's error policy to the original side's answers |
| `redact.py` | removes secret values from files |
| `publish.py`, `summarize.py` | add results to the `parity-results` branch and write the summary |
