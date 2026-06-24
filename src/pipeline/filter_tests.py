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

    2. SETUP FAILURE IS A CLASS-LEVEL HEALTH CHECK — USES all() NOT any()
       If setUp ALWAYS fails on pre the entire class is excluded.
       Reason: a setUp that fails on every single invocation means the
       test environment could not be initialized at all — no tests in
       that class can run reliably.
       However a single transient setUp failure (e.g. a JVM class-loading
       error that only fires on the first invocation of an older library
       version) must not discard a class whose test methods all passed.
       Using all() instead of any() correctly handles the transient case.
       If setUp passes on pre but fails on breaking that IS a breaking
       change signal and is detected by compare_results.py.

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
       a. setup_failed: setUp always failed on pre — bad test setup
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

    7. STAGED FILE CLEANUP WHEN staged_dir IS PROVIDED
       If a staged_dir is passed as a third argument, excluded
       test methods are physically removed from the staged .java
       files so the breaking-stage test class only contains
       methods that are known to work on the pre version.
       Reason: keeps the staged file in sync with passing_methods
       and avoids shipping dead test code to the breaking stage.
"""

import re
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


def _find_staged_file(staged_dir, class_name):
    """
    Recursively searches staged_dir for {class_name}.java.
    Returns the Path if found, else None.
    """
    for p in Path(staged_dir).rglob(f"{class_name}.java"):
        return p
    return None


def remove_methods_from_staged(staged_path, methods_to_remove):
    """
    Removes specified test methods (and their preceding annotations)
    from a staged Java source file in place.

    Approach: line-by-line state machine.
      - Accumulate consecutive '@...' annotation lines into a buffer.
      - When the next non-annotation line is a method signature matching
        a name in methods_to_remove: discard the buffer and skip the
        method body (brace-depth counting).
      - Otherwise: flush the buffer to output and keep the line.

    Limitations: simple brace counting — string/char literals containing
    { or } could confuse the parser, but LLM-generated test code rarely
    has that pattern.
    """
    if not methods_to_remove:
        return

    content      = staged_path.read_text(encoding="utf-8")
    lines        = content.split("\n")
    output       = []
    remove_set   = set(methods_to_remove)
    i            = 0

    while i < len(lines):
        # Accumulate annotation lines (and blank lines between annotations)
        # into a buffer until we see a non-annotation line.
        annotation_buf = []
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped.startswith("@") or stripped == "":
                annotation_buf.append(lines[i])
                i += 1
            else:
                break

        if i >= len(lines):
            output.extend(annotation_buf)
            break

        line     = lines[i]
        stripped = line.strip()

        # Check if this line is a method declaration we should remove.
        # Pattern: [modifiers] returnType methodName(
        method_to_remove = None
        for name in remove_set:
            if re.search(r"\b" + re.escape(name) + r"\s*\(", stripped):
                method_to_remove = name
                break

        if method_to_remove:
            # Discard the buffered annotations — do not write them to output.
            # Skip past the method body by counting braces.
            brace_depth = 0
            found_open  = False
            while i < len(lines):
                for ch in lines[i]:
                    if ch == "{":
                        brace_depth += 1
                        found_open   = True
                    elif ch == "}":
                        brace_depth -= 1
                if found_open and brace_depth == 0:
                    i += 1
                    break
                i += 1
            print(
                f"  removed method {method_to_remove} from {staged_path.name}",
                file=sys.stderr
            )
        else:
            # Keep this block.
            output.extend(annotation_buf)
            output.append(line)
            i += 1

    # Remove trailing blank lines that accumulate after removal.
    while output and output[-1].strip() == "":
        output.pop()
    output.append("")   # single trailing newline

    staged_path.write_text("\n".join(output), encoding="utf-8")


def filter_tests(pre_results_path, output_path, staged_dir=None):
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

        # check setUp health — only exclude the class if setUp ALWAYS fails,
        # meaning no test in the class could ever initialize successfully.
        # a single transient setUp failure (e.g. a NoClassDefFoundError on the
        # first JVM class-load that is cached and not repeated) must not discard
        # a class whose test methods all passed. using all() instead of any()
        # preserves classes with at least one successful setUp invocation.
        setup_methods       = [m for m in all_methods if m["method_name"] == "setUp"]
        setup_always_failed = bool(setup_methods) and all(
            m["status"] != "passed" for m in setup_methods
        )

        if setup_always_failed:
            print(f"  excluding {class_name}: setUp always failed on pre",
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
            failed_names = [m["method_name"] for m in failed]
            excluded_methods[class_name] = failed_names
            excluded_count += len(failed)

            # remove the failing methods from the staged .java file so the
            # breaking-stage test class only contains methods known to pass
            if staged_dir:
                staged_path = _find_staged_file(staged_dir, class_name)
                if staged_path:
                    remove_methods_from_staged(staged_path, failed_names)
                else:
                    print(
                        f"  warning: staged file for {class_name} not found"
                        f" under {staged_dir}, skipping cleanup",
                        file=sys.stderr
                    )

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
    staged_dir       = sys.argv[3] if len(sys.argv) > 3 else None
    filter_tests(pre_results_path, output_path, staged_dir)
