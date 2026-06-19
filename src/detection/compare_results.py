"""
compare_results.py

PURPOSE:
    Compares pre_results.json and breaking_results.json to detect
    breaking changes. A breaking change is confirmed when a test
    method passed on the pre version but fails on the breaking
    version of the library.

DESIGN DECISIONS:
    1. COMPARISON AT METHOD LEVEL NOT CLASS LEVEL
       Comparison happens per individual @Test method not per class.
       Reason: one test class may have multiple methods testing
       different API calls. Method A may detect a BC while method B
       does not. Class-level comparison would lose this granularity
       and produce imprecise reports.

    2. FOUR RESULT CATEGORIES PER METHOD
       Each method falls into exactly one category:
         a. breaking_change: passed on pre, failed on breaking
            -> definitive BC signal
         b. no_change: passed on pre, passed on breaking
            -> no BC for this API call
         c. pre_only_failure: failed on pre, not run on breaking
            -> bad test or already broken before bump
            -> excluded by filter_tests.py, not a BC signal
         d. compilation_bc: compiled on pre, failed to compile
            on breaking -> library API change broke compilation
       Reason: clear categories make the report actionable.

    3. PER FILE COMPILATION BC
       If a test file compiled successfully against the old library
       version but fails to compile against the new version that is
       a breaking change at the API level.
       Reason: compilation failure means the library changed or
       removed an API that the client code was using. This is the
       strongest form of BC — the code cannot even be compiled
       against the new version.
       This is detected by comparing compile_results_pre.json and
       compile_results_breaking.json written by compile_tests.sh.

    4. SETUP FAILURE ON BREAKING IS A BC SIGNAL
       If setUp passed on pre but fails on breaking that means
       the library change broke the test initialization itself.
       Reason: if setUp fails on breaking no tests in that class
       can run — the library change made the class unusable.

    5. FILTER AWARE COMPARISON
       compare_results.py reads passing_tests.json written by
       filter_tests.py to know exactly which classes and methods
       were carried forward to the breaking stage.
       Classes excluded by the filter are skipped entirely.
       Methods excluded by the filter are skipped entirely.
       Reason: without this awareness excluded classes appear as
       breaking changes because their methods passed on pre but
       are absent from breaking results — giving false positives.

    6. COMPILATION BC IS STRONGEST SIGNAL
       If breaking image main compile failed this is reported
       as a compilation_bc before any method-level comparison.
       Reason: if the client code itself cannot compile against
       the new library version that is the most severe form of
       breaking change. All methods are implicitly broken.

    7. VERDICT PER ROW NOT JUST OVERALL
       Each config row gets its own verdict:
         BREAKING CHANGES DETECTED
         NO BREAKING CHANGES
         INCONCLUSIVE

    8. INCONCLUSIVE WHEN NO VALID TESTS
       If no tests passed on pre and no compilation BC was
       detected the verdict is INCONCLUSIVE not NO BREAKING CHANGES.
       Reason: absence of evidence is not evidence of absence.

    9. OUTPUTS BC COUNT TO STDOUT
       The number of breaking changes is printed to stdout so
       entrypoint.sh can capture it and write to GITHUB_OUTPUT.
       All other output goes to the JSON report file.
"""

import sys
import json
from pathlib import Path


LIFECYCLE_METHODS = {
    "setUp", "tearDown", "beforeMethod", "afterMethod",
    "beforeClass", "afterClass", "beforeTest", "afterTest",
    "before", "after", "init", "cleanup"
}


def is_test_method(method_name):
    if method_name in LIFECYCLE_METHODS:
        return False
    if not method_name.startswith("test"):
        return False
    return True


def detect_per_file_compilation_bc(results_dir, library, old_v, new_v):
    """
    Compares compile_results_pre.json and compile_results_breaking.json
    to find test files that compiled on pre but failed on breaking.
    These are per-file compilation breaking changes.
    """
    pre_compile_path      = Path(results_dir) / "compile_results_pre.json"
    breaking_compile_path = Path(results_dir) / "compile_results_breaking.json"

    per_file_bc = []

    if not pre_compile_path.exists() or not breaking_compile_path.exists():
        return per_file_bc

    with open(pre_compile_path) as f:
        pre_compile = json.load(f)
    with open(breaking_compile_path) as f:
        breaking_compile = json.load(f)

    pre_compiled = set(
        Path(f).name for f in pre_compile.get("compiled", [])
    )

    for failed in breaking_compile.get("failed", []):
        fname = Path(failed["file"]).name
        if fname in pre_compiled:
            per_file_bc.append({
                "file":  fname,
                "error": failed.get("error", "")
            })
            print(
                f"  compilation bc: {fname} compiled on {old_v} "
                f"but failed on {new_v}",
                file=sys.stderr
            )

    return per_file_bc


