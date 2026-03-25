"""
transplant_tests.py

PURPOSE:
    Copies LLM-generated test files from the staged directory into
    the correct package path inside the cloned client repo.
    Also writes fqn_map.json which maps each transplanted file to
    its fully qualified class name so execute_tests.sh does not
    need to derive FQNs from file paths.

DESIGN DECISIONS:
    1. FQN MAP WRITTEN ALONGSIDE TRANSPLANTED FILES
       fqn_map.json is written to the results directory after
       transplanting. It maps file path to FQN:
         {
           "R1C1U0BCDetectorTest.java": "com.example.llmtests.R1C1U0BCDetectorTest",
           ...
         }
       Reason: FQNs are already known from the manifest. Deriving
       them from file paths in shell is fragile and error-prone
       especially for nested packages. Writing them once here
       means execute_tests.sh just reads the map.

    2. PACKAGE DECLARATION VERIFIED AND FIXED BEFORE COPY
       Every file is checked for the correct package declaration
       before being copied into the client repo.
       Reason: LLM sometimes writes the wrong package or omits it
       entirely. A wrong package declaration causes compilation
       failure even if the file is in the right directory since
       Java requires the package declaration to match the directory
       structure exactly.

    3. CLASS NAME VERIFIED BEFORE COPY
       The generated class name is checked against the expected
       name from the manifest.
       Reason: LLM sometimes generates a different class name than
       instructed. A mismatched class name causes compilation
       failure since Java requires the public class name to match
       the filename.

    4. DESTINATION DIRECTORY CREATED IF NOT EXISTS
       The full package path under test_source_root is created
       if it does not exist.
       Reason: the client repo may not have any existing tests
       under this package. The directory must exist before the
       file can be copied there.

    5. CLASH DETECTION
       If a file already exists at the destination it is not
       overwritten. The clash is recorded in the output.
       Reason: BCDetectorTest suffix makes clashes very unlikely
       but not impossible. Silently overwriting an existing file
       could corrupt the client test suite.

    6. STAGED FILES PRESERVED
       Original staged files are never modified or deleted.
       Reason: staged files are the LLM output and should remain
       intact for debugging. Only copies are placed in the client
       repo.

    7. MARKDOWN CODE FENCE STRIPPING
       LLM output sometimes wraps the Java class in markdown
       code fences. These are stripped before writing.
       Reason: markdown fences cause immediate compilation failure.
       Stripping them here means the issue is handled once
       centrally rather than in every downstream script.
"""

import sys
import json
import shutil
import re
from pathlib import Path


def strip_markdown_fences(content):
    """
    Removes markdown code fences from LLM output.
    e.g. ```java ... ``` -> just the java code inside
    """
    lines  = content.splitlines()
    result = []
    in_fence = False

    for line in lines:
        stripped = line.strip()
        if not in_fence and stripped.lower().startswith("```"):
            in_fence = True
            continue
        if in_fence and stripped == "```":
            in_fence = False
            continue
        if not in_fence:
            result.append(line)

    return "\n".join(result).strip()


def verify_and_fix_package(content, expected_package, file_path):
    """
    Verifies the package declaration matches expected_package.
    Fixes it if wrong or missing.
    Returns fixed content and whether a fix was applied.
    """
    expected_decl = f"package {expected_package};"
    lines         = content.splitlines()

    pkg_line_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("package "):
            pkg_line_idx = i
            break

    if pkg_line_idx is None:
        print(f"  no package declaration found in {file_path}, injecting")
        content = expected_decl + "\n\n" + content
        return content, True

    actual = lines[pkg_line_idx].strip()
    if actual != expected_decl:
        print(f"  wrong package in {file_path}")
        print(f"    expected : {expected_decl}")
        print(f"    found    : {actual}")
        lines[pkg_line_idx] = expected_decl
        return "\n".join(lines), True

    return content, False


