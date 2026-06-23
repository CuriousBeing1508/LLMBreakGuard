#!/bin/bash
# run_pipeline.sh
#
# PURPOSE:
#   Runs the full LLMBreakGuard pipeline end to end for local testing.
#   Logs all output to workspace/pipeline.log
#   Asks user confirmation before LLM calls and Docker builds.
#
# USAGE:
#   bash scripts/run_pipeline.sh
#
# PREREQUISITES:
#   - bc-config.csv exists in project root with correct values
#   - .env file exists with LLM_API_KEY set
#   - Docker is running
#   - Virtual environment is activated

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="$PROJECT_DIR/workspace"
LOG_FILE="$WORKSPACE/pipeline.log"

cd "$PROJECT_DIR"

mkdir -p "$WORKSPACE"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "======================================================"
echo "LLMBreakGuard pipeline started"
echo "$(date)"
echo "======================================================"

# ── step 1: parse config ──────────────────────────────────
echo ""
echo "[1/13] parsing config"
python3 src/pipeline/parse_config.py bc-config.csv \
    > workspace/configs.json

echo ""
echo "config loaded:"
python3 -c "
import json
with open('workspace/configs.json') as f:
    configs = json.load(f)
for i, c in enumerate(configs):
    print(f'  row {i+1}:')
    print(f'    repo             : {c[\"client_github_url\"]}')
    print(f'    library          : {c[\"library_group_id\"]}:{c[\"library_name\"]}')
    print(f'    version bump     : {c[\"old_version\"]} -> {c[\"new_version\"]}')
    print(f'    java             : {c[\"java_version\"]}')
    print(f'    build tool       : {c[\"build_tool\"]} {c[\"build_tool_version\"]}')
    print(f'    testing framework: {c[\"testing_framework\"]}')
    print(f'    test source root : {c[\"test_source_root\"]}')
"

# ── step 2: run static analysis ───────────────────────────
echo ""
echo "[2/13] running spoon static analysis"
mkdir -p workspace/clients
mkdir -p workspace/analysis
python3 src/pipeline/run_static_analysis.py \
    workspace/configs.json \
    workspace \
    workspace/analysis

# ── step 3: generate manifest ─────────────────────────────
echo ""
echo "[3/13] generating manifest"
mkdir -p workspace/staged_tests
python3 src/pipeline/generate_manifest.py \
    workspace/configs.json \
    workspace/analysis \
    workspace/staged_tests \
    workspace \
    workspace/manifest.json

# ── confirmation checkpoint ───────────────────────────────
echo ""
echo "======================================================"
echo "CONFIRMATION REQUIRED"
echo "======================================================"
echo ""
echo "static analysis complete. please review before continuing:"
echo ""
python3 -c "
import json
with open('workspace/manifest.json') as f:
    m = json.load(f)
