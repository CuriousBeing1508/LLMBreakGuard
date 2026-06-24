"""
run_static_analysis.py

PURPOSE:
    Clones the client repo, checks out pre_breaking_commit, determines
    the correct pre-state directory, and runs the Spoon JAR to extract
    all usages of the target library.

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

    5. VERSION-BOUNDARY DETECTION FOR PRE DIR
       The correct pre-state directory is determined by reading
       pom.xml at successive commits using git show (no checkout
       needed). The logic handles three cases:
         a. hint commit has new_version  -> it IS the breaking commit;
            create a git worktree at its parent for the pre dir.
         b. hint commit has old_version  -> it IS the pre state;
            reuse client_dir; Dockerfile.breaking will override version.
         c. neither version found        -> walk back up to MAX_WALK
            commits until the old->new boundary is located.
       Using git show avoids modifying the working tree and avoids
       cloning the repo a second time (worktrees share the object db).
"""

import subprocess
import sys
import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path


MAVEN_NS   = "http://maven.apache.org/POM/4.0.0"
MAX_WALK   = 20   # maximum commits to walk back when searching for version boundary


def get_spoon_jar():
    env_path = os.environ.get("SPOON_JAR_PATH")
    if env_path:
        return Path(env_path)
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
    subprocess.run(["git", "clone", clone_url, str(client_dir)], check=True)


