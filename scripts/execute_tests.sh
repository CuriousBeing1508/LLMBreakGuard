#!/bin/bash
# execute_tests.sh
#
# PURPOSE:
#   Executes compiled BCDetectorTest files using Maven Surefire.
#   Called inside both pre and breaking containers.
#   On pre: runs all compiled tests, writes pre_results.json.
#   On breaking: runs only tests that passed on pre (from
#   passing_tests.json), writes breaking_results.json.
#
# DESIGN DECISIONS:
#   1. FQN READ FROM fqn_map.json NOT DERIVED FROM PATH
#      FQNs are read from fqn_map.json written by transplant_tests.py
#      rather than being derived from file paths in shell.
#      Reason: deriving FQNs from paths is fragile for nested
#      packages. The manifest already has correct FQNs so using
#      them directly is reliable.
#
#   2. READS COMPILE RESULTS BEFORE EXECUTING
#      Only executes files that compiled successfully.
#      Reason: attempting to run a test whose class file does
#      not exist causes confusing surefire errors.
#
#   3. FILTERS BY PASSING TESTS ON BREAKING STAGE
#      On breaking stage reads passing_tests.json to run only
#      tests that passed on pre.
#      Reason: tests that failed on pre are not BC signals.
#
#   4. RUNS EACH TEST CLASS INDEPENDENTLY
#      Each test class is run in a separate surefire invocation.
#      Reason: surefire stops on first failure by default.
#      Independent invocations give complete results for every
#      test class.
#
#   5. INDIVIDUAL TEST METHOD RESULTS CAPTURED
#      Each @Test method result is captured from surefire XML.
#      Reason: one class may have multiple methods. A class with
#      2 passing and 1 failing method carries only the 2 passing
#      methods forward to the breaking stage.
#
#   6. SUREFIRE REPORTS CLEARED BETWEEN RUNS
#      target/surefire-reports cleared before each class run.
#      Reason: prevents stale reports from previous class being
#      picked up for current one.
#
# ARGUMENTS:
#   $1 -> compile_results.json
#   $2 -> fqn_map.json written by transplant_tests.py
#   $3 -> results output file
#   $4 -> passing_tests.json (breaking stage only, else empty string)

source /root/.sdkman/bin/sdkman-init.sh

COMPILE_RESULTS=$1
FQN_MAP=$2
RESULTS_FILE=$3
PASSING_TESTS_FILE=${4:-""}

mkdir -p "$(dirname $RESULTS_FILE)"
rm -f /tmp/class_results.json

