#!/bin/bash
set -e

# Arguments:
#   $1 -> config-file
#   $2 -> mode (detect or run)
#   $3 -> resolved-config-file

CONFIG_FILE="${GITHUB_WORKSPACE}/$1"
MODE="${2:-detect}"
RESOLVED_CONFIG="${GITHUB_WORKSPACE}/${3:-bc-config-resolved.csv}"

source /root/.sdkman/bin/sdkman-init.sh

# --------------------------------------------------------------
# switch_versions
# Reads java_version, build_tool, build_tool_version from a
# JSON entry and switches to them via SDKMAN
# --------------------------------------------------------------
switch_versions() {
    local ENTRY=$1

    local JAVA_VERSION=$(echo "$ENTRY" | python3 -c \
        "import sys,json; print(json.load(sys.stdin)['java_version'])")
    local BUILD_TOOL=$(echo "$ENTRY" | python3 -c \
        "import sys,json; print(json.load(sys.stdin)['build_tool'])")
    local TOOL_VERSION=$(echo "$ENTRY" | python3 -c \
        "import sys,json; print(json.load(sys.stdin)['build_tool_version'])")

    echo "switching to Java $JAVA_VERSION"
    if sdk list java | grep -q "installed.*${JAVA_VERSION}"; then
        sdk use java ${JAVA_VERSION}.0.9-tem 2>/dev/null || \
        sdk use java ${JAVA_VERSION}-tem     2>/dev/null || true
    else
        sdk install java ${JAVA_VERSION}-tem || true
        sdk use java ${JAVA_VERSION}-tem
    fi

    if [ "$BUILD_TOOL" = "maven" ]; then
        echo "switching to Maven $TOOL_VERSION"
        if sdk list maven | grep -q "installed.*${TOOL_VERSION}"; then
            sdk use maven $TOOL_VERSION
        else
            sdk install maven $TOOL_VERSION || true
            sdk use maven $TOOL_VERSION
        fi

    elif [ "$BUILD_TOOL" = "gradle" ]; then
        echo "switching to Gradle $TOOL_VERSION"
        if sdk list gradle | grep -q "installed.*${TOOL_VERSION}"; then
            sdk use gradle $TOOL_VERSION
        else
            sdk install gradle $TOOL_VERSION || true
            sdk use gradle $TOOL_VERSION
        fi
    fi
}

# --------------------------------------------------------------
# merge_entry
# Merges user-provided values with auto-detected values.
# User values always win.
# --------------------------------------------------------------
merge_entry() {
    local USER_ENTRY=$1
    local DETECTED=$2

    python3 -c "
import sys, json

user     = json.loads(sys.argv[1])
detected = json.loads(sys.argv[2])

merged = {**detected}

for k, v in user.items():
    if v:
        merged[k] = v

merged.setdefault('llm_tests_folder', 'bc_generated_tests')
merged.setdefault('testing_framework', '')
merged.setdefault('test_source_root', '')

print(json.dumps(merged))
" "$USER_ENTRY" "$DETECTED"
}

# --------------------------------------------------------------
# MODE: DETECT
# --------------------------------------------------------------
if [ "$MODE" = "detect" ]; then

    echo "parsing $CONFIG_FILE"
    CONFIGS=$(python3 /app/src/pipeline/parse_config.py "$CONFIG_FILE")
    ROW_COUNT=$(echo "$CONFIGS" | python3 -c \
        "import sys,json; print(len(json.load(sys.stdin)))")

    ALL_RESOLVED="["

    for i in $(seq 0 $((ROW_COUNT - 1))); do

        ENTRY=$(echo "$CONFIGS" | python3 -c \
            "import sys,json; d=json.load(sys.stdin); print(json.dumps(d[$i]))")

        REPO=$(echo "$ENTRY" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['client_repo'])")
        BUILD_TOOL=$(echo "$ENTRY" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['build_tool'])")

        CLIENT_DIR="/workspace/client_$i"

        echo "row $((i+1))/$ROW_COUNT : $REPO"

        switch_versions "$ENTRY"

        echo "cloning $REPO"
        if ! git clone "$REPO" "$CLIENT_DIR"; then
            echo "failed to clone $REPO"
            exit 1
        fi

        echo "detecting project info"
        DETECTED=$(python3 /app/src/pipeline/detect_project_info.py \
            "$CLIENT_DIR" "$BUILD_TOOL") || {
            echo "detection failed, fields will be empty in resolved config"
            DETECTED='{"testing_framework":"","test_source_root":"","llm_tests_folder":"bc_generated_tests"}'
        }

        RESOLVED_ENTRY=$(merge_entry "$ENTRY" "$DETECTED")

        echo "detected values for row $((i+1)):"
        echo "$RESOLVED_ENTRY" | python3 -c "
import sys, json
entry = json.load(sys.stdin)
fields = ['testing_framework', 'test_source_root', 'llm_tests_folder']
for f in fields:
    v = entry.get(f, '')
    status = 'detected' if v else 'not detected - please fill in'
    print(f'  {f}: {v} ({status})')
"
        ALL_RESOLVED+="$RESOLVED_ENTRY,"
    done

    ALL_RESOLVED="${ALL_RESOLVED%,}]"

    echo "writing resolved config to $RESOLVED_CONFIG"
    python3 /app/src/pipeline/write_resolved_csv.py \
        "$ALL_RESOLVED" "$RESOLVED_CONFIG"

    bash /app/scripts/commit_resolved_config.sh \
        "$(basename $RESOLVED_CONFIG)" \
        "$GITHUB_WORKSPACE"

    echo ""
    echo "detect mode complete"
    echo "please review $RESOLVED_CONFIG on the review branch"
    echo "edit any incorrect or missing values, then re-run with mode: run"
    echo ""

    exit 1

