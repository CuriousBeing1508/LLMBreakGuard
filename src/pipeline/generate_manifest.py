"""
generate_manifest.py

PURPOSE:
    Reads Spoon analysis output and builds manifest.json which
    serves as the single source of truth for all downstream steps.

DESIGN DECISIONS:
    1. TEST CLASS NAMING: R{row}C{class}U{usage}BCDetectorTest
       Row index, class index, usage index keep names short.
       Reason: full class+method names cause file system path
       length issues and Maven Surefire parsing problems.

    2. TEST PACKAGE: {original_package}.llmtests
       All LLM generated tests live in a llmtests subpackage.
       Reason: cleanly separates generated tests from existing
       client tests, prevents name clashes, easy to target with
       **/llmtests/* pattern.

    3. ONE USAGE BLOCK = ONE TEST CLASS FILE
       Each method in the client class that uses the library =
       one usage block from Spoon = one prompt = one test file.
       Reason: keeps each test focused on one focal method making
       it easier to identify exactly which method triggers a BC.

    4. SPOON OUTPUT PATH CONVENTION
       Spoon writes output to:
         analysis_root/analysis_{i}/row_{i+1}/UsageReport/*.json
       This is driven by how SpoonPipeline.java uses --bump-id
       and --analysis-root arguments.

    5. FULL CLASS SOURCE READ HERE
       Focal class source is read from the cloned repo and stored
       in the manifest so prompt_llm.py does not need to access
       the client repo directly.
       Reason: single point of file I/O, all downstream scripts
       work purely from the manifest.

    6. INNER CLASS SEPARATOR SANITIZED
       $ in inner class names is replaced with nothing for test
       class naming since $ is not valid in Java identifiers used
       as filenames.

    7. NO SILENT DEFAULTS FOR REQUIRED FIELDS
       testing_framework, test_source_root, and llm_tests_folder
       must be present in the config. If missing the script exits
       with a clear error telling the user to run detect mode.
       Reason: silently defaulting to junit5 when the project uses
       testng causes all generated tests to fail compilation.
       The user must always validate these fields via detect mode.
"""

import sys
import json
from pathlib import Path


def package_to_path(package_name):
    return package_name.replace(".", "/")


def extract_package_from_fqn(client_class_fqn):
    parts = client_class_fqn.rsplit(".", 1)
    if len(parts) == 2:
        return parts[0]
    return ""


def read_focal_class_source(client_dir, file_path):
    """
    Reads full source of the focal class from the cloned repo.
    Tries direct path first then recursive search as fallback.
    """
    direct = Path(client_dir) / file_path
    if direct.exists():
        return direct.read_text(encoding="utf-8", errors="replace")

    file_name = Path(file_path).name
    matches = [
        p for p in Path(client_dir).rglob(file_name)
        if str(p).endswith(file_path)
    ]
    if matches:
        best = min(matches, key=lambda p: len(str(p)))
        return best.read_text(encoding="utf-8", errors="replace")

    return f"// source not found for {file_path}"


def find_usage_files(analysis_root, row_index):
    """
    Finds Spoon output JSON files for a given row.
    Spoon writes to:
      analysis_root/analysis_{i}/row_{row}/UsageReport/*.json
    where i = row_index - 1
    """
    i         = row_index - 1
    row_id    = f"row_{row_index}"
    usage_dir = Path(analysis_root) / f"analysis_{i}" / row_id / "UsageReport"

    if not usage_dir.exists():
        print(f"usage dir not found: {usage_dir}", file=sys.stderr)
        return []

    return list(usage_dir.glob("*.json"))


def validate_required_fields(entry, row_num):
    """
    Validates that fields which must be confirmed by the user
    are present. Exits with a clear error if any are missing.
    These fields are detected in detect mode and confirmed by
    the user before run mode — they must never be defaulted.
    """
    required = {
        "testing_framework": "junit5, junit4, or testng",
        "test_source_root":  "e.g. src/test/java",
        "llm_tests_folder":  "e.g. bc_generated_tests"
    }

    missing = []
    for field, example in required.items():
        if not entry.get(field, "").strip():
            missing.append(f"  {field} ({example})")

    if missing:
        print(f"\nrow {row_num}: missing required fields:", file=sys.stderr)
        for m in missing:
            print(m, file=sys.stderr)
        print(
            f"\nthese fields must be confirmed by the user before running.",
            file=sys.stderr
        )
        print(
            f"run with mode: detect first to auto-detect them,",
            file=sys.stderr
        )
        print(
            f"then review bc-config-resolved.csv and re-run with mode: run",
            file=sys.stderr
        )
        sys.exit(1)


