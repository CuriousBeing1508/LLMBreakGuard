"""
generate_manifest.py

PURPOSE:
    Reads Spoon analysis output and builds a manifest.json that serves as the
    single source of truth for all downstream steps (prompt generation, LLM
    calling, transplanting, test execution, result comparison).

DESIGN DECISIONS:
    1. TEST CLASS NAMING: R{row}C{class}U{usage}BCDetectorTest
       - Row index    : which row in bc-config-resolved.csv
       - Class index  : which class Spoon found in that project
       - Usage index  : which usage block (method) within that class
       - Reason: full class+method names cause file system path length issues
                 and Maven Surefire parsing problems. Short indexed names avoid
                 this while still being unique and traceable via the manifest.

    2. TEST PACKAGE: {original_package}.llmtests
       - All LLM generated tests live in a llmtests subpackage under the
         original class package.
       - Reason: cleanly separates generated tests from existing client tests,
                 prevents name clashes, easy to target in Maven/Gradle with
                 **/llmtests/* and easy to clean up after the run.

    3. ONE USAGE BLOCK = ONE TEST CLASS FILE
       - Each method in the client class that uses the library = one usage block
         from Spoon = one prompt to LLM = one generated test class file.
       - Reason: keeps each test class focused on one focal method, making it
                 easier to identify exactly which method triggers a BC.

    4. MANIFEST STRUCTURE: rows -> classes -> usage_blocks
       - Mirrors the hierarchy: one config row -> multiple classes -> multiple
         usage blocks per class.
       - Every downstream script iterates this same hierarchy so nothing needs
         to re-derive paths or indices independently.

    5. STAGED PATH vs TRANSPLANT PATH
       - staged_path:     where LLM output is written first (outside client repo)
       - transplant_path: final destination inside the cloned client repo
       - Reason: keeping them separate means LLM output is always inspectable
                 independently of what got transplanted, useful for debugging.

    6. FULL CLASS SOURCE IN MANIFEST
       - The full focal class source code is read here and stored in the manifest
         so prompt_llm.py does not need to know about the client repo path.
       - Reason: single point of file I/O for source reading, all downstream
                 scripts work purely from the manifest.
"""

import os
import sys
import json
from pathlib import Path


def package_to_path(package_name):
    return package_name.replace(".", "/")


def read_focal_class_source(client_dir, file_path):
    """
    Reads the full source code of the focal class from the cloned repo.
    Tries direct path first, then recursive search if not found.
    """
    direct = Path(client_dir) / file_path
    if direct.exists():
        return direct.read_text(encoding="utf-8", errors="replace")

    # recursive fallback: file_path might have module prefix differences
    file_name = Path(file_path).name
    matches = [
        p for p in Path(client_dir).rglob(file_name)
        if str(p).endswith(file_path)
    ]
    if matches:
        best = min(matches, key=lambda p: len(str(p)))
        return best.read_text(encoding="utf-8", errors="replace")

    return f"// source not found for {file_path}"


def extract_package_from_fqn(client_class_fqn):
    """
    Extracts package name from fully qualified class name.
    e.g. org.versly.rest.wsdoc.AnnotationProcessor -> org.versly.rest.wsdoc
    Handles inner classes e.g. AnnotationProcessor$TypeVisitorImpl
    """
    parts = client_class_fqn.rsplit(".", 1)
    if len(parts) == 2:
        return parts[0]
    return ""


def extract_simple_name(client_class_fqn):
    """
    Extracts simple class name from FQN, sanitizing inner class separator.
    e.g. org.versly.rest.wsdoc.AnnotationProcessor$TypeVisitorImpl
      -> AnnotationProcessorTypeVisitorImpl
    """
    simple = client_class_fqn.rsplit(".", 1)[-1]
    return simple.replace("$", "")


