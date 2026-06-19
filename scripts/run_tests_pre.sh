#!/bin/bash
# run_tests_pre.sh
# runs compile and execute in one session so compiled classes persist
# arguments:
#   $1 -> staged dir
#   $2 -> results dir
#   $3 -> fqn_map.json path

set -e

source /root/.sdkman/bin/sdkman-init.sh

STAGED_DIR=$1
RESULTS_DIR=$2
FQN_MAP=$3

/compile_tests.sh \
    $STAGED_DIR \
    $RESULTS_DIR/compile_results_pre.json

/execute_tests.sh \
    $RESULTS_DIR/compile_results_pre.json \
    $FQN_MAP \
    $RESULTS_DIR/pre_results.json \
    ""