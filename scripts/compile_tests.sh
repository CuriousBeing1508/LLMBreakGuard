#!/bin/bash
# compile_tests.sh
#
# PURPOSE:
#   Compiles each generated test file independently using javac.
#   Called inside both pre and breaking containers.
#   Writes compile_results.json listing which files compiled
#   successfully and which failed with their error.
#
# DESIGN DECISIONS:
#   1. JAVAC PER FILE NOT MVN TEST-COMPILE
#      Each test file is compiled independently using javac directly.
#      Reason: mvn test-compile compiles all files together. One bad
#      file fails the entire build and you lose information about the
#      other files. javac per file gives independent pass/fail.
#
#   2. CLASSPATH FROM PRE-RESOLVED FILE
#      Reads /project/test-classpath.txt written during image build.
#      Reason: resolving classpath via Maven per file is slow. The
#      classpath does not change between files so resolving once and
#      reusing is correct and fast.
#
#   3. COMPILED CLASSES WRITTEN TO target/test-classes
#      Each successfully compiled file writes its .class file to
#      the standard Maven test-classes directory.
#      Reason: execute_tests.sh uses mvn surefire:test which expects
#      compiled test classes in target/test-classes.
#
#   4. RESULTS WRITTEN AS JSON
#      compile_results.json lists compiled and failed files with
#      error details for failed ones.
#      Reason: execute_tests.sh and filter_tests.py read this file
#      to know which files to execute. JSON is easy to parse in
#      both shell and Python.
#
#   5. COMPILATION FAILURE OF MAIN CLASSES CHECKED FIRST
#      Reads /project/compile_status.txt written during image build.
#      If main classes failed to compile there is no point trying
#      to compile test files — exits early with a clear message.
#      Reason: test files import client classes. If client classes
#      are not compiled the test compilation will fail for the wrong
#      reason and produce misleading error messages.
#
# ARGUMENTS:
#   $1 -> path to directory containing generated test files
#         e.g. /tmp/llmbreakguard/staged_0
#   $2 -> path to write compile_results.json
#         e.g. /tmp/llmbreakguard/results_0/compile_results.json

source /root/.sdkman/bin/sdkman-init.sh

STAGED_DIR=$1
RESULTS_FILE=$2

mkdir -p "$(dirname $RESULTS_FILE)"
mkdir -p /project/target/test-classes

# check if main classes compiled successfully
if [ -f /project/compile_status.txt ]; then
    STATUS=$(cat /project/compile_status.txt)
    if [ "$STATUS" = "failed" ]; then
        echo "main class compilation failed during image build"
        echo "this indicates a breaking change at the source level"
        python3 -c "
import json
result = {
    'main_compile_status': 'failed',
    'compiled': [],
    'failed': [],
    'summary': {
        'total': 0,
        'compiled': 0,
        'failed': 0,
        'main_compile_failed': True
    }
}
with open('$RESULTS_FILE', 'w') as f:
    json.dump(result, f, indent=2)
print('wrote compile results')
"
        exit 0
    fi
fi

# read pre-resolved classpath
CLASSPATH=$(cat /project/test-classpath.txt)
CLASSPATH="/project/target/classes:/project/target/test-classes:${CLASSPATH}"

echo "compiling test files from $STAGED_DIR"

# find all generated test java files
TEST_FILES=$(find $STAGED_DIR -name "*BCDetectorTest.java" 2>/dev/null)

if [ -z "$TEST_FILES" ]; then
    echo "no BCDetectorTest files found in $STAGED_DIR"
    python3 -c "
import json
result = {
    'main_compile_status': 'passed',
    'compiled': [],
    'failed': [],
    'summary': {
        'total': 0,
        'compiled': 0,
        'failed': 0,
        'main_compile_failed': False
    }
}
with open('$RESULTS_FILE', 'w') as f:
    json.dump(result, f, indent=2)
"
    exit 0
fi

COMPILED=()
FAILED=()
FAILED_ERRORS=()

for TEST_FILE in $TEST_FILES; do
    CLASS_NAME=$(basename $TEST_FILE .java)
    echo "compiling $CLASS_NAME"

    # compile independently — pass or fail does not affect other files
    OUTPUT=$(javac \
        -cp "$CLASSPATH" \
        -d /project/target/test-classes \
        "$TEST_FILE" 2>&1)

    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "  $CLASS_NAME -> compiled"
        COMPILED+=("$TEST_FILE")
    else
        echo "  $CLASS_NAME -> failed"
        echo "  error: $OUTPUT"
        FAILED+=("$TEST_FILE")
        FAILED_ERRORS+=("$OUTPUT")
    fi
done

echo ""
echo "compile results: ${#COMPILED[@]} compiled, ${#FAILED[@]} failed"

# write results as JSON
python3 -c "
import json, sys

compiled      = sys.argv[1].split('|') if sys.argv[1] else []
failed        = sys.argv[2].split('|') if sys.argv[2] else []
failed_errors = sys.argv[3].split('|') if sys.argv[3] else []

compiled = [f for f in compiled if f]
failed   = [f for f in failed   if f]

failed_details = []
for i, f in enumerate(failed):
    failed_details.append({
        'file':  f,
        'error': failed_errors[i] if i < len(failed_errors) else ''
    })

result = {
    'main_compile_status': 'passed',
    'compiled': compiled,
    'failed':   failed_details,
    'summary': {
        'total':    len(compiled) + len(failed),
        'compiled': len(compiled),
        'failed':   len(failed),
        'main_compile_failed': False
    }
}

with open('$RESULTS_FILE', 'w') as f:
    json.dump(result, f, indent=2)

print(f'compile results written to $RESULTS_FILE')
" \
"$(IFS='|'; echo "${COMPILED[*]}")" \
"$(IFS='|'; echo "${FAILED[*]}")" \
"$(IFS='|'; echo "${FAILED_ERRORS[*]}")"