# --------------------------------------------------------------
# MODE: RUN
# --------------------------------------------------------------
elif [ "$MODE" = "run" ]; then

    if [ ! -f "$RESOLVED_CONFIG" ]; then
        echo "resolved config not found at $RESOLVED_CONFIG"
        echo "please run with mode: detect first"
        exit 1
    fi

    echo "parsing resolved config $RESOLVED_CONFIG"
    CONFIGS=$(python3 /app/src/pipeline/parse_config.py \
        "$RESOLVED_CONFIG" --resolved)
    ROW_COUNT=$(echo "$CONFIGS" | python3 -c \
        "import sys,json; print(len(json.load(sys.stdin)))")

    TOTAL_CLASSES=0
    TOTAL_BC=0

    for i in $(seq 0 $((ROW_COUNT - 1))); do

        ENTRY=$(echo "$CONFIGS" | python3 -c \
            "import sys,json; d=json.load(sys.stdin); print(json.dumps(d[$i]))")

        REPO=$(echo "$ENTRY" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['client_repo'])")
        LIBRARY=$(echo "$ENTRY" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['library_name'])")
        OLD_V=$(echo "$ENTRY" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['old_version'])")
        NEW_V=$(echo "$ENTRY" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['new_version'])")
        LLM_FOLDER=$(echo "$ENTRY" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['llm_tests_folder'])")

        CLIENT_DIR="/workspace/client_$i"
        ANALYSIS_DIR="/workspace/analysis_$i"
        STAGED_DIR="/workspace/${LLM_FOLDER}_$i"
        MANIFEST="/workspace/manifest_$i.json"
        RESULTS_DIR="/workspace/results_$i"

        mkdir -p "$RESULTS_DIR"

        echo ""
        echo "row $((i+1))/$ROW_COUNT : $REPO"
        echo "library: $LIBRARY $OLD_V -> $NEW_V"

        switch_versions "$ENTRY"

        echo "cloning $REPO"
        if ! git clone "$REPO" "$CLIENT_DIR"; then
            echo "failed to clone $REPO, skipping"
            continue
        fi

        echo "running spoon analysis"
        if ! python3 /app/src/pipeline/run_static_analysis.py \
                "$ENTRY" "$CLIENT_DIR" "$ANALYSIS_DIR"; then
            echo "spoon analysis failed, skipping row $((i+1))"
            continue
        fi

        echo "generating manifest"
        if ! python3 /app/src/pipeline/generate_manifest.py \
                "$ENTRY" "$ANALYSIS_DIR" "$STAGED_DIR" "$MANIFEST"; then
            echo "manifest generation failed, skipping row $((i+1))"
            continue
        fi

        CLASS_COUNT=$(python3 -c "
import json
with open('$MANIFEST') as f:
    m = json.load(f)
print(len(m['classes']))
")
        TOTAL_CLASSES=$((TOTAL_CLASSES + CLASS_COUNT))

        echo "spoon found $CLASS_COUNT class(es) using $LIBRARY"
        python3 -c "
import json
with open('$MANIFEST') as f:
    m = json.load(f)
for c in m['classes']:
    print(f\"  {c['fully_qualified_name']}\")
    print(f\"    staged     : {c['staged_path']}\")
    print(f\"    transplant : {c['transplant_path']}\")
"

        echo "generating tests via LLM for $CLASS_COUNT class(es)"
        if ! python3 /app/src/pipeline/prompt_llm.py "$MANIFEST"; then
            echo "LLM test generation failed, skipping row $((i+1))"
            continue
        fi

        echo "transplanting tests"
        if ! python3 /app/src/pipeline/transplant_tests.py \
                "$MANIFEST" "$CLIENT_DIR"; then
            echo "transplant failed, skipping row $((i+1))"
            continue
        fi

        echo "running tests against old version $OLD_V"
        python3 /app/src/pipeline/run_tests.py \
            "$MANIFEST" \
            "$CLIENT_DIR" \
            "$OLD_V" \
            "$RESULTS_DIR/old_results.json" || true

        echo "running tests against new version $NEW_V"
        python3 /app/src/pipeline/run_tests.py \
            "$MANIFEST" \
            "$CLIENT_DIR" \
            "$NEW_V" \
            "$RESULTS_DIR/new_results.json" || true

        echo "comparing results"
        BC_COUNT=$(python3 /app/src/detection/compare_results.py \
            "$RESULTS_DIR/old_results.json" \
            "$RESULTS_DIR/new_results.json" \
            "$MANIFEST" \
            "$RESULTS_DIR/bc_report_$i.json")

        TOTAL_BC=$((TOTAL_BC + BC_COUNT))
    done

    echo "generating final report"
    python3 /app/src/reporting/generate_report.py \
        "/workspace" \
        "$ROW_COUNT" \
        "/workspace/bc_report.json"

    echo ""
    echo "done"
    echo "  classes analyzed      : $TOTAL_CLASSES"
    echo "  breaking changes found: $TOTAL_BC"
    echo "  report                : /workspace/bc_report.json"
    echo ""

    {
        echo "breaking-changes-detected=$TOTAL_BC"
        echo "bc-report=/workspace/bc_report.json"
        echo "tests-transplanted=$TOTAL_CLASSES"
    } >> "$GITHUB_OUTPUT"

    [ "$TOTAL_BC" -gt 0 ] && exit 1 || exit 0

else
    echo "unknown mode: $MODE, valid values are detect or run"
    exit 1
fi