#!/bin/bash
set -e

source /root/.sdkman/bin/sdkman-init.sh

# switch to required versions
sdk use java ${JAVA_VERSION}.0.9-tem 2>/dev/null || \
sdk install java ${JAVA_VERSION}-tem && sdk use java ${JAVA_VERSION}-tem

if [ "$BUILD_TOOL" = "maven" ]; then
    sdk use maven $TOOL_VERSION 2>/dev/null || \
    sdk install maven $TOOL_VERSION && sdk use maven $TOOL_VERSION
else
    sdk use gradle $TOOL_VERSION 2>/dev/null || \
    sdk install gradle $TOOL_VERSION && sdk use gradle $TOOL_VERSION
fi

echo "running tests against old version $LIBRARY_VERSION"

# override library version in pom.xml or build.gradle
if [ "$BUILD_TOOL" = "maven" ]; then
    mvn versions:use-dep-version \
        -Dincludes="${LIBRARY_GROUP}:${LIBRARY_NAME}" \
        -DdepVersion="${LIBRARY_VERSION}" \
        -DforceVersion=true \
        -f /project/pom.xml -q

    mvn test \
        -Dtest="*BCDetectorTest" \
        -Dsurefire.failIfNoSpecifiedTests=false \
        -f /project/pom.xml \
        | tee /tmp/test_output.txt || true

elif [ "$BUILD_TOOL" = "gradle" ]; then
    gradle test \
        --tests "*BCDetectorTest" \
        -p /project \
        | tee /tmp/test_output.txt || true
fi

# parse results and write passing tests list
python3 -c "
import re, json

with open('/tmp/test_output.txt') as f:
    output = f.read()

# parse individual test results
passed  = re.findall(r'Tests run:.*?(\w+\.\w+BCDetectorTest).*?PASSED', output)
failed  = re.findall(r'Tests run:.*?(\w+\.\w+BCDetectorTest).*?FAILED', output)
errors  = re.findall(r'Tests run:.*?(\w+\.\w+BCDetectorTest).*?ERROR',  output)

results = {
    'library_version': '$LIBRARY_VERSION',
    'passed':  passed,
    'failed':  failed + errors,
    'raw_output': output
}

with open('$RESULTS_FILE', 'w') as f:
    json.dump(results, f, indent=2)

print(f'pre run complete: {len(passed)} passed, {len(failed)+len(errors)} failed')
"