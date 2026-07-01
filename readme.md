# LLMBreakGuard

Detects breaking changes in OSS library upgrades **before** shipping to production.

Given a Java client project and a library version bump (e.g. `jackson-databind 2.4.2 → 2.12.6`), LLMBreakGuard:

1. Clones the client repo and runs [Spoon](https://spoon.gforge.inria.fr/) static analysis to find every place the library is used
2. Asks an LLM (GPT4o/Claude) to generate UNIT test classes targeting those exact API calls
3. Compiles and runs the tests against the **old** library version (baseline)
4. Compiles and runs the same tests against the **new** library version
5. Compares results — any test that passed on the old version but fails on the new version is reported as a breaking change

---

## Prerequisites

### Local execution
- Python 3.10+
- Docker (running)
- Java 17+ on the host (only needed to build the Spoon JAR)
- An [Anthropic API key](https://console.anthropic.com/)
- The Spoon extractor JAR at `spoon-analysis/spoon-extractor.jar`

### GitHub Action execution
- An Anthropic API key stored as a repository secret (`ANTHROPIC_API_KEY`)
- Docker available on the runner (included on `ubuntu-latest`)

---

## Step 1 — Build the Spoon JAR (one-time setup)

The Spoon JAR extracts library usage information from the client repo. Build it once:

```bash
cd spoon-analysis/my_spoon_wrapper
mvn package -q
cp target/spoon-extractor-*.jar ../spoon-extractor.jar
cd ../..
```

After this, `spoon-analysis/spoon-extractor.jar` must exist. The pipeline reads it from that path automatically. If you want to override the path set `SPOON_JAR_PATH=/your/path/to/spoon-extractor.jar` in your environment.

---

## Step 2 — Fill in `bc-config.csv`

Edit `bc-config.csv` in the project root. One row per library upgrade you want to check.

| Column | Description | Example |
|--------|-------------|---------|
| `client_github_url` | GitHub PR URL of the client project at the upgrade commit | `https://github.com/org/repo/pull/77` |
| `pre_breaking_commit` | Full commit SHA of the client code **before** the library bump | `8776fe7cd60bd...` |
| `library_group_id` | Maven group ID of the library | `com.fasterxml.jackson.core` |
| `library_name` | Maven artifact ID of the library | `jackson-databind` |
| `old_version` | Library version the client is currently on | `2.4.2` |
| `new_version` | Library version the client wants to upgrade to | `2.12.6.1` |
| `java_version` | Java major version the client project uses | `17` |
| `build_tool` | `maven` or `gradle` | `maven` |
| `build_tool_version` | Exact build tool version | `3.9.6` |
| `testing_framework` | `junit5`, `junit4`, or `testng` | `testng` |
| `test_source_root` | Relative path to the test sources directory | `src/test/java` |
| `llm_tests_folder` | Subfolder name for generated tests (inside test source root) | `bc_generated_tests` |

**Example `bc-config.csv`:**
```csv
client_github_url,pre_breaking_commit,library_group_id,library_name,old_version,new_version,java_version,build_tool,build_tool_version,testing_framework,test_source_root,llm_tests_folder
https://github.com/versly/wsdoc/pull/77,8776fe7cd60bd309daf843bc70608ebf963c6761,com.fasterxml.jackson.core,jackson-databind,2.4.2,2.12.6.1,17,maven,3.9.6,testng,src/test/java,bc_generated_tests
```

> **Tip — not sure about `testing_framework` or `test_source_root`?**  
> Run the pipeline in `detect` mode (GitHub Action only) and it will auto-detect these fields from the client repo's `pom.xml` or `build.gradle` and write a `bc-config-resolved.csv` for you to review.

---

## Step 3 — Set up credentials

Create a `.env` file in the project root:

```
LLM_API_KEY=sk-ant-...
```

This file is read automatically by the pipeline. It is already listed in `.gitignore` — do not commit it.

---

## Step 4 — Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Step 5 — Run the pipeline locally

```bash
bash scripts/run_pipeline.sh
```

The script walks you through the pipeline interactively:

```
[1/13] parsing config
[2/13] running spoon static analysis
[3/13] generating manifest

══════════════════════════════════════
CONFIRMATION REQUIRED
══════════════════════════════════════
  row 1: https://github.com/versly/wsdoc/pull/77
    library          : jackson-databind 2.4.2 -> 2.12.6.1
    classes found    : 2
    usage blocks     : 4  (= 4 LLM calls)

next steps will:
  1. call LLM API for each usage block above (costs credits)
  2. build Docker images for pre and breaking versions
  3. execute tests in Docker containers

do you want to continue? (y/n):
```

After you confirm, the pipeline:
- Calls the LLM API once per usage block to generate test classes
- Builds a Docker image with the **old** library version and runs the tests
- Filters to keep only tests that pass on the old version (valid baseline)
- Builds a Docker image with the **new** library version and runs the same tests
- Compares results and writes the report

### Output files

All output lands in `workspace/`:

```
workspace/
├── pipeline.log              ← full log of every step
├── bc_report.json            ← machine-readable final report
├── bc_report.md              ← human-readable markdown report
├── manifest.json             ← intermediate: all usage blocks
├── configs.json              ← parsed config rows
├── clients/                  ← cloned client repos
├── staged_tests/             ← LLM-generated test files
│   └── staged_0/
│       └── com/example/llmtests/
│           ├── R1C0U0BCDetectorTest.java
│           └── R1C0U0BCDetectorTest_prompt.txt
└── results_0/
    ├── pre_results.json       ← test results on old version
    ├── breaking_results.json  ← test results on new version
    ├── passing_tests.json     ← filter output
    └── bc_report_0.json       ← per-row report
```

### Resume after interruption

If the LLM generation step is interrupted (rate limit, network error, credits), re-run the same command. The pipeline saves progress to `workspace/llm_progress.json` and skips already-generated tests.

---

## Running as a GitHub Action

Add LLMBreakGuard to any repository's workflow. The action runs in two modes:

### Mode 1: `detect` — auto-detect project info

If you are unsure about `testing_framework` or `test_source_root`, run detect mode first. It clones the repo and inspects `pom.xml`/`build.gradle` to fill in those fields automatically.

**`.github/workflows/bc-detect.yml`:**
```yaml
name: BC Detect
on:
  workflow_dispatch:

jobs:
  detect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Detect project info
        uses: your-org/LLMBreakGuard@main
        with:
          mode: detect
          config-file: bc-config.csv
          llm-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

This writes `bc-config-resolved.csv` to a review branch. Open that file, confirm or correct the detected fields, commit it, then run mode 2.

---

### Mode 2: `run` — full pipeline

**`.github/workflows/bc-check.yml`:**
```yaml
name: Breaking Change Check
on:
  pull_request:
  workflow_dispatch:

jobs:
  bc-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Check for breaking changes
        uses: your-org/LLMBreakGuard@main
        with:
          mode: run
          config-file: bc-config.csv
          llm-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          llm-model: claude-sonnet-4-6    # optional, this is the default
```

The action uploads `bc_report.json` and `bc_report.md` as artifacts and writes a summary to the GitHub Actions step summary page.

**Action inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `config-file` | No | `bc-config.csv` | Path to config CSV relative to repo root |
| `mode` | No | `detect` | `detect` or `run` |
| `resolved-config-file` | No | `bc-config-resolved.csv` | Used only in `detect` mode output and `run` mode input |
| `llm-api-key` | Yes | — | Anthropic API key — pass as `${{ secrets.ANTHROPIC_API_KEY }}` |
| `llm-model` | No | `claude-sonnet-4-6` | Claude model to use for test generation |

**Action outputs:**

| Output | Description |
|--------|-------------|
| `bc-report` | Path to the generated `bc_report.json` artifact |
| `breaking-changes-detected` | Number of individual breaking test methods found |
| `rows-with-bc` | Number of config rows where breaking changes were detected |

---

## Understanding the report

### `bc_report.md` (human-readable)

```markdown
# LLMBreakGuard Report

## Summary
| Metric                  | Value |
|-------------------------|-------|
| Rows analyzed           | 1     |
| Breaking changes detected | 1   |
| No breaking changes     | 0     |
| Inconclusive            | 0     |
| Total methods tested    | 4     |
| Total breaking methods  | 2     |

## Breaking Changes Detected

### jackson-databind 2.4.2 -> 2.12.6.1
**Verdict:** BREAKING CHANGES DETECTED

| Class                  | Method                    | Failure Message           |
|------------------------|---------------------------|---------------------------|
| R1C1U0BCDetectorTest   | test_jsonSchemaFromType... | expected [200] but was [500] |
| R1C2U1BCDetectorTest   | test_isJsonBeanGetter     | NoSuchMethodError: readValue |
```

### Verdict meanings

| Verdict | Meaning |
|---------|---------|
| `BREAKING CHANGES DETECTED` | At least one test passed on the old version and failed on the new version |
| `NO BREAKING CHANGES` | All tests that passed on the old version also passed on the new version |
| `INCONCLUSIVE` | No valid tests could be established on the old version (check logs) |

---

## Troubleshooting

**"spoon jar not found"**  
Build the JAR as described in Step 1, or set `SPOON_JAR_PATH` to the correct path.

**"spoon found no classes using \<library\>"**  
The `library_group_id`, `library_name`, or derived `import_prefix` is wrong. The import prefix is derived by dropping the last segment of the group ID — for `com.fasterxml.jackson.core` this gives `com.fasterxml.jackson`. If the client's imports use a different prefix, set it manually in `bc-config-resolved.csv`.

**All tests INCONCLUSIVE**  
The LLM-generated tests all failed on the old (baseline) version. This means the tests themselves are invalid — they likely make incorrect assumptions about the client codebase. Check `workspace/staged_tests/` to inspect the generated files and the prompt files alongside them (`_prompt.txt`) to debug what the LLM was given.

**LLM generation interrupted**  
Re-run `bash scripts/run_pipeline.sh`. Progress is saved to `workspace/llm_progress.json` and already-generated files are skipped automatically.

**Docker build fails on the breaking version**  
This itself is reported as a `BREAKING CHANGES DETECTED` verdict with a `compilation_bc` entry — it means the library change broke the client code at the compilation level, which is the strongest form of breaking change.
