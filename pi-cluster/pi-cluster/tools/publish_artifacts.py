#!/usr/bin/env python3
"""Build/publish artifacts from artifacts/recipes/*.yaml into artifacts/repo.

Two recipe kinds:
  source: <path>   a file copied as is (scripts)
  build:  <path>   a script producing one tar.gz tree
The repo directory is runtime distribution, not source of truth (gitignored).
"""

import json
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from controller.artifacts import REPO, sha256_file  # noqa: E402
from controller.model import ROOT, load_yaml  # noqa: E402


def git_commit():
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "-q", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def build(r, work):
    """Return (file, build_info) for one recipe."""
    if "source" in r:
        src = ROOT / r["source"]
        return src, {"source": r["source"], "source_sha256": sha256_file(src)}
    script = ROOT / r["build"]
    out = work / f"{r['name']}.tar.gz"
    p = subprocess.run([str(script), str(out)], capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"{r['name']}: {script} failed: {p.stderr.strip()}")
    return out, {"build": r["build"], "build_script_sha256": sha256_file(script)}


def main():
    commit, work = git_commit(), pathlib.Path("/tmp/picluster-build")
    work.mkdir(exist_ok=True)
    for recipe in sorted((ROOT / "artifacts" / "recipes").glob("*.yaml")):
        r = load_yaml(recipe)
        blob, info = build(r, work)
        sha = sha256_file(blob)
        rel = pathlib.Path(r["name"]) / sha / blob.name
        (REPO / rel).parent.mkdir(parents=True, exist_ok=True)
        if not (REPO / rel).exists():
            shutil.copy(blob, REPO / rel)
        manifest = {"name": r["name"], "type": r["type"],
                    "service_types": r["service_types"],
                    "entrypoint": r["entrypoint"], "sha256": sha, "path": str(rel),
                    "file": blob.name, "recipe_commit": commit, **info}
        (REPO / r["name"] / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"published {r['name']} ({r['type']}) sha256={sha[:12]}")


if __name__ == "__main__":
    main()
