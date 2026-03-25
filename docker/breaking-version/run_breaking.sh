#!/bin/bash
set -e

source /root/.sdkman/bin/sdkman-init.sh

sdk use java ${JAVA_VERSION}.0.9-tem 2>/dev/null || \
sdk install java ${JAVA_VERSION}-tem && sdk use java ${JAVA_VERSION}-tem

if [ "$BUILD_TOOL" = "maven" ]; then
    sdk use maven $TOOL_VERSION 2>/dev/null || \
    sdk install maven $TOOL_VERSION && sdk use maven $TOOL_VERSION
else
    sdk use gradle $TOOL_VERSION 2>/dev/null || \
    sdk install gradle $TOOL_VERSION && sdk use gradle $TOOL_VERSION
fi

echo "running tests against new version $LIBRARY_VERSION"

# load only the tests that passed in pre run
PASSING_TESTS=$(python3 -c "
import json
with open('$PASSING_TESTS_FILE') as f:
    data = json.load(f)
print(','.join(data['passing_tests']))
")

if [ -z "$PASSING_TESTS" ]; then
    echo "no passing tests from pre run, nothing to run"
    echo '{"library_version":"'$LIBRARY_VERSION'","passed":[],"failed":[]}' \
        > $RESULTS_FILE
    exit 0
fi

echo "running ${#PASSING_TESTS} test(s) that passed on old version"

# override library version
if [ "$BUILD_TOOL" = "maven" ]; then
    mvn versions:use-dep-version \
        -Dincludes="${LIBRARY_GROUP}:${LIBRARY_NAME}" \
        -DdepVersion="${LIBRARY_VERSION}" \
        -DforceVersion=true \
        -f /project/pom.xml -q

    mvn test \
        -Dtest="$PASSING_TESTS" \
        -Dsurefire.failIfNoSpecifiedTests=false \
        -f /project/pom.xml \
        | tee /tmp/test_output.txt || true

elif [ "$BUILD_TOOL" = "gradle" ]; then
    gradle test \
        $(echo $PASSING_TESTS | tr ',' '\n' | sed 's/^/--tests /') \
        -p /project \
        | tee /tmp/test_output.txt || true
fi

python3 -c "
import re, json

with open('/tmp/test_output.txt') as f:
    output = f.read()

passed = re.findall(r'Tests run:.*?(\w+\.\w+BCDetectorTest).*?PASSED', output)
failed = re.findall(r'Tests run:.*?(\w+\.\w+BCDetectorTest).*?FAILED', output)
errors = re.findall(r'Tests run:.*?(\w+\.\w+BCDetectorTest).*?ERROR',  output)

results = {
    'library_version': '$LIBRARY_VERSION',
    'passed': passed,
    'failed': failed + errors,
    'raw_output': output
}

with open('$RESULTS_FILE', 'w') as f:
    json.dump(results, f, indent=2)

print(f'breaking run complete: {len(passed)} passed, {len(failed)+len(errors)} failed')
"