def generate_manifest(configs_path, analysis_root, staged_root,
                      workspace, output_path):
    with open(configs_path) as f:
        configs = json.load(f)

    manifest = {"rows": []}

    for row_idx, entry in enumerate(configs):
        row_num     = row_idx + 1
        client_name = entry["clone_url"].split("/")[-1].replace(".git", "")
        client_dir  = Path(workspace) / "clients" / f"{client_name}_{row_idx}"
        analysis_dir = Path(analysis_root) / f"analysis_{row_idx}"

        if not analysis_dir.exists():
            print(f"row {row_num}: analysis directory not found at {analysis_dir}")
            sys.exit(1)

        # Spoon produces one JSON file per project containing all usage blocks
        analysis_files = list(analysis_dir.glob("*.json"))
        if not analysis_files:
            print(f"row {row_num}: no analysis files found in {analysis_dir}")
            sys.exit(1)

        # load all usage blocks from all analysis files
        all_usage_blocks = []
        for af in sorted(analysis_files):
            with open(af) as f:
                data = json.load(f)
            if isinstance(data, list):
                all_usage_blocks.extend(data)
            else:
                all_usage_blocks.append(data)

        print(f"row {row_num}: {len(all_usage_blocks)} usage block(s) found")

        # group usage blocks by clientClass so we can assign class index
        classes_seen = {}
        class_counter = 0

        row_entry = {
            "row_index":          row_num,
            "client_name":        client_name,
            "client_github_url":  entry["client_github_url"],
            "library_name":       entry["library_name"],
            "library_group_id":   entry["library_group_id"],
            "old_version":        entry["old_version"],
            "new_version":        entry["new_version"],
            "java_version":       entry["java_version"],
            "build_tool":         entry["build_tool"],
            "build_tool_version": entry["build_tool_version"],
            "testing_framework":  entry["testing_framework"],
            "test_source_root":   entry["test_source_root"],
            "llm_tests_folder":   entry["llm_tests_folder"],
            "classes":            []
        }

        # track classes already added to row_entry for appending usage blocks
        class_entries = {}

        for block in all_usage_blocks:
            client_class_fqn = block.get("clientClass", "")
            file_path        = block.get("filePath", "")
            method_name      = block.get("methodName", "")

            if not client_class_fqn:
                print(f"row {row_num}: skipping block with no clientClass")
                continue

            # assign class index if first time seeing this class
            if client_class_fqn not in classes_seen:
                classes_seen[client_class_fqn] = class_counter
                class_counter += 1

                package_name      = extract_package_from_fqn(client_class_fqn)
                test_package_name = f"{package_name}.llmtests"
                pkg_path          = package_to_path(test_package_name)

                class_entry = {
                    "class_fqn":          client_class_fqn,
                    "package_name":       package_name,
                    "test_package_name":  test_package_name,
                    "class_index":        classes_seen[client_class_fqn],
                    "usage_blocks":       []
                }
                row_entry["classes"].append(class_entry)
                class_entries[client_class_fqn] = class_entry
            else:
                class_entry   = class_entries[client_class_fqn]
                package_name  = class_entry["package_name"]
                test_package_name = class_entry["test_package_name"]
                pkg_path      = package_to_path(test_package_name)

            class_idx   = classes_seen[client_class_fqn]
            usage_idx   = len(class_entry["usage_blocks"])

            # R{row}C{class}U{usage}BCDetectorTest
            test_class_name = f"R{row_num}C{class_idx}U{usage_idx}BCDetectorTest"
            test_fqn        = f"{test_package_name}.{test_class_name}"
            pkg_path        = package_to_path(test_package_name)

            staged_path     = str(
                Path(staged_root) / f"staged_{row_idx}" /
                pkg_path / f"{test_class_name}.java"
            )
            transplant_path = str(
                Path(entry["test_source_root"]) /
                pkg_path / f"{test_class_name}.java"
            )

            # read full focal class source here so prompt_llm.py
            # does not need to access the client repo directly
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

            print(f"  R{row_num}C{class_idx}U{usage_idx} "
                  f"-> {client_class_fqn}.{method_name}"
                  f" -> {test_class_name}")

        manifest["rows"].append(row_entry)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)

    total_blocks = sum(
        len(c["usage_blocks"])
        for r in manifest["rows"]
        for c in r["classes"]
    )
    print(f"\nmanifest generated")
    print(f"  rows          : {len(manifest['rows'])}")
    print(f"  total classes : {sum(len(r['classes']) for r in manifest['rows'])}")
    print(f"  total tests   : {total_blocks}")
    print(f"  written to    : {output_path}")


if __name__ == "__main__":
    configs_path  = sys.argv[1]   # /tmp/llmbreakguard/configs.json
    analysis_root = sys.argv[2]   # /tmp/llmbreakguard/analysis
    staged_root   = sys.argv[3]   # /tmp/llmbreakguard/staged_tests
    workspace     = sys.argv[4]   # $GITHUB_WORKSPACE
    output_path   = sys.argv[5]   # /tmp/llmbreakguard/manifest.json

    generate_manifest(configs_path, analysis_root, staged_root,
                      workspace, output_path)