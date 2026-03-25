#!/bin/bash
# commit_resolved_config.sh
#
# PURPOSE:
#   Commits bc-config-resolved.csv to a dedicated review branch
#   so the user can inspect, edit, and confirm detected values
#   before re-running with mode: run.
#
# DESIGN DECISIONS:
#   1. DEDICATED REVIEW BRANCH
#      Commits to a branch named llmbreakguard/review-{timestamp}
#      rather than directly to the current branch.
#      Reason: avoids polluting the user's working branch with
#      a tool-generated file. The user can review, edit, merge
#      or delete the branch as they see fit.
#
#   2. TIMESTAMP IN BRANCH NAME
#      Branch name includes a timestamp.
#      Reason: if detect mode is run multiple times each run
#      gets its own review branch. No branch conflicts and the
#      user can compare runs if needed.
#
#   3. BOT IDENTITY FOR COMMIT
#      Commits as llmbreakguard[bot] with a generic email.
#      Reason: makes it clear in git history that this commit
#      was made by the tool not a human. Easy to identify and
#      revert if needed.
#
#   4. GITHUB TOKEN FOR PUSH
#      Uses GITHUB_TOKEN injected by GitHub Actions for push.
#      Reason: the action runs in a sandboxed environment with
#      no user credentials. GITHUB_TOKEN is automatically
#      available in all GitHub Actions workflows and has write
#      access to the repo by default.
#
#   5. REVIEW INSTRUCTIONS IN COMMIT MESSAGE
#      The commit message explains exactly what the user needs
#      to do next.
#      Reason: the user may receive the notification about this
#      commit without reading the action logs. The commit message
#      itself should be self-contained instructions.
#
#   6. BRANCH URL PRINTED TO LOGS
#      The full URL to the review branch is printed to the
#      action logs.
#      Reason: the user can click directly from the logs to
#      the review branch without having to navigate GitHub
#      manually.
#
# ARGUMENTS:
#   $1 -> resolved config filename (bc-config-resolved.csv)
#   $2 -> workspace path ($GITHUB_WORKSPACE)

set -e

RESOLVED_FILENAME=$1
WORKSPACE=$2

cd "$WORKSPACE"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BRANCH="llmbreakguard/review-${TIMESTAMP}"

# configure git identity
git config user.name  "llmbreakguard[bot]"
git config user.email "llmbreakguard@github-actions"

# configure remote to use GITHUB_TOKEN for auth
git remote set-url origin \
    "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

# create and switch to review branch
git checkout -b "$BRANCH"

# stage resolved config
git add "$RESOLVED_FILENAME"

# check if there is anything to commit
if git diff --cached --quiet; then
    echo "no changes to commit in $RESOLVED_FILENAME"
    exit 0
fi

git commit -m "llmbreakguard: auto-detected config — please review before running

LLMBreakGuard has auto-detected the following fields and written
them to ${RESOLVED_FILENAME}:

  - testing_framework  : detected from pom.xml dependencies
  - test_source_root   : detected from project directory structure
  - llm_tests_folder   : default value
  - import_prefix      : derived from library_group_id — validate this

Please review ${RESOLVED_FILENAME} on this branch.
Edit any incorrect or missing values.
Then re-run this workflow with:  mode: run

Fields that could not be detected are left empty and must be
filled in manually before running."

git push origin "$BRANCH"

echo ""
echo "resolved config committed to branch: $BRANCH"
echo "review at: ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/blob/${BRANCH}/${RESOLVED_FILENAME}"
echo ""
echo "next steps:"
echo "  1. open the link above"
echo "  2. review and edit any incorrect or missing values"
echo "  3. commit your changes to the branch"
echo "  4. re-run this workflow with mode: run"
echo ""