def generate_manifest(configs_path, analysis_root, staged_root,
                      workspace, output_path):
    with open(configs_path) as f:
        configs = json.load(f)

    manifest = {"rows": []}

    for row_idx, entry in enumerate(configs):
        row_num     = row_idx + 1
        client_name = entry["clone_url"].split("/")[-1].replace(".git", "")
        client_dir          = Path(workspace) / "clients" / f"{client_name}_{row_idx}"
        _pre_worktree       = Path(workspace) / "clients" / f"{client_name}_{row_idx}_pre"
        # Use the worktree created by run_static_analysis if it exists.
        # If not, client_dir is already at the pre state (old_version in pom.xml)
        # and Dockerfile.breaking will override to new_version.
        pre_client_dir      = _pre_worktree if _pre_worktree.exists() else client_dir

        # validate required user-confirmed fields before doing anything
        validate_required_fields(entry, row_num)

        testing_framework = entry["testing_framework"]
        test_source_root  = entry["test_source_root"]
        llm_tests_folder  = entry["llm_tests_folder"]

        print(f"\nrow {row_num}: {entry['client_github_url']}", file=sys.stderr)
        print(f"  testing_framework : {testing_framework}", file=sys.stderr)
        print(f"  test_source_root  : {test_source_root}", file=sys.stderr)
        print(f"  llm_tests_folder  : {llm_tests_folder}", file=sys.stderr)

        # find spoon output files
        usage_files = find_usage_files(analysis_root, row_num)
        if not usage_files:
            print(f"row {row_num}: no usage files found", file=sys.stderr)
            sys.exit(1)

        # load all usage blocks
        all_usage_blocks = []
        for uf in sorted(usage_files):
            with open(uf) as f:
                data = json.load(f)
            if isinstance(data, list):
                all_usage_blocks.extend(data)
            else:
                all_usage_blocks.append(data)

        print(
            f"  usage blocks      : {len(all_usage_blocks)}",
            file=sys.stderr
        )

        row_entry = {
            "row_index":          row_num,
            "client_name":        client_name,
            "client_dir":         str(client_dir),
            "pre_client_dir":     str(pre_client_dir),
            "client_github_url":  entry["client_github_url"],
            "library_name":       entry["library_name"],
            "library_group_id":   entry["library_group_id"],
            "old_version":        entry["old_version"],
            "new_version":        entry["new_version"],
            "java_version":       entry["java_version"],
            "build_tool":         entry["build_tool"],
            "build_tool_version": entry["build_tool_version"],
            "testing_framework":  testing_framework,
            "test_source_root":   test_source_root,
            "llm_tests_folder":   llm_tests_folder,
            "classes":            []
        }

        classes_seen  = {}
        class_counter = 0
        class_entries = {}

        for block in all_usage_blocks:
            client_class_fqn = block.get("clientClass", "")
            file_path        = block.get("filePath", "")
            method_name      = block.get("methodName", "")

            if not client_class_fqn:
                print(
                    f"row {row_num}: skipping block with no clientClass",
                    file=sys.stderr
                )
                continue

            if client_class_fqn not in classes_seen:
                classes_seen[client_class_fqn] = class_counter
                class_counter += 1

                package_name      = extract_package_from_fqn(client_class_fqn)
                test_package_name = f"{package_name}.llmtests"

                class_entry = {
                    "class_fqn":         client_class_fqn,
                    "package_name":      package_name,
                    "test_package_name": test_package_name,
                    "class_index":       classes_seen[client_class_fqn],
                    "usage_blocks":      []
                }
                row_entry["classes"].append(class_entry)
                class_entries[client_class_fqn] = class_entry
            else:
                class_entry       = class_entries[client_class_fqn]
                package_name      = class_entry["package_name"]
                test_package_name = class_entry["test_package_name"]

            class_idx   = classes_seen[client_class_fqn]
            usage_idx   = len(class_entry["usage_blocks"])
            pkg_path    = package_to_path(test_package_name)

            test_class_name = f"R{row_num}C{class_idx}U{usage_idx}BCDetectorTest"
            test_fqn        = f"{test_package_name}.{test_class_name}"

            staged_path = str(
                Path(staged_root) / f"staged_{row_idx}" /
                pkg_path / f"{test_class_name}.java"
            )
            transplant_path = str(
                Path(test_source_root) /
                pkg_path / f"{test_class_name}.java"
            )

            focal_class_source = read_focal_class_source(client_dir, file_path)

            usage_block_entry = {
                "test_class_name":    test_class_name,
                "test_fqn":           test_fqn,
                "method_name":        method_name,
                "method_source":      block.get("methodSource", ""),
                "focal_class_source": focal_class_source,
                "library_usages":     block.get("libraryUsages", []),
                "staged_path":        staged_path,
                "transplant_path":    transplant_path,
                "analysis_block":     block
            }

            class_entry["usage_blocks"].append(usage_block_entry)

            print(
                f"  R{row_num}C{class_idx}U{usage_idx} "
                f"-> {client_class_fqn}.{method_name} "
                f"-> {test_class_name}",
                file=sys.stderr
            )

        manifest["rows"].append(row_entry)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)

    total_blocks = sum(
        len(c["usage_blocks"])
        for r in manifest["rows"]
        for c in r["classes"]
    )
    print(f"\nmanifest generated", file=sys.stderr)
    print(f"  rows          : {len(manifest['rows'])}", file=sys.stderr)
    print(
        f"  total classes : {sum(len(r['classes']) for r in manifest['rows'])}",
        file=sys.stderr
    )
    print(f"  total tests   : {total_blocks}", file=sys.stderr)
    print(f"  written to    : {output_path}", file=sys.stderr)


if __name__ == "__main__":
    configs_path  = sys.argv[1]
    analysis_root = sys.argv[2]
    staged_root   = sys.argv[3]
    workspace     = sys.argv[4]
    output_path   = sys.argv[5]

    generate_manifest(configs_path, analysis_root, staged_root,
                      workspace, output_path)