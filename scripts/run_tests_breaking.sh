#!/bin/bash
# run_tests_breaking.sh
# runs compile and execute in one session for breaking stage
# arguments:
#   $1 -> staged dir
#   $2 -> results dir
#   $3 -> fqn_map.json path
#   $4 -> passing_tests.json path

set -e

source /root/.sdkman/bin/sdkman-init.sh

STAGED_DIR=$1
RESULTS_DIR=$2
FQN_MAP=$3
PASSING_TESTS=$4

/compile_tests.sh \
    $STAGED_DIR \
    $RESULTS_DIR/compile_results_breaking.json

/execute_tests.sh \
    $RESULTS_DIR/compile_results_breaking.json \
    $FQN_MAP \
    $RESULTS_DIR/breaking_results.json \
    $PASSING_TESTS