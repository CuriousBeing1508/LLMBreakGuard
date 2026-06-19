"""
run_static_analysis.py

PURPOSE:
    Clones the client repo, checks out pre_breaking_commit, and
    runs the Spoon JAR to extract all usages of the target library.

DESIGN DECISIONS:
    1. ROW INDEX REPLACES BUMP ID
       The Spoon JAR requires a --bump-id argument which it uses
       for naming output directories and files. Since this tool
       does not use bump IDs, the row index (row_1, row_2 etc.)
       is passed instead.
       Reason: keeps output files uniquely named per row without
       requiring a user-facing ID field in bc-config.csv.

    2. JAR PATH FROM ENVIRONMENT VARIABLE WITH LOCAL FALLBACK
       SPOON_JAR_PATH environment variable is used if set.
       Falls back to relative path from script location for
       local development.
       Reason: in Docker the JAR is at /app/spoon-extractor.jar
       set via ENV. Locally it is at spoon-analysis/ in the
       project root. One variable handles both cases.

    3. TWO STEP SPOON PIPELINE
       The JAR runs two steps internally:
         Step 1: ExtractVersionImport -> writes CSV of Java files
                 that import the target library
         Step 2: ExtractUsages -> runs Spoon on those files and
                 writes usage JSON
       The output JSON files land in:
         analysis_root/row_{i}/UsageReport/*.json

    4. COMMIT NOT FOUND FALLBACK
       If pre_breaking_commit is not found after fetch --all,
       the PR ref is fetched explicitly as a fallback.
       Reason: some commits only exist on PR branches not on
       the default branch. This mirrors the original research
       script behavior.
"""

import subprocess
import sys
import os
import json
from pathlib import Path


def get_spoon_jar():
    """
    Resolves Spoon JAR path.
    Uses SPOON_JAR_PATH env var if set (Docker).
    Falls back to relative path for local development.
    """
    env_path = os.environ.get("SPOON_JAR_PATH")
    if env_path:
        return Path(env_path)

    # local: LLMBreakGuard/spoon-analysis/spoon-extractor.jar
    return (
        Path(__file__).resolve().parent.parent.parent
        / "spoon-analysis"
        / "spoon-extractor.jar"
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
        print(f"repo already cloned at {client_dir}", file=sys.stderr)
        return

    print(f"cloning {clone_url} into {client_dir}", file=sys.stderr)
    client_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", clone_url, str(client_dir)],
        check=True
    )


def fetch_and_checkout(client_dir, pre_breaking_commit, pull_number, row_index):
    print(f"row {row_index}: fetching refs", file=sys.stderr)
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
        print(f"row {row_index}: git fetch failed\n{e.stderr.decode()}", file=sys.stderr)
        sys.exit(1)

    if not commit_exists(client_dir, pre_breaking_commit):
        print(f"row {row_index}: commit not found, fetching PR #{pull_number}", file=sys.stderr)
        subprocess.run(
            ["git", "fetch", "origin", f"pull/{pull_number}/head:pr-{pull_number}"],
            cwd=client_dir,
            check=True
        )

    print(f"row {row_index}: checking out {pre_breaking_commit}", file=sys.stderr)
    try:
        subprocess.run(
            ["git", "checkout", pre_breaking_commit],
            cwd=client_dir,
            check=True,
            capture_output=True
        )
        print(f"row {row_index}: checkout successful", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"row {row_index}: git checkout failed\n{e.stderr.decode()}", file=sys.stderr)
        sys.exit(1)


def run_spoon(client_dir, analysis_root, entry, row_index):
    spoon_jar  = get_spoon_jar()

    if not spoon_jar.exists():
        print(f"spoon jar not found at {spoon_jar}", file=sys.stderr)
        print(f"set SPOON_JAR_PATH env var or place jar at spoon-analysis/spoon-extractor.jar", file=sys.stderr)
        sys.exit(1)

    # row index used as bump-id for naming output files
    row_id     = f"row_{row_index}"
    output_dir = Path(analysis_root) / f"analysis_{row_index - 1}"
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "java", "-jar", str(spoon_jar),
        "--bump-id",        row_id,
        "--clients-folder", str(client_dir),
        "--analysis-root",  str(output_dir),
        "--dep-group",      entry["library_group_id"],
        "--dep-artifact",   entry["library_name"],
        "--import-prefix",  entry["import_prefix"]
    ]

    print(f"row {row_index}: running spoon on {client_dir}", file=sys.stderr)
    print(f"row {row_index}: looking for {entry['library_group_id']}:{entry['library_name']}", file=sys.stderr)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(result.stdout, file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"row {row_index}: spoon failed\n{e.stderr}", file=sys.stderr)
        sys.exit(1)

    # Spoon writes output to analysis_root/row_{i}/UsageReport/*.json
    usage_dir = output_dir / row_id / "UsageReport"
    found     = list(usage_dir.glob("*.json")) if usage_dir.exists() else []

    if not found:
        print(f"row {row_index}: spoon found no classes using {entry['library_name']}", file=sys.stderr)
        print(f"row {row_index}: check library_name, library_group_id and import_prefix", file=sys.stderr)
        sys.exit(1)

    print(f"row {row_index}: spoon found {len(found)} output file(s)", file=sys.stderr)
    for f in found:
        with open(f) as fp:
            data = json.load(fp)
        if isinstance(data, list):
            print(f"  {f.name}: {len(data)} usage block(s)", file=sys.stderr)


def run_static_analysis(configs_path, workspace, analysis_root):
    with open(configs_path) as f:
        configs = json.load(f)

    for i, entry in enumerate(configs):
        row_index   = i + 1
        clone_url   = entry["clone_url"]
        pull_number = entry["pull_number"]
        pre_commit  = entry["pre_breaking_commit"]
        client_name = clone_url.split("/")[-1].replace(".git", "")

        client_dir  = Path(workspace) / "clients" / f"{client_name}_{i}"

        print(f"\nrow {row_index}: {clone_url}", file=sys.stderr)

        clone_repo(clone_url, client_dir)
        fetch_and_checkout(client_dir, pre_commit, pull_number, row_index)
        run_spoon(client_dir, analysis_root, entry, row_index)

    print("\nstatic analysis complete", file=sys.stderr)


if __name__ == "__main__":
    configs_path  = sys.argv[1]   # workspace/configs.json
    workspace     = sys.argv[2]   # workspace
    analysis_root = sys.argv[3]   # workspace/analysis

    run_static_analysis(configs_path, workspace, analysis_root)