def verify_class_name(content, expected_class_name, file_path):
    """
    Checks that the public class name matches expected_class_name.
    Fixes it if wrong.
    Returns fixed content and whether a fix was applied.
    """
    pattern = r"public\s+class\s+(\w+)"
    match   = re.search(pattern, content)

    if not match:
        print(f"  no public class declaration found in {file_path}")
        return content, False

    actual = match.group(1)
    if actual != expected_class_name:
        print(f"  wrong class name in {file_path}")
        print(f"    expected : {expected_class_name}")
        print(f"    found    : {actual}")
        content = re.sub(
            r"(public\s+class\s+)\w+",
            r"\1" + expected_class_name,
            content,
            count=1
        )
        return content, True

    return content, False


def transplant_all(manifest_path, client_dir, results_dir):
    with open(manifest_path) as f:
        manifest = json.load(f)

    client_dir  = Path(client_dir)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    fqn_map      = {}
    transplanted = []
    clashes      = []
    failed       = []

    for row_entry in manifest.get("rows", []):
        row_num = row_entry["row_index"]

        for class_entry in row_entry["classes"]:

            for usage_block in class_entry["usage_blocks"]:
                test_class_name   = usage_block["test_class_name"]
                test_fqn          = usage_block["test_fqn"]
                test_package_name = class_entry["test_package_name"]
                staged_path       = Path(usage_block["staged_path"])
                transplant_path   = client_dir / usage_block["transplant_path"]

                print(f"row {row_num} | {test_class_name}")

                # staged file must exist
                if not staged_path.exists():
                    print(f"  staged file not found: {staged_path}")
                    failed.append({
                        "test_class_name": test_class_name,
                        "reason":          "staged file not found",
                        "staged_path":     str(staged_path)
                    })
                    continue

                # clash detection
                if transplant_path.exists():
                    print(f"  clash at destination: {transplant_path}")
                    clashes.append({
                        "test_class_name": test_class_name,
                        "transplant_path": str(transplant_path)
                    })
                    continue

                # read staged content
                content = staged_path.read_text(encoding="utf-8", errors="replace")

                # strip markdown fences
                content = strip_markdown_fences(content)

                # verify and fix package declaration
                content, pkg_fixed = verify_and_fix_package(
                    content, test_package_name, staged_path
                )

                # verify and fix class name
                content, cls_fixed = verify_class_name(
                    content, test_class_name, staged_path
                )

                # create destination directory
                transplant_path.parent.mkdir(parents=True, exist_ok=True)

                # write fixed content to destination
                transplant_path.write_text(content, encoding="utf-8")

                # record in fqn map
                fqn_map[f"{test_class_name}.java"] = test_fqn

                transplanted.append({
                    "test_class_name": test_class_name,
                    "test_fqn":        test_fqn,
                    "transplant_path": str(transplant_path),
                    "pkg_fixed":       pkg_fixed,
                    "cls_fixed":       cls_fixed
                })

                print(f"  transplanted -> {transplant_path}")

    # write fqn map for execute_tests.sh
    fqn_map_path = results_dir / "fqn_map.json"
    with open(fqn_map_path, "w") as f:
        json.dump(fqn_map, f, indent=2)

    # write transplant report
    report = {
        "transplanted": transplanted,
        "clashes":      clashes,
        "failed":       failed,
        "summary": {
            "transplanted": len(transplanted),
            "clashes":      len(clashes),
            "failed":       len(failed)
        }
    }
    report_path = results_dir / "transplant_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\ntransplant complete")
    print(f"  transplanted : {len(transplanted)}")
    print(f"  clashes      : {len(clashes)}")
    print(f"  failed       : {len(failed)}")
    print(f"  fqn map      : {fqn_map_path}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    manifest_path = sys.argv[1]   # /tmp/llmbreakguard/manifest.json
    client_dir    = sys.argv[2]   # $GITHUB_WORKSPACE/clients/client_0
    results_dir   = sys.argv[3]   # /tmp/llmbreakguard/results_0

    transplant_all(manifest_path, client_dir, results_dir)