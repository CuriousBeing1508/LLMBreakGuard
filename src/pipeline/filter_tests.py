"""
filter_tests.py

PURPOSE:
    Reads pre_results.json and determines which test classes
    and individual test methods should be carried forward to
    the breaking stage.

DESIGN DECISIONS:
    1. CLASS LEVEL AND METHOD LEVEL FILTERING
       Only classes where at least one method passed on pre
       are carried forward. Within each class only passing
       test methods are run on breaking.

    2. SETUP FAILURE IS A CLASS-LEVEL HEALTH CHECK
       If setUp fails on pre the entire class is excluded.
       Reason: a failing setUp means the test environment
       could not be initialized — no tests in that class
       can run reliably. Running them on breaking would
       produce noise not signal.
       However if setUp passes on pre but fails on breaking
       that IS a breaking change signal and is detected by
       compare_results.py. It means the library change broke
       the test initialization itself.

    3. ONLY ACTUAL TEST METHODS IN PASSING LIST
       setUp and other lifecycle methods are excluded from
       passing_methods because surefire runs them automatically
       before each test. Including them in the surefire filter
       string causes them to be run multiple times and pollutes
       pass/fail counts.
       Test methods are identified by name starting with 'test'.
       Reason: this matches the TestNG and JUnit convention for
       test method naming and avoids false positives from
       lifecycle methods.

    4. THREE CATEGORIES OF EXCLUSION
       a. setup_failed: setUp failed on pre — bad test setup
       b. class_all_failed: all test methods failed on pre
       c. no_methods: no test methods found after filtering
       Each category is tracked separately for reporting.

    5. PASSING METHODS WRITTEN PER CLASS
       Output passing_tests.json contains per-class method lists
       so execute_tests.sh knows exactly which methods to run
       on the breaking version.

    6. MAIN COMPILE FAILURE HANDLED EXPLICITLY
       If pre_results.json indicates main compile failed nothing
       is carried forward.
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
    """
    Returns True only for actual @Test annotated methods.
    Excludes TestNG and JUnit lifecycle methods that surefire
    reports in XML alongside test results.
    Test methods are identified by name starting with 'test'.
    """
    if method_name in LIFECYCLE_METHODS:
        return False
    if not method_name.startswith("test"):
        return False
    return True


def filter_tests(pre_results_path, output_path):
    with open(pre_results_path) as f:
        pre_results = json.load(f)

    passing_classes  = []
    passing_methods  = {}
    excluded_classes = {
        "main_compile_failed": [],
        "setup_failed":        [],
        "class_all_failed":    [],
        "no_methods":          []
    }
    excluded_methods = {}

    # handle main compile failure
    if pre_results.get("main_compile_failed", False):
        print("main compilation failed on pre, nothing to carry forward",
              file=sys.stderr)
        output = {
            "passing_classes":  [],
            "passing_methods":  {},
            "excluded_classes": excluded_classes,
            "excluded_methods": excluded_methods,
            "summary": {
                "total_classes":       0,
                "passing_classes":     0,
                "excluded_classes":    0,
                "total_methods":       0,
                "passing_methods":     0,
                "excluded_methods":    0,
                "main_compile_failed": True
            }
        }
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
        return

    total_methods  = 0
    passing_count  = 0
    excluded_count = 0

    for cls in pre_results.get("tests", []):
        class_name  = cls["class_name"]
        all_methods = cls.get("methods", [])

        # check setUp health — if setUp failed on pre exclude
        # the whole class since test environment is broken
        setup_methods        = [m for m in all_methods if m["method_name"] == "setUp"]
        setup_has_failure    = any(m["status"] != "passed" for m in setup_methods)

        if setup_has_failure:
            print(f"  excluding {class_name}: setUp failed on pre",
                  file=sys.stderr)
            excluded_classes["setup_failed"].append(class_name)
            continue

        # only keep actual test methods for pass/fail analysis
        test_methods = [m for m in all_methods if is_test_method(m["method_name"])]

        if not test_methods:
            excluded_classes["no_methods"].append(class_name)
            continue

        total_methods += len(test_methods)

        passed = [m for m in test_methods if m["status"] == "passed"]
        failed = [m for m in test_methods if m["status"] != "passed"]

        # exclude class if no test methods passed on pre
        if not passed:
            excluded_classes["class_all_failed"].append(class_name)
            excluded_count += len(test_methods)
            continue

        # carry forward class with only its passing test methods
        # setUp is intentionally NOT included here — surefire
        # runs it automatically before each test
        passing_classes.append(class_name)
        passing_methods[class_name] = [m["method_name"] for m in passed]
        passing_count  += len(passed)

        if failed:
            excluded_methods[class_name] = [m["method_name"] for m in failed]
            excluded_count += len(failed)

    output = {
        "passing_classes":  passing_classes,
        "passing_methods":  passing_methods,
        "excluded_classes": excluded_classes,
        "excluded_methods": excluded_methods,
        "summary": {
            "total_classes":       len(pre_results.get("tests", [])),
            "passing_classes":     len(passing_classes),
            "excluded_classes":    sum(len(v) for v in excluded_classes.values()),
            "total_methods":       total_methods,
            "passing_methods":     passing_count,
            "excluded_methods":    excluded_count,
            "main_compile_failed": False
        }
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"filter complete", file=sys.stderr)
    print(f"  total classes      : {output['summary']['total_classes']}",
          file=sys.stderr)
    print(f"  passing classes    : {output['summary']['passing_classes']}",
          file=sys.stderr)
    print(f"  excluded classes   : {output['summary']['excluded_classes']}",
          file=sys.stderr)
    print(f"  total methods      : {output['summary']['total_methods']}",
          file=sys.stderr)
    print(f"  passing methods    : {output['summary']['passing_methods']}",
          file=sys.stderr)
    print(f"  excluded methods   : {output['summary']['excluded_methods']}",
          file=sys.stderr)
    print(f"  written to         : {output_path}", file=sys.stderr)


if __name__ == "__main__":
    pre_results_path = sys.argv[1]
    output_path      = sys.argv[2]
    filter_tests(pre_results_path, output_path)