def fetch_and_checkout(client_dir, pre_breaking_commit, pull_number, row_index):
    print(f"row {row_index}: fetching refs", file=sys.stderr)
    try:
        subprocess.run(
            ["git", "fetch", "--all", "--tags", "--prune", "--force"],
            cwd=client_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"],
            cwd=client_dir, check=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        print(f"row {row_index}: git fetch failed\n{e.stderr.decode()}", file=sys.stderr)
        sys.exit(1)

    if not commit_exists(client_dir, pre_breaking_commit):
        print(f"row {row_index}: commit not found, fetching PR #{pull_number}", file=sys.stderr)
        subprocess.run(
            ["git", "fetch", "origin", f"pull/{pull_number}/head:pr-{pull_number}"],
            cwd=client_dir, check=True
        )

    print(f"row {row_index}: checking out {pre_breaking_commit}", file=sys.stderr)
    try:
        subprocess.run(
            ["git", "checkout", pre_breaking_commit],
            cwd=client_dir, check=True, capture_output=True
        )
        print(f"row {row_index}: checkout successful", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"row {row_index}: git checkout failed\n{e.stderr.decode()}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# pom.xml version reading (no checkout required)
# ---------------------------------------------------------------------------

def read_pom_at_commit(client_dir, commit):
    """
    Returns the text of pom.xml at a specific commit using git show.
    Does NOT modify the working tree.  Returns None on failure.
    """
    result = subprocess.run(
        ["git", "show", f"{commit}:pom.xml"],
        cwd=client_dir, capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


def parse_version_from_pom(pom_text, group_id, artifact_id):
    """
    Extracts the declared version of a dependency from pom.xml text.

    Handles:
    - Maven XML namespace (http://maven.apache.org/POM/4.0.0) or no namespace
    - Simple ${property} version references (resolved from <properties>)
    - Both <dependencies> and <dependencyManagement> sections

    Returns the resolved version string, or None if not found.
    """
    if not pom_text:
        return None
    try:
        root = ET.fromstring(pom_text)
    except ET.ParseError:
        return None

    ns = MAVEN_NS if root.tag.startswith(f"{{{MAVEN_NS}}}") else ""

    def t(name):
        return f"{{{ns}}}{name}" if ns else name

    # Collect <properties> for ${...} resolution
    properties = {}
    props_el = root.find(t("properties"))
    if props_el is not None:
        for child in props_el:
            local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child.text:
                properties[local] = child.text.strip()

    def resolve(val):
        if val and val.startswith("${") and val.endswith("}"):
            return properties.get(val[2:-1], val)
        return val

    for dep in root.iter(t("dependency")):
        gid = dep.find(t("groupId"))
        aid = dep.find(t("artifactId"))
        ver = dep.find(t("version"))
        if (gid is not None and gid.text == group_id
                and aid is not None and aid.text == artifact_id
                and ver is not None and ver.text):
            return resolve(ver.text.strip())

    return None


def parent_commit(client_dir, commit):
    """Returns the hash of the first parent of commit, or None."""
    result = subprocess.run(
        ["git", "rev-parse", f"{commit}^"],
        cwd=client_dir, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


# ---------------------------------------------------------------------------
# Pre-state directory resolution
# ---------------------------------------------------------------------------

def resolve_pre_client_dir(client_dir, pre_client_dir,
                            hint_commit, pull_number,
                            group_id, artifact_id, old_version, new_version,
                            row_index):
    """
    Determines the correct directory to use as the pre-stage build context.

    Strategy
    --------
    Walk backwards from hint_commit reading pom.xml at each commit via
    git show (no checkout needed).  Find the first commit whose pom.xml
    has new_version and whose parent has old_version — that is the
    version-bump boundary.

    Result
    ------
    Case A  hint_commit (or a nearby ancestor) is the breaking commit:
      pom at breaking commit = new_version
      pom at parent          = old_version
      -> Create a git worktree at the parent commit in pre_client_dir.
         Dockerfile.pre uses pre_client_dir (pom has old_version — no override needed).
         Dockerfile.breaking uses client_dir  (pom has new_version — override is a no-op).
      -> Returns str(pre_client_dir)

    Case B  hint_commit already has old_version (user pointed at the pre state):
      -> No second clone needed.  Both Docker images build from client_dir;
         Dockerfile.breaking overrides the version to new_version.
      -> Returns str(client_dir)

    Exits with a clear error if the boundary cannot be found within MAX_WALK commits.
    """

    commit  = hint_commit
    visited = []   # [(hash, version)] in backwards order

    for depth in range(MAX_WALK + 1):
        pom_text = read_pom_at_commit(client_dir, commit)
        version  = parse_version_from_pom(pom_text, group_id, artifact_id)

        print(
            f"row {row_index}: {commit[:8]}  {artifact_id}={version or 'not-found'}",
            file=sys.stderr
        )
        visited.append((commit, version))

        if version == old_version:
            # This commit is the pre state.
            # Check whether the previous iteration found the breaking commit.
            if len(visited) >= 2:
                prev_commit, prev_version = visited[-2]
                if prev_version == new_version:
                    # visited[-2] is the breaking commit, visited[-1] is the pre commit.
                    # We found the boundary while walking; use pre_client_dir worktree.
                    _create_worktree(client_dir, pre_client_dir, commit, row_index)
                    return str(pre_client_dir)

            # hint_commit itself (depth == 0) OR we got here without ever seeing
            # new_version above us. Either way, client_dir is the pre state.
            print(
                f"row {row_index}: hint commit already at old_version={old_version}; "
                f"pre_client_dir = client_dir (Dockerfile.breaking will override version)",
                file=sys.stderr
            )
            return str(client_dir)

        if version == new_version:
            # This commit has new_version.  Check its parent for old_version.
            par = parent_commit(client_dir, commit)
            if par is None:
                print(
                    f"row {row_index}: {commit[:8]} has new_version but has no parent",
                    file=sys.stderr
                )
                sys.exit(1)

            par_pom     = read_pom_at_commit(client_dir, par)
            par_version = parse_version_from_pom(par_pom, group_id, artifact_id)
            print(
                f"row {row_index}: {par[:8]}  {artifact_id}={par_version or 'not-found'} (parent)",
                file=sys.stderr
            )

            if par_version == old_version:
                # Perfect boundary found: commit = breaking, par = pre.
                _create_worktree(client_dir, pre_client_dir, par, row_index)
                return str(pre_client_dir)

            # Parent doesn't have old_version — keep walking back.
            commit = par
            visited.append((par, par_version))
            continue

        # Version not found at this commit — keep walking back.
        par = parent_commit(client_dir, commit)
        if par is None:
            break
        commit = par

    print(
        f"\nrow {row_index}: ERROR — could not find version boundary for "
        f"{group_id}:{artifact_id} between {old_version} and {new_version} "
        f"within {MAX_WALK} commits of {hint_commit[:8]}.\n"
        f"Check that pre_breaking_commit in bc-config.csv is the commit that "
        f"introduced the version bump, or a commit close to it.",
        file=sys.stderr
    )
    sys.exit(1)


def _create_worktree(client_dir, pre_client_dir, commit_hash, row_index):
    """
    Creates a git worktree at pre_client_dir checked out at commit_hash.
    Worktrees share the object database with client_dir so no extra data
    is downloaded.  Skipped if pre_client_dir already exists.
    """
    if pre_client_dir.exists():
        print(
            f"row {row_index}: pre worktree already exists at {pre_client_dir}",
            file=sys.stderr
        )
        return

    print(
        f"row {row_index}: creating pre worktree at {commit_hash[:8]} → {pre_client_dir}",
        file=sys.stderr
    )
    pre_client_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(pre_client_dir), commit_hash],
            cwd=client_dir, check=True, capture_output=True
        )
        print(f"row {row_index}: pre worktree ready", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(
            f"row {row_index}: git worktree add failed\n{e.stderr.decode()}",
            file=sys.stderr
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Spoon analysis
# ---------------------------------------------------------------------------

def run_spoon(client_dir, analysis_root, entry, row_index):
    spoon_jar = get_spoon_jar()

    if not spoon_jar.exists():
        print(f"spoon jar not found at {spoon_jar}", file=sys.stderr)
        print("set SPOON_JAR_PATH env var or place jar at spoon-analysis/spoon-extractor.jar",
              file=sys.stderr)
        sys.exit(1)

    row_id     = f"row_{row_index}"
    output_dir = Path(analysis_root).resolve() / f"analysis_{row_index - 1}"
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
    print(f"row {row_index}: looking for {entry['library_group_id']}:{entry['library_name']}",
          file=sys.stderr)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(result.stdout, file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"row {row_index}: spoon failed\n{e.stderr}", file=sys.stderr)
        sys.exit(1)

    usage_dir = output_dir / row_id / "UsageReport"
    found     = list(usage_dir.glob("*.json")) if usage_dir.exists() else []

    if not found:
        print(f"row {row_index}: spoon found no classes using {entry['library_name']}",
              file=sys.stderr)
        print("check library_name, library_group_id and import_prefix", file=sys.stderr)
        sys.exit(1)

    print(f"row {row_index}: spoon found {len(found)} output file(s)", file=sys.stderr)
    for f in found:
        with open(f) as fp:
            data = json.load(fp)
        if isinstance(data, list):
            print(f"  {f.name}: {len(data)} usage block(s)", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_static_analysis(configs_path, workspace, analysis_root):
    with open(configs_path) as f:
        configs = json.load(f)

    # Resolve to absolute paths so that git worktree add and all subprocess
    # calls receive absolute paths regardless of CWD at invocation time.
    workspace     = Path(workspace).resolve()
    analysis_root = Path(analysis_root).resolve()

    for i, entry in enumerate(configs):
        row_index   = i + 1
        clone_url   = entry["clone_url"]
        pull_number = entry["pull_number"]
        hint_commit = entry["pre_breaking_commit"]
        client_name = clone_url.split("/")[-1].replace(".git", "")

        client_dir     = workspace / "clients" / f"{client_name}_{i}"
        pre_client_dir = workspace / "clients" / f"{client_name}_{i}_pre"

        print(f"\nrow {row_index}: {clone_url}", file=sys.stderr)

        # Clone once and check out at the hint commit.
        clone_repo(clone_url, client_dir)
        fetch_and_checkout(client_dir, hint_commit, pull_number, row_index)

        # Determine pre-state directory by reading pom.xml via git show —
        # no extra clone required.  Returns client_dir if hint already has
        # old_version, or creates a worktree at the parent commit otherwise.
        actual_pre_dir = resolve_pre_client_dir(
            client_dir, pre_client_dir,
            hint_commit, pull_number,
            entry["library_group_id"], entry["library_name"],
            entry["old_version"],      entry["new_version"],
            row_index
        )

        # Store actual pre dir back into the in-memory config for downstream use.
        entry["actual_pre_client_dir"] = actual_pre_dir

        run_spoon(client_dir, analysis_root, entry, row_index)

    print("\nstatic analysis complete", file=sys.stderr)


if __name__ == "__main__":
    configs_path  = sys.argv[1]   # workspace/configs.json
    workspace     = sys.argv[2]   # workspace
    analysis_root = sys.argv[3]   # workspace/analysis

    run_static_analysis(configs_path, workspace, analysis_root)