# check if main compile failed
MAIN_COMPILE_FAILED=$(python3 -c "
import json
with open('$COMPILE_RESULTS') as f:
    d = json.load(f)
print('true' if d.get('summary', {}).get('main_compile_failed', False) else 'false')
")

if [ "$MAIN_COMPILE_FAILED" = "true" ]; then
    echo "main compilation failed, skipping test execution"
    python3 -c "
import json
result = {
    'main_compile_failed': True,
    'tests': [],
    'summary': {
        'total_classes':   0,
        'total_methods':   0,
        'passed_classes':  0,
        'failed_classes':  0,
        'passed_methods':  0,
        'failed_methods':  0
    }
}
with open('$RESULTS_FILE', 'w') as f:
    json.dump(result, f, indent=2)
"
    exit 0
fi

# read compiled files
COMPILED_FILES=$(python3 -c "
import json
with open('$COMPILE_RESULTS') as f:
    d = json.load(f)
for f in d.get('compiled', []):
    print(f)
")

if [ -z "$COMPILED_FILES" ]; then
    echo "no compiled files to execute"
    python3 -c "
import json
result = {
    'main_compile_failed': False,
    'tests': [],
    'summary': {
        'total_classes':   0,
        'total_methods':   0,
        'passed_classes':  0,
        'failed_classes':  0,
        'passed_methods':  0,
        'failed_methods':  0
    }
}
with open('$RESULTS_FILE', 'w') as f:
    json.dump(result, f, indent=2)
"
    exit 0
fi

# load passing tests filter for breaking stage
PASSING_CLASSES=""
if [ -n "$PASSING_TESTS_FILE" ] && [ -f "$PASSING_TESTS_FILE" ]; then
    PASSING_CLASSES=$(python3 -c "
import json
with open('$PASSING_TESTS_FILE') as f:
    d = json.load(f)
for c in d.get('passing_classes', []):
    print(c)
")
    echo "breaking stage: running only tests that passed on pre"
fi

for TEST_FILE in $COMPILED_FILES; do
    CLASS_NAME=$(basename $TEST_FILE .java)

    # skip if not in passing list (breaking stage)
    if [ -n "$PASSING_CLASSES" ]; then
        if ! echo "$PASSING_CLASSES" | grep -q "^${CLASS_NAME}$"; then
            echo "skipping $CLASS_NAME (did not pass on pre)"
            continue
        fi
    fi

    # look up FQN from fqn_map.json written by transplant_tests.py
    FQN=$(python3 -c "
import json
with open('$FQN_MAP') as f:
    fqn_map = json.load(f)
print(fqn_map.get('${CLASS_NAME}.java', ''))
")

    if [ -z "$FQN" ]; then
        echo "fqn not found for $CLASS_NAME in $FQN_MAP, skipping"
        continue
    fi

    # for breaking stage also pass only passing methods
    # build surefire test filter including only passing methods
    TEST_FILTER="$FQN"
    if [ -n "$PASSING_TESTS_FILE" ] && [ -f "$PASSING_TESTS_FILE" ]; then
        PASSING_METHODS=$(python3 -c "
import json
with open('$PASSING_TESTS_FILE') as f:
    d = json.load(f)
methods = d.get('passing_methods', {}).get('$CLASS_NAME', [])
if methods:
    print('$FQN#' + '+'.join(methods))
else:
    print('$FQN')
")
        TEST_FILTER="$PASSING_METHODS"
    fi

    echo ""
    echo "executing $CLASS_NAME"
    echo "  fqn    : $FQN"
    echo "  filter : $TEST_FILTER"

    # clear surefire reports before each run
    rm -rf /project/target/surefire-reports
    mkdir -p /project/target/surefire-reports

    bash -c "source /root/.sdkman/bin/sdkman-init.sh \
        && mvn surefire:test \
            -Dtest=${TEST_FILTER} \
            -DfailIfNoTests=false \
            -Dpmd.skip=true \
            -Dcheckstyle.skip=true \
            -Denforcer.skip=true \
            -f /project/pom.xml" \
        > /tmp/surefire_output.txt 2>&1 || true

    # parse surefire XML into structured result
    python3 -c "
import json
import xml.etree.ElementTree as ET
from pathlib import Path

class_name = '$CLASS_NAME'
fqn        = '$FQN'

class_result = {
    'class_name': class_name,
    'fqn':        fqn,
    'methods':    [],
    'summary': {
        'passed': 0,
        'failed': 0,
        'errors': 0
    }
}

surefire_dir = Path('/project/target/surefire-reports')
xml_files    = list(surefire_dir.glob(f'TEST-*{class_name}*.xml'))

if not xml_files:
    class_result['summary']['errors'] = 1
else:
    for xml_file in xml_files:
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            for testcase in root.findall('testcase'):
                method = {
                    'method_name': testcase.get('name', ''),
                    'time':        testcase.get('time', '0'),
                    'status':      'passed',
                    'message':     ''
                }
                failure = testcase.find('failure')
                error   = testcase.find('error')
                if failure is not None:
                    method['status']  = 'failed'
                    method['message'] = failure.get('message', '')
                    class_result['summary']['failed'] += 1
                elif error is not None:
                    method['status']  = 'error'
                    method['message'] = error.get('message', '')
                    class_result['summary']['errors'] += 1
                else:
                    class_result['summary']['passed'] += 1
                class_result['methods'].append(method)
        except ET.ParseError as e:
            print(f'failed to parse {xml_file}: {e}')

with open('/tmp/class_results.json', 'a') as f:
    f.write(json.dumps(class_result) + '\n')

s = class_result['summary']
print(f\"  passed: {s['passed']}, failed: {s['failed']}, errors: {s['errors']}\")
"
done

# aggregate all results
python3 -c "
import json
from pathlib import Path

all_classes = []
results_path = Path('/tmp/class_results.json')
if results_path.exists():
    for line in results_path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                all_classes.append(json.loads(line))
            except json.JSONDecodeError:
                pass

total_methods  = sum(len(c['methods'])      for c in all_classes)
passed_methods = sum(c['summary']['passed'] for c in all_classes)
failed_methods = sum(c['summary']['failed'] + c['summary']['errors']
                     for c in all_classes)
passed_classes = sum(
    1 for c in all_classes
    if c['summary']['failed'] == 0
    and c['summary']['errors'] == 0
    and len(c['methods']) > 0
)
failed_classes = len(all_classes) - passed_classes

result = {
    'main_compile_failed': False,
    'tests': all_classes,
    'summary': {
        'total_classes':  len(all_classes),
        'total_methods':  total_methods,
        'passed_classes': passed_classes,
        'failed_classes': failed_classes,
        'passed_methods': passed_methods,
        'failed_methods': failed_methods
    }
}

with open('$RESULTS_FILE', 'w') as f:
    json.dump(result, f, indent=2)

print(f'results written to $RESULTS_FILE')
print(f'  classes : {len(all_classes)}')
print(f'  passed  : {passed_methods}')
print(f'  failed  : {failed_methods}')
"

rm -f /tmp/class_results.json /tmp/surefire_output.txt