for row in m['rows']:
    print(f'  row {row[\"row_index\"]}: {row[\"client_github_url\"]}')
    print(f'    library          : {row[\"library_name\"]} {row[\"old_version\"]} -> {row[\"new_version\"]}')
    print(f'    testing framework: {row[\"testing_framework\"]}')
    print(f'    test source root : {row[\"test_source_root\"]}')
    print(f'    classes found    : {len(row[\"classes\"])}')
    total_blocks = sum(len(c[\"usage_blocks\"]) for c in row[\"classes\"])
    print(f'    usage blocks     : {total_blocks} (= {total_blocks} LLM calls)')
    print()
    for cls in row['classes']:
        print(f'    class: {cls[\"class_fqn\"]}')
        for ub in cls['usage_blocks']:
            print(f'      {ub[\"test_class_name\"]} <- {ub[\"method_name\"]}')
            print(f'        staged     : {ub[\"staged_path\"]}')
            print(f'        transplant : {ub[\"transplant_path\"]}')
        print()
"
echo "------------------------------------------------------"
echo "next steps will:"
echo "  1. call LLM API for each usage block above (costs credits)"
echo "  2. build Docker images for pre and breaking versions"
echo "  3. execute tests in Docker containers"
echo "------------------------------------------------------"
echo ""
read -p "do you want to continue? (y/n): " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "pipeline stopped by user"
    echo "you can edit bc-config.csv and re-run"
    exit 0
fi

# ── step 4: generate prompts ──────────────────────────────
echo ""
echo "[4/13] generating prompts"
python3 src/pipeline/generate_prompt.py \
    workspace/manifest.json

# ── step 5: generate tests via LLM ───────────────────────
echo ""
echo "[5/13] generating tests via LLM"
python3 src/pipeline/prompt_llm.py \
    workspace/manifest.json

# ── step 6: transplant tests ──────────────────────────────
echo ""
echo "[6/13] transplanting tests (one per row)"

ROW_COUNT=$(python3 -c "
import json
with open('workspace/manifest.json') as f:
    m = json.load(f)
print(len(m['rows']))
")

python3 -c "
import json
with open('workspace/manifest.json') as f:
    m = json.load(f)
for row in m['rows']:
    i = row['row_index'] - 1
    print(i, row['client_name'])
" | while read ROW_IDX CLIENT_NAME; do
    CLIENT_DIR="workspace/clients/${CLIENT_NAME}_${ROW_IDX}"
    python3 src/pipeline/transplant_tests.py \
        workspace/manifest.json \
        "$CLIENT_DIR" \
        "workspace/results_${ROW_IDX}"
done

# ── step 7: build base docker image ───────────────────────
echo ""
echo "[7/13] building base docker image"
docker build \
    -f docker/Dockerfile.base \
    -t llmbreakguard-base:latest \
    . 2>&1

# ── steps 8–12: per-row pre + breaking execution ──────────
python3 -c "
import json
with open('workspace/configs.json') as f:
    configs = json.load(f)
with open('workspace/manifest.json') as f:
    manifest = json.load(f)
for i, (c, r) in enumerate(zip(configs, manifest['rows'])):
    print(
        i,
        r['client_name'],
        c['java_version'],
        c['build_tool_version'],
        c['library_group_id'],
        c['library_name'],
        c['old_version'],
        c['new_version']
    )
" | while read ROW_IDX CLIENT_NAME JAVA_VERSION MAVEN_VERSION LIBRARY_GROUP LIBRARY_NAME OLD_VERSION NEW_VERSION; do

    CLIENT_DIR="workspace/clients/${CLIENT_NAME}_${ROW_IDX}"
    RESULTS_DIR="workspace/results_${ROW_IDX}"
    mkdir -p "$RESULTS_DIR"

    echo ""
    echo "[8/13] building pre image — row $ROW_IDX ($LIBRARY_NAME $OLD_VERSION)"
    docker build \
        --build-arg JAVA_VERSION=$JAVA_VERSION \
        --build-arg MAVEN_VERSION=$MAVEN_VERSION \
        -f "$(pwd)/docker/pre-version/Dockerfile.pre" \
        -t "llmbreakguard-pre-${ROW_IDX}" \
        "$CLIENT_DIR" 2>&1

    echo ""
    echo "[9/13] running tests on pre version — row $ROW_IDX ($OLD_VERSION)"
    docker run --rm \
        -v "$(pwd)/workspace/staged_tests/staged_${ROW_IDX}:/staged:ro" \
        -v "$(pwd)/${RESULTS_DIR}:/results" \
        "llmbreakguard-pre-${ROW_IDX}" \
        bash -c "
            source /root/.sdkman/bin/sdkman-init.sh && \
            /compile_tests.sh /staged /results/compile_results_pre.json && \
            /execute_tests.sh \
                /results/compile_results_pre.json \
                /results/fqn_map.json \
                /results/pre_results.json \
                ''
        " 2>&1

    echo ""
    echo "[10/13] filtering passing tests — row $ROW_IDX"
    python3 src/pipeline/filter_tests.py \
        "${RESULTS_DIR}/pre_results.json" \
        "${RESULTS_DIR}/passing_tests.json"

    PASSING=$(python3 -c "
import json
with open('${RESULTS_DIR}/passing_tests.json') as f:
    d = json.load(f)
print(d['summary']['passing_classes'])
")

    if [ "$PASSING" = "0" ]; then
        echo "row $ROW_IDX: no passing tests on pre, skipping breaking stage"
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
with open('${RESULTS_DIR}/breaking_results.json', 'w') as f:
    json.dump(result, f, indent=2)
"
        docker rmi "llmbreakguard-pre-${ROW_IDX}" || true
    else
        echo ""
        echo "[11/13] building breaking image — row $ROW_IDX ($LIBRARY_NAME $NEW_VERSION)"
        docker build \
            --build-arg JAVA_VERSION=$JAVA_VERSION \
            --build-arg MAVEN_VERSION=$MAVEN_VERSION \
            --build-arg OLD_VERSION=$OLD_VERSION \
            --build-arg NEW_VERSION=$NEW_VERSION \
            --build-arg LIBRARY_GROUP_ID=$LIBRARY_GROUP \
            --build-arg LIBRARY_NAME=$LIBRARY_NAME \
            -f "$(pwd)/docker/breaking-version/Dockerfile.breaking" \
            -t "llmbreakguard-breaking-${ROW_IDX}" \
            "$CLIENT_DIR" 2>&1

        echo ""
        echo "[12/13] running tests on breaking version — row $ROW_IDX ($NEW_VERSION)"
        docker run --rm \
            -v "$(pwd)/workspace/staged_tests/staged_${ROW_IDX}:/staged:ro" \
            -v "$(pwd)/${RESULTS_DIR}:/results" \
            "llmbreakguard-breaking-${ROW_IDX}" \
            bash -c "
                source /root/.sdkman/bin/sdkman-init.sh && \
                /compile_tests.sh /staged /results/compile_results_breaking.json && \
                /execute_tests.sh \
                    /results/compile_results_breaking.json \
                    /results/fqn_map.json \
                    /results/breaking_results.json \
                    /results/passing_tests.json
            " 2>&1

        docker rmi "llmbreakguard-pre-${ROW_IDX}" "llmbreakguard-breaking-${ROW_IDX}" || true
    fi

    echo ""
    echo "[13/13] comparing results — row $ROW_IDX"
    python3 src/detection/compare_results.py \
        "${RESULTS_DIR}/pre_results.json" \
        "${RESULTS_DIR}/breaking_results.json" \
        workspace/manifest.json \
        "${RESULTS_DIR}/bc_report_${ROW_IDX}.json" \
        "${RESULTS_DIR}/passing_tests.json"

done

# ── final report ──────────────────────────────────────────
echo ""
echo "generating final report"
python3 src/reporting/generate_report.py \
    workspace \
    workspace/bc_report.json

echo ""
echo "======================================================"
echo "pipeline complete"
echo "$(date)"
echo "report : workspace/bc_report.json"
echo "report : workspace/bc_report.md"
echo "log    : $LOG_FILE"
echo "======================================================"