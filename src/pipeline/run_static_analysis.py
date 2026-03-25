import subprocess
import sys
import json
from pathlib import Path

import os

# works both locally and inside Docker container
# locally:  LLMBreakGuard/spoon-analysis/spoon-extractor.jar
# docker:   /app/spoon-extractor.jar (set via environment variable)
SPOON_JAR = Path(
    os.environ.get(
        "SPOON_JAR_PATH",
        Path(__file__).resolve().parent.parent.parent / "spoon-analysis" / "spoon-extractor.jar"
    )
)


def commit_exists(repo_path, commit_hash):
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit_hash}^{{commit}}"],
        cwd=repo_path,
        capture_output=True
    )
    return result.returncode == 0


def clone_repo(clone_url, client_dir):
    if client_dir.exists():
        print(f"repo already cloned at {client_dir}")
        return

    print(f"cloning {clone_url} into {client_dir}")
    client_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", clone_url, str(client_dir)],
        check=True
    )


def fetch_and_checkout(client_dir, pre_breaking_commit, pull_number, row_index):
    print(f"row {row_index}: fetching refs")
    try:
        subprocess.run(
            ["git", "fetch", "--all", "--tags", "--prune", "--force"],
            cwd=client_dir,
            check=True,
            capture_output=True
        )
        subprocess.run(
            ["git", "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"],
            cwd=client_dir,
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError as e:
        print(f"row {row_index}: git fetch failed\n{e.stderr.decode()}")
        sys.exit(1)

    if not commit_exists(client_dir, pre_breaking_commit):
        print(f"row {row_index}: commit not found, fetching PR #{pull_number}")
        subprocess.run(
            ["git", "fetch", "origin", f"pull/{pull_number}/head:pr-{pull_number}"],
            cwd=client_dir,
            check=True
        )

    print(f"row {row_index}: checking out {pre_breaking_commit}")
    try:
        subprocess.run(
            ["git", "checkout", pre_breaking_commit],
            cwd=client_dir,
            check=True,
            capture_output=True
        )
        print(f"row {row_index}: checkout successful")
    except subprocess.CalledProcessError as e:
        print(f"row {row_index}: git checkout failed\n{e.stderr.decode()}")
        sys.exit(1)


def run_spoon(client_dir, analysis_dir, entry, row_index):
    analysis_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "java", "-jar", str(SPOON_JAR),
        "--clients-folder", str(client_dir),
        "--analysis-root",  str(analysis_dir),
        "--dep-group",      entry["library_group_id"],
        "--dep-artifact",   entry["library_name"],
        "--import-prefix",  entry["import_prefix"]
    ]

    print(f"row {row_index}: running spoon on {client_dir}")
    print(f"row {row_index}: looking for {entry['library_group_id']}:{entry['library_name']}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"row {row_index}: spoon completed\n{result.stdout}")
    except subprocess.CalledProcessError as e:
        print(f"row {row_index}: spoon failed\n{e.stderr}")
        sys.exit(1)

    found = list(analysis_dir.glob("*_analysis.json"))
    if not found:
        print(f"row {row_index}: spoon found no classes using {entry['library_name']}")
        print(f"row {row_index}: check library_name, library_group_id and import_prefix")
        sys.exit(1)

    print(f"row {row_index}: spoon found {len(found)} class(es)")
    for f in found:
        with open(f) as fp:
            data = json.load(fp)
        print(f"  {data['packageName']}.{data['className']}")


def run_static_analysis(configs_path, workspace, analysis_root):
    with open(configs_path) as f:
        configs = json.load(f)

    for i, entry in enumerate(configs):
        row_index    = i + 1
        clone_url    = entry["clone_url"]
        pull_number  = entry["pull_number"]
        pre_commit   = entry["pre_breaking_commit"]
        client_name  = clone_url.split("/")[-1].replace(".git", "")

        client_dir   = Path(workspace) / "clients"  / f"{client_name}_{i}"
        analysis_dir = Path(analysis_root) / f"analysis_{i}"

        print(f"\nrow {row_index}: {clone_url}")

        clone_repo(clone_url, client_dir)
        fetch_and_checkout(client_dir, pre_commit, pull_number, row_index)
        run_spoon(client_dir, analysis_dir, entry, row_index)

    print("\nstatic analysis complete for all rows")


if __name__ == "__main__":
    configs_path  = sys.argv[1]   # /tmp/llmbreakguard/configs.json
    workspace     = sys.argv[2]   # $GITHUB_WORKSPACE
    analysis_root = sys.argv[3]   # /tmp/llmbreakguard/analysis

    run_static_analysis(configs_path, workspace, analysis_root)