def compare_results(pre_results_path, breaking_results_path,
                    manifest_path, output_path,
                    passing_tests_path=None):

    with open(pre_results_path) as f:
        pre_results = json.load(f)

    with open(breaking_results_path) as f:
        breaking_results = json.load(f)

    with open(manifest_path) as f:
        manifest = json.load(f)

    # load filter output so we know which classes and methods
    # were actually carried forward to the breaking stage
    passing_classes     = None
    passing_methods_map = None
    if passing_tests_path and Path(passing_tests_path).exists():
        with open(passing_tests_path) as f:
            passing_tests = json.load(f)
        passing_classes     = set(passing_tests.get("passing_classes", []))
        passing_methods_map = passing_tests.get("passing_methods", {})
        print(
            f"loaded filter: {len(passing_classes)} class(es) carried forward",
            file=sys.stderr
        )

    results_dir = Path(pre_results_path).parent
    report      = {"rows": []}
    total_bc    = 0

    for row_entry in manifest.get("rows", []):
        row_num = row_entry["row_index"]
        library = row_entry["library_name"]
        old_v   = row_entry["old_version"]
        new_v   = row_entry["new_version"]

        row_report = {
            "row_index":   row_num,
            "library":     library,
            "old_version": old_v,
            "new_version": new_v,
            "verdict":     "",
            "results": {
                "breaking_change":  [],
                "no_change":        [],
                "compilation_bc":   [],
                "pre_only_failure": [],
                "setup_bc":         []
            },
            "summary": {
                "breaking_changes":     0,
                "no_change":            0,
                "compilation_bc":       0,
                "pre_only_failure":     0,
                "setup_bc":             0,
                "total_methods_tested": 0
            }
        }

        # handle main compilation BC on breaking image
        if breaking_results.get("main_compile_failed", False):
            row_report["verdict"] = "BREAKING CHANGES DETECTED"
            row_report["results"]["compilation_bc"].append({
                "description": (
                    f"client code fails to compile against "
                    f"{library} {new_v}. "
                    f"the new version introduced an incompatible "
                    f"api change at the source level."
                )
            })
            row_report["summary"]["compilation_bc"] = 1
            total_bc += 1
            report["rows"].append(row_report)
            continue

        # detect per-file compilation BC
        # test files that compiled on pre but failed on breaking
        print(f"\nchecking per-file compilation BC", file=sys.stderr)
        per_file_bc = detect_per_file_compilation_bc(
            results_dir, library, old_v, new_v
        )
        for item in per_file_bc:
            row_report["results"]["compilation_bc"].append({
                "description": (
                    f"test file {item['file']} compiled against "
                    f"{library} {old_v} (pre) but failed to compile "
                    f"against {library} {new_v} (breaking). "
                    f"error: {item['error'][:300]}"
                )
            })
            row_report["summary"]["compilation_bc"] += 1
            total_bc += 1

        # build lookup of breaking results by class and method
        breaking_lookup       = {}
        breaking_setup_status = {}

        for cls in breaking_results.get("tests", []):
            class_name = cls["class_name"]
            for method in cls.get("methods", []):
                key = f"{class_name}.{method['method_name']}"
                breaking_lookup[key] = method

            setup_methods = [
                m for m in cls.get("methods", [])
                if m["method_name"] == "setUp"
            ]
            if setup_methods:
                breaking_setup_status[class_name] = any(
                    m["status"] != "passed" for m in setup_methods
                )

        # compare per class
        for cls in pre_results.get("tests", []):
            class_name  = cls["class_name"]
            all_methods = cls.get("methods", [])

            # skip classes excluded by filter_tests.py
            if passing_classes is not None \
                    and class_name not in passing_classes:
                print(
                    f"  skipping {class_name} (excluded by filter)",
                    file=sys.stderr
                )
                continue

            # check setUp BC: passed on pre, failed on breaking
            pre_setup    = [m for m in all_methods if m["method_name"] == "setUp"]
            pre_setup_ok = all(m["status"] == "passed" for m in pre_setup) \
                           if pre_setup else True
            breaking_setup_failed = breaking_setup_status.get(class_name, False)

            if pre_setup_ok and breaking_setup_failed:
                row_report["results"]["setup_bc"].append({
                    "class":    class_name,
                    "method":   "setUp",
                    "pre":      "passed",
                    "breaking": "failed",
                    "message":  (
                        "setUp failed on new version — "
                        "library change broke test initialization"
                    )
                })
                row_report["summary"]["setup_bc"] += 1
                total_bc += 1

            # compare actual test methods
            for method in all_methods:
                method_name = method["method_name"]

                if not is_test_method(method_name):
                    continue

                # skip methods excluded by filter
                if passing_methods_map is not None:
                    allowed = passing_methods_map.get(class_name, [])
                    if method_name not in allowed:
                        continue

                key        = f"{class_name}.{method_name}"
                pre_status = method["status"]

                if pre_status != "passed":
                    row_report["results"]["pre_only_failure"].append({
                        "class":   class_name,
                        "method":  method_name,
                        "message": method.get("message", "")
                    })
                    row_report["summary"]["pre_only_failure"] += 1
                    continue

                row_report["summary"]["total_methods_tested"] += 1

                breaking_method = breaking_lookup.get(key)

                if breaking_method is None:
                    row_report["results"]["breaking_change"].append({
                        "class":    class_name,
                        "method":   method_name,
                        "pre":      "passed",
                        "breaking": "not executed",
                        "message":  "test did not run on breaking version"
                    })
                    row_report["summary"]["breaking_changes"] += 1
                    total_bc += 1

                elif breaking_method["status"] != "passed":
                    row_report["results"]["breaking_change"].append({
                        "class":    class_name,
                        "method":   method_name,
                        "pre":      "passed",
                        "breaking": breaking_method["status"],
                        "message":  breaking_method.get("message", "")
                    })
                    row_report["summary"]["breaking_changes"] += 1
                    total_bc += 1

                else:
                    row_report["results"]["no_change"].append({
                        "class":  class_name,
                        "method": method_name
                    })
                    row_report["summary"]["no_change"] += 1

        # verdict includes all BC types
        bc_count = (
            row_report["summary"]["breaking_changes"] +
            row_report["summary"]["setup_bc"] +
            row_report["summary"]["compilation_bc"]
        )
        no_change = row_report["summary"]["no_change"]
        tested    = row_report["summary"]["total_methods_tested"]

        if bc_count > 0:
            row_report["verdict"] = "BREAKING CHANGES DETECTED"
        elif tested == 0 and bc_count == 0:
            row_report["verdict"] = "INCONCLUSIVE"
        else:
            row_report["verdict"] = "NO BREAKING CHANGES"

        print(f"row {row_num}: {library} {old_v} -> {new_v}",
              file=sys.stderr)
        print(f"  verdict          : {row_report['verdict']}",
              file=sys.stderr)
        print(f"  breaking changes : {row_report['summary']['breaking_changes']}",
              file=sys.stderr)
        print(f"  compilation bc   : {row_report['summary']['compilation_bc']}",
              file=sys.stderr)
        print(f"  setup bc         : {row_report['summary']['setup_bc']}",
              file=sys.stderr)
        print(f"  no change        : {no_change}",
              file=sys.stderr)
        print(f"  methods tested   : {tested}",
              file=sys.stderr)

        report["rows"].append(row_report)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nreport written to {output_path}", file=sys.stderr)
    print(f"total_bc={total_bc}")

    return total_bc


if __name__ == "__main__":
    pre_results_path      = sys.argv[1]
    breaking_results_path = sys.argv[2]
    manifest_path         = sys.argv[3]
    output_path           = sys.argv[4]
    passing_tests_path    = sys.argv[5] if len(sys.argv) > 5 else None

    bc_count = compare_results(
        pre_results_path,
        breaking_results_path,
        manifest_path,
        output_path,
        passing_tests_path
    )

    sys.exit(0)