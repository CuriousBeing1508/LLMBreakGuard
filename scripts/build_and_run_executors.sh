#!/bin/bash
# build_and_run_executors.sh
#
# PURPOSE:
#   Builds pre and breaking Docker images for a single config row,
#   runs compile and execute phases sequentially, and coordinates
#   the filter step between pre and breaking execution.
#   Called once per row in bc-config-resolved.csv from action.yml.
#
# DESIGN DECISIONS:
#   1. SEQUENTIAL NOT PARALLEL
#      Pre image is fully built and executed before breaking image
#      starts building.
#      Reason: filter_tests.py must run between pre execution and
#      breaking execution to determine which tests to carry forward.
#      Breaking image only runs tests that passed on pre.
#
#   2. CLIENT REPO MOUNTED NOT BAKED FOR TEST FILES
#      Generated test files are mounted into the container at
#      runtime via -v rather than baked into the image.
#      Reason: if a test file needs to be fixed and rerun the
#      image does not need to be rebuilt. Only the test file
#      changes. This saves significant time during debugging.
#
#   3. RESULTS DIRECTORY MOUNTED
#      Results directory is mounted so compile_results.json,
#      pre_results.json, breaking_results.json and fqn_map.json
#      are written directly to the host runner filesystem.
#      Reason: these files are needed by filter_tests.py and
#      compare_results.py which run on the host not inside
#      the container.
#
#   4. IMAGES TAGGED WITH ROW INDEX
#      Images tagged llmbreakguard-pre-{row} and
#      llmbreakguard-breaking-{row}.
#      Reason: multiple rows in bc-config.csv would cause name
#      conflicts without row-specific tags.
#
#   5. IMAGES REMOVED AFTER USE
#      docker rmi runs after both containers complete.
#      Reason: GitHub Actions runners have limited disk space.
#      Each image can be several GB. Removing them immediately
#      prevents disk exhaustion on multi-row configs.
#
#   6. BUILD FAILURES HANDLED GRACEFULLY
#      If the breaking image fails to build (e.g. compilation BC)
#      this is recorded and the script continues to generate a
#      report rather than exiting.
#      Reason: a build failure on the breaking image is itself
#      a BC signal and should appear in the report.
#
#   7. FQN MAP COPIED INTO CONTAINER
#      fqn_map.json written by transplant_tests.py is copied
#      into the results mount so execute_tests.sh inside the
#      container can read it.
#      Reason: execute_tests.sh needs FQNs to invoke surefire
#      correctly. The map lives on the host after transplanting
#      so it must be available inside the container via the
#      mounted results directory.
#
# ARGUMENTS:
#   $1  -> action_path        (${{ github.action_path }})
#   $2  -> client_dir         (cloned repo path)
#   $3  -> staged_dir         (LLM staged tests path)
#   $4  -> results_dir        (output directory for this row)
#   $5  -> row_index
#   $6  -> java_version
#   $7  -> maven_version
#   $8  -> library_group_id
#   $9  -> library_name
#   $10 -> old_version
#   $11 -> new_version
#   $12 -> pre_client_dir (client repo checked out at pre_breaking_commit^1)

#!/bin/bash
set -e

ACTION_PATH=$1
CLIENT_DIR=$2
STAGED_DIR=$3
RESULTS_DIR=$4
ROW_INDEX=$5
JAVA_VERSION=$6
MAVEN_VERSION=$7
LIBRARY_GROUP_ID=$8
LIBRARY_NAME=$9
OLD_VERSION=${10}
NEW_VERSION=${11}
PRE_CLIENT_DIR=${12}

PRE_IMAGE="llmbreakguard-pre-${ROW_INDEX}"
BREAKING_IMAGE="llmbreakguard-breaking-${ROW_INDEX}"

mkdir -p "$RESULTS_DIR"

# build base image once if not already built
# base contains all scripts baked in
if ! docker image inspect llmbreakguard-base:latest > /dev/null 2>&1; then
    echo "row $ROW_INDEX: building base executor image"
    docker build \
        -f $ACTION_PATH/docker/Dockerfile.base \
        -t llmbreakguard-base:latest \
        $ACTION_PATH
fi

