"""
transplant_tests.py

PURPOSE:
    Copies LLM-generated test files from the staged directory into
    the correct package path inside the cloned client repo.
    Also writes fqn_map.json which maps each transplanted file to
    its fully qualified class name so execute_tests.sh does not
    need to derive FQNs from file paths.

DESIGN DECISIONS:
    1. STAGED FILES CLEANED IN PLACE BEFORE TRANSPLANTING
       Markdown fences and explanation text from LLM output are
       stripped from staged files before transplanting.
       The cleaned content is written back to the staged file too
       so compile_tests.sh which mounts the staged directory gets
       clean Java files.
       Reason: prompt_llm.py saves raw LLM output as-is. Cleaning
       happens here as a single central point so both the staged
       copy and the transplanted copy are always clean.

    2. FQN MAP WRITTEN ALONGSIDE TRANSPLANTED FILES
       fqn_map.json maps filename to FQN:
         {
           "R1C0U0BCDetectorTest.java": "com.example.llmtests.R1C0U0BCDetectorTest"
         }
       Reason: FQNs are already known from the manifest. Deriving
       them from file paths in shell is fragile. Writing them once
       here means execute_tests.sh just reads the map.

    3. PACKAGE DECLARATION VERIFIED AND FIXED BEFORE COPY
       Every file is checked for the correct package declaration.
       Reason: LLM sometimes writes the wrong package or omits it.
       A wrong package declaration causes compilation failure since
       Java requires the package declaration to match the directory
       structure exactly.

    4. CLASS NAME VERIFIED BEFORE COPY
       The generated class name is checked against the manifest.
       Reason: LLM sometimes generates a different class name.
       A mismatched class name causes compilation failure.

    5. DESTINATION DIRECTORY CREATED IF NOT EXISTS
       The full package path under test_source_root is created
       if it does not exist.
       Reason: the client repo may not have existing tests under
       this package.

    6. CLASH DETECTION
       If a file already exists at the destination it is not
       overwritten. The clash is recorded in the output.
       Reason: BCDetectorTest suffix makes clashes very unlikely
       but not impossible. Silently overwriting could corrupt the
       client test suite.
"""

import sys
import json
import shutil
import re
from pathlib import Path


def strip_markdown_fences(content):
    """
    Removes markdown code fences and any explanatory text before
    the first ```java fence from LLM output.
    Keeps only the raw Java code inside the fences.
    """
    lines       = content.splitlines()
    result      = []
    in_fence    = False
    found_fence = False

    # first pass: look for ```java fence
    for line in lines:
        stripped = line.strip()

        if not in_fence and stripped.lower().startswith("```java"):
            in_fence    = True
            found_fence = True
            continue

        if in_fence and stripped == "```":
            in_fence = False
            continue

        if in_fence:
            result.append(line)

    # second pass: if no ```java found try generic ``` fence
    if not found_fence:
        in_fence = False
        for line in lines:
            stripped = line.strip()

            if not in_fence and stripped == "```":
                in_fence    = True
                found_fence = True
                continue

            if in_fence and stripped == "```":
                in_fence = False
                continue

            if in_fence:
                result.append(line)

    # if still no fence found return content as-is
    # LLM may have returned raw Java without fences
    if not found_fence:
        return content.strip()

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
        print(f"  no package declaration found, injecting: {expected_decl}",
              file=sys.stderr)
        content = expected_decl + "\n\n" + content
        return content, True

    actual = lines[pkg_line_idx].strip()
    if actual != expected_decl:
        print(f"  wrong package declaration", file=sys.stderr)
        print(f"    expected : {expected_decl}", file=sys.stderr)
        print(f"    found    : {actual}", file=sys.stderr)
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
        print(f"  no public class declaration found in {file_path}",
              file=sys.stderr)
        return content, False

    actual = match.group(1)
    if actual != expected_class_name:
        print(f"  wrong class name", file=sys.stderr)
        print(f"    expected : {expected_class_name}", file=sys.stderr)
        print(f"    found    : {actual}", file=sys.stderr)
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

                print(f"row {row_num} | {test_class_name}", file=sys.stderr)

                # staged file must exist
                if not staged_path.exists():
                    print(f"  staged file not found: {staged_path}",
                          file=sys.stderr)
                    failed.append({
                        "test_class_name": test_class_name,
                        "reason":          "staged file not found",
                        "staged_path":     str(staged_path)
                    })
                    continue

                # read staged content
                content = staged_path.read_text(
                    encoding="utf-8", errors="replace"
                )

                # strip markdown fences and explanation text
                content = strip_markdown_fences(content)

                # verify and fix package declaration
                content, pkg_fixed = verify_and_fix_package(
                    content, test_package_name, staged_path
                )

                # verify and fix class name
                content, cls_fixed = verify_class_name(
                    content, test_class_name, staged_path
                )

                # write cleaned content back to staged file
                # so compile_tests.sh gets clean Java when
                # mounting the staged directory
                staged_path.write_text(content, encoding="utf-8")

                # clash detection at transplant destination
                if transplant_path.exists():
                    print(f"  clash at destination: {transplant_path}",
                          file=sys.stderr)
                    clashes.append({
                        "test_class_name": test_class_name,
                        "transplant_path": str(transplant_path)
                    })
                    continue

                # create destination directory
                transplant_path.parent.mkdir(parents=True, exist_ok=True)

                # copy cleaned content to destination
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

                print(f"  transplanted -> {transplant_path}",
                      file=sys.stderr)

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

    print(f"\ntransplant complete", file=sys.stderr)
    print(f"  transplanted : {len(transplanted)}", file=sys.stderr)
    print(f"  clashes      : {len(clashes)}", file=sys.stderr)
    print(f"  failed       : {len(failed)}", file=sys.stderr)
    print(f"  fqn map      : {fqn_map_path}", file=sys.stderr)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    manifest_path = sys.argv[1]   # workspace/manifest.json
    client_dir    = sys.argv[2]   # workspace/clients/wsdoc_0
    results_dir   = sys.argv[3]   # workspace/results_0

    transplant_all(manifest_path, client_dir, results_dir)