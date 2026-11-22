"""Put downloaded parity results into a checkout of the parity-results branch.

    python3 parity/publish.py --downloads downloads --results results --results-dir runs/<LABEL>

Each artifact parity-<side>-run<N>-shard<K> goes to <results-dir>/<side>/run<N>/shard<K>/
(replacing an earlier copy of the same shard, e.g. from a re-run job). Agent logs are kept
for pilot runs only; for full runs they stay in the workflow's artifacts. Writes the
label's fingerprint.json the first time, then summary.md and summary.json. A dry run
replaces everything under dryrun/.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summarize  # noqa: E402

ARTIFACT = re.compile(r"parity-(harbor|original)-run(\d+)-shard(\d+)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--downloads", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True, help="parity-results checkout")
    parser.add_argument("--results-dir", required=True, help="runs/<LABEL> or dryrun/<LABEL>")
    args = parser.parse_args()

    label_dir = args.results / args.results_dir
    if args.results_dir.startswith("dryrun/") and (args.results / "dryrun").exists():
        shutil.rmtree(args.results / "dryrun")
    label_dir.mkdir(parents=True, exist_ok=True)

    artifacts = sorted(p for p in args.downloads.iterdir() if p.is_dir()) if args.downloads.is_dir() else []
    copied = 0
    for artifact in artifacts:
        match = ARTIFACT.fullmatch(artifact.name)
        if not match or not (artifact / "meta.json").is_file():
            print(f"skipping {artifact.name}")
            continue
        side, run, shard = match.group(1), int(match.group(2)), int(match.group(3))
        meta = json.loads((artifact / "meta.json").read_text(encoding="utf-8"))
        dest = label_dir / side / f"run{run}" / f"shard{shard}"
        if dest.exists():
            shutil.rmtree(dest)
        ignore = None if meta["config"]["TASKS"] == "pilot" else shutil.ignore_patterns("logs")
        shutil.copytree(artifact, dest, ignore=ignore)
        fingerprint = label_dir / "fingerprint.json"
        if not fingerprint.exists():
            fingerprint.write_text(json.dumps(meta["fingerprint"], indent=1) + "\n", encoding="utf-8")
        copied += 1
        print(f"{artifact.name} -> {dest.relative_to(args.results)}")
    print(f"{copied} result folder(s) added")

    data, text = summarize.summarize(label_dir)
    (label_dir / "summary.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    (label_dir / "summary.md").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