echo "row $ROW_INDEX: building pre image ($OLD_VERSION)"
docker build \
    --build-arg JAVA_VERSION=$JAVA_VERSION \
    --build-arg MAVEN_VERSION=$MAVEN_VERSION \
    -f $ACTION_PATH/docker/pre-version/Dockerfile.pre \
    -t $PRE_IMAGE \
    $PRE_CLIENT_DIR

echo "row $ROW_INDEX: running pre stage (compile + execute)"
docker run --rm \
    -v "$STAGED_DIR:/staged:ro" \
    -v "$RESULTS_DIR:/results" \
    $PRE_IMAGE \
    bash -c "
        source /root/.sdkman/bin/sdkman-init.sh && \
        /compile_tests.sh /staged /results/compile_results_pre.json && \
        /execute_tests.sh \
            /results/compile_results_pre.json \
            /results/fqn_map.json \
            /results/pre_results.json \
            ''
    "

echo "row $ROW_INDEX: filtering passing tests"
python3 $ACTION_PATH/src/pipeline/filter_tests.py \
    "$RESULTS_DIR/pre_results.json" \
    "$RESULTS_DIR/passing_tests.json" \
    "$STAGED_DIR"

PASSING_COUNT=$(python3 -c "
import json
with open('$RESULTS_DIR/passing_tests.json') as f:
    d = json.load(f)
print(d['summary']['passing_classes'])
")

if [ "$PASSING_COUNT" = "0" ]; then
    echo "row $ROW_INDEX: no passing tests on pre, skipping breaking stage"
    python3 -c "
import json
result = {
    'main_compile_failed': False,
    'tests': [],
    'summary': {
        'total_classes': 0, 'total_methods': 0,
        'passed_classes': 0, 'failed_classes': 0,
        'passed_methods': 0, 'failed_methods': 0
    }
}
with open('$RESULTS_DIR/breaking_results.json', 'w') as f:
    json.dump(result, f, indent=2)
"
    docker rmi $PRE_IMAGE || true
    exit 0
fi

echo "row $ROW_INDEX: building breaking image ($NEW_VERSION)"
BREAKING_BUILD_FAILED=false
docker build \
    --build-arg JAVA_VERSION=$JAVA_VERSION \
    --build-arg MAVEN_VERSION=$MAVEN_VERSION \
    --build-arg OLD_VERSION=$OLD_VERSION \
    --build-arg NEW_VERSION=$NEW_VERSION \
    --build-arg LIBRARY_GROUP_ID=$LIBRARY_GROUP_ID \
    --build-arg LIBRARY_NAME=$LIBRARY_NAME \
    -f $ACTION_PATH/docker/breaking-version/Dockerfile.breaking \
    -t $BREAKING_IMAGE \
    $CLIENT_DIR || BREAKING_BUILD_FAILED=true

if [ "$BREAKING_BUILD_FAILED" = "true" ]; then
    echo "row $ROW_INDEX: breaking image build failed"
    python3 -c "
import json
result = {
    'main_compile_failed': True,
    'tests': [],
    'summary': {
        'total_classes': 0, 'total_methods': 0,
        'passed_classes': 0, 'failed_classes': 0,
        'passed_methods': 0, 'failed_methods': 0
    }
}
with open('$RESULTS_DIR/breaking_results.json', 'w') as f:
    json.dump(result, f, indent=2)
"
    docker rmi $PRE_IMAGE || true
    exit 0
fi

echo "row $ROW_INDEX: running breaking stage (compile + execute)"
docker run --rm \
    -v "$STAGED_DIR:/staged:ro" \
    -v "$RESULTS_DIR:/results" \
    $BREAKING_IMAGE \
    bash -c "
        source /root/.sdkman/bin/sdkman-init.sh && \
        /compile_tests.sh /staged /results/compile_results_breaking.json && \
        /execute_tests.sh \
            /results/compile_results_breaking.json \
            /results/fqn_map.json \
            /results/breaking_results.json \
            /results/passing_tests.json
    "

echo "row $ROW_INDEX: cleaning up images"
docker rmi $PRE_IMAGE $BREAKING_IMAGE || true

echo "row $ROW_INDEX: executor pipeline complete"