"""
filter_tests.py

PURPOSE:
    Reads pre_results.json produced by execute_tests.sh on the pre
    container and determines which test classes and individual test
    methods should be carried forward to the breaking stage.

DESIGN DECISIONS:
    1. CLASS LEVEL AND METHOD LEVEL FILTERING
       Filtering happens at two levels:
         - Class level: only classes where at least one method
           passed on pre are carried forward.
         - Method level: within a carried-forward class, only the
           methods that passed on pre are run on breaking.
       Reason: a class with 3 methods where 2 pass and 1 fails
       should still be tested on breaking for the 2 passing methods.
       The failing method is ignored on breaking since it was
       already failing before the bump — not a BC signal.

    2. THREE CATEGORIES OF EXCLUSION
       Tests excluded from breaking stage fall into three categories:
         a. class_compile_failed: file did not compile on pre
         b. class_all_failed: all methods failed on pre
         c. method_failed_on_pre: individual method failed on pre
       These are tracked separately in the output for reporting.
       Reason: distinguishing why a test was excluded helps the
       final report explain what happened clearly.

    3. PASSING METHODS WRITTEN PER CLASS
       Output passing_tests.json contains per-class method lists:
         {
           "passing_classes": ["R1C1U0BCDetectorTest", ...],
           "passing_methods": {
             "R1C1U0BCDetectorTest": ["test_method1", "test_method2"],
             ...
           }
         }
       Reason: execute_tests.sh on breaking needs both the class
       list (to decide which classes to run) and the method list
       (to ignore methods that failed on pre within each class).

    4. MAIN COMPILE FAILURE HANDLED EXPLICITLY
       If pre_results.json indicates main compile failed nothing
       is carried forward and the output reflects this clearly.
       Reason: if client main classes did not compile on pre there
       are no valid tests at all. The report should say this rather
       than silently producing empty results.

    5. EMPTY CLASSES EXCLUDED
       Classes with no test methods in surefire output are excluded.
       Reason: a class with no methods either did not run at all
       or produced no useful signal. Running it on breaking would
       add noise.
"""

import sys
import json
from pathlib import Path


def filter_tests(pre_results_path, output_path):
    with open(pre_results_path) as f:
        pre_results = json.load(f)

    passing_classes  = []
    passing_methods  = {}
    excluded_classes = {
        "main_compile_failed":  [],
        "class_all_failed":     [],
        "no_methods":           []
    }
    excluded_methods = {}

    # handle main compile failure
    if pre_results.get("main_compile_failed", False):
        print("main compilation failed on pre — nothing to carry forward")
        output = {
            "passing_classes":  [],
            "passing_methods":  {},
            "excluded_classes": excluded_classes,
            "excluded_methods": excluded_methods,
            "summary": {
                "total_classes":      0,
                "passing_classes":    0,
                "excluded_classes":   0,
                "total_methods":      0,
                "passing_methods":    0,
                "excluded_methods":   0,
                "main_compile_failed": True
            }
        }
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
        return

    total_methods   = 0
    passing_count   = 0
    excluded_count  = 0

    for cls in pre_results.get("tests", []):
        class_name = cls["class_name"]
        methods    = cls.get("methods", [])

        # exclude classes with no methods
        if not methods:
            excluded_classes["no_methods"].append(class_name)
            continue

        total_methods += len(methods)

        # split methods into passed and failed
        passed = [m for m in methods if m["status"] == "passed"]
        failed = [m for m in methods if m["status"] != "passed"]

        # exclude class if no methods passed on pre
        if not passed:
            excluded_classes["class_all_failed"].append(class_name)
            excluded_count += len(methods)
            continue

        # carry forward class with only its passing methods
        passing_classes.append(class_name)
        passing_methods[class_name] = [m["method_name"] for m in passed]
        passing_count += len(passed)

        # track excluded methods within carried-forward classes
        if failed:
            excluded_methods[class_name] = [m["method_name"] for m in failed]
            excluded_count += len(failed)

    output = {
        "passing_classes":  passing_classes,
        "passing_methods":  passing_methods,
        "excluded_classes": excluded_classes,
        "excluded_methods": excluded_methods,
        "summary": {
            "total_classes":      len(pre_results.get("tests", [])),
            "passing_classes":    len(passing_classes),
            "excluded_classes":   sum(
                len(v) for v in excluded_classes.values()
            ),
            "total_methods":      total_methods,
            "passing_methods":    passing_count,
            "excluded_methods":   excluded_count,
            "main_compile_failed": False
        }
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"filter complete")
    print(f"  total classes      : {output['summary']['total_classes']}")
    print(f"  passing classes    : {output['summary']['passing_classes']}")
    print(f"  excluded classes   : {output['summary']['excluded_classes']}")
    print(f"  total methods      : {output['summary']['total_methods']}")
    print(f"  passing methods    : {output['summary']['passing_methods']}")
    print(f"  excluded methods   : {output['summary']['excluded_methods']}")
    print(f"  written to         : {output_path}")


if __name__ == "__main__":
    pre_results_path = sys.argv[1]   # /tmp/llmbreakguard/results_0/pre_results.json
    output_path      = sys.argv[2]   # /tmp/llmbreakguard/results_0/passing_tests.json
    filter_tests(pre_results_path, output_path)