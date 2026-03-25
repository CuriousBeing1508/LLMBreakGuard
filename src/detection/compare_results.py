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
         d. compilation_bc: main compile failed on breaking
            -> library API change breaks compilation itself
            -> strongest BC signal
       Reason: clear categories make the report actionable.
       The user knows exactly which API calls broke and which
       are safe.

    3. COMPILATION BC IS STRONGEST SIGNAL
       If breaking image main compile failed this is reported
       as a compilation_bc before any method-level comparison.
       Reason: if the client code itself cannot compile against
       the new library version that is the most severe form of
       breaking change. All methods are implicitly broken.

    4. VERDICT PER ROW NOT JUST OVERALL
       Each config row gets its own verdict:
         BREAKING CHANGES DETECTED
         NO BREAKING CHANGES
         INCONCLUSIVE
       Reason: user may have multiple rows in bc-config.csv
       testing different libraries or projects. Per-row verdicts
       make the report clear about which project is affected.

    5. INCONCLUSIVE WHEN NO VALID TESTS
       If no tests passed on pre and no compilation BC was
       detected the verdict is INCONCLUSIVE not NO BREAKING CHANGES.
       Reason: absence of evidence is not evidence of absence.
       If all generated tests were bad we cannot conclude the
       bump is safe. The user should be warned.

    6. OUTPUTS BC COUNT TO STDOUT
       The number of breaking changes is printed to stdout so
       entrypoint.sh can capture it and write to GITHUB_OUTPUT.
       All other output goes to the JSON report file.
"""

import sys
import json
from pathlib import Path


def compare_results(pre_results_path, breaking_results_path,
                    manifest_path, output_path):

    with open(pre_results_path) as f:
        pre_results = json.load(f)

    with open(breaking_results_path) as f:
        breaking_results = json.load(f)

    with open(manifest_path) as f:
        manifest = json.load(f)

    report = {
        "rows": []
    }

    total_bc = 0

    for row_entry in manifest.get("rows", []):
        row_num  = row_entry["row_index"]
        library  = row_entry["library_name"]
        old_v    = row_entry["old_version"]
        new_v    = row_entry["new_version"]

        row_report = {
            "row_index":   row_num,
            "library":     library,
            "old_version": old_v,
            "new_version": new_v,
            "verdict":     "",
            "results":     {
                "breaking_change":     [],
                "no_change":           [],
                "compilation_bc":      [],
                "pre_only_failure":    []
            },
            "summary": {
                "breaking_changes":    0,
                "no_change":           0,
                "compilation_bc":      0,
                "pre_only_failure":    0,
                "total_methods_tested": 0
            }
        }

        # handle compilation BC on breaking image
        if breaking_results.get("main_compile_failed", False):
            row_report["verdict"] = "BREAKING CHANGES DETECTED"
            row_report["results"]["compilation_bc"].append({
                "description": (
                    f"client code fails to compile against "
                    f"{library} {new_v}. "
                    f"The new version introduced an incompatible "
                    f"API change at the source level."
                )
            })
            row_report["summary"]["compilation_bc"] = 1
            total_bc += 1
            report["rows"].append(row_report)
            continue

        # build lookup of breaking results by class and method
        breaking_lookup = {}
        for cls in breaking_results.get("tests", []):
            class_name = cls["class_name"]
            for method in cls.get("methods", []):
                key = f"{class_name}.{method['method_name']}"
                breaking_lookup[key] = method

        # compare method by method
        for cls in pre_results.get("tests", []):
            class_name = cls["class_name"]

            for method in cls.get("methods", []):
                method_name = method["method_name"]
                key         = f"{class_name}.{method_name}"
                pre_status  = method["status"]

                if pre_status != "passed":
                    # failed on pre — excluded from breaking stage
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
                    # passed on pre but not found in breaking results
                    # treat as breaking change — test could not run
                    row_report["results"]["breaking_change"].append({
                        "class":   class_name,
                        "method":  method_name,
                        "pre":     "passed",
                        "breaking": "not executed",
                        "message": "test did not run on breaking version"
                    })
                    row_report["summary"]["breaking_changes"] += 1
                    total_bc += 1

                elif breaking_method["status"] != "passed":
                    # passed on pre, failed on breaking = BC
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
                    # passed on both = no BC
                    row_report["results"]["no_change"].append({
                        "class":  class_name,
                        "method": method_name
                    })
                    row_report["summary"]["no_change"] += 1

        # determine verdict
        bc_count = row_report["summary"]["breaking_changes"]
        no_change = row_report["summary"]["no_change"]
        tested   = row_report["summary"]["total_methods_tested"]

        if bc_count > 0:
            row_report["verdict"] = "BREAKING CHANGES DETECTED"
        elif tested == 0:
            row_report["verdict"] = "INCONCLUSIVE"
        else:
            row_report["verdict"] = "NO BREAKING CHANGES"

        print(f"row {row_num}: {library} {old_v} -> {new_v}")
        print(f"  verdict          : {row_report['verdict']}")
        print(f"  breaking changes : {bc_count}")
        print(f"  no change        : {no_change}")
        print(f"  methods tested   : {tested}")

        report["rows"].append(row_report)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nreport written to {output_path}")

    # print bc count to stdout for entrypoint to capture
    print(f"total_bc={total_bc}")

    return total_bc


if __name__ == "__main__":
    pre_results_path      = sys.argv[1]
    breaking_results_path = sys.argv[2]
    manifest_path         = sys.argv[3]
    output_path           = sys.argv[4]

    bc_count = compare_results(
        pre_results_path,
        breaking_results_path,
        manifest_path,
        output_path
    )

    # exit code used by entrypoint.sh to set action status
    sys.exit(0)