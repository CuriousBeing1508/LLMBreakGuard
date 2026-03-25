"""
generate_report.py

PURPOSE:
    Reads all per-row bc_report.json files and aggregates them into
    a single human-readable final report. This is the last step in
    the pipeline and produces the artifact uploaded by the action.

DESIGN DECISIONS:
    1. TWO OUTPUT FORMATS
       Produces both a machine-readable JSON report and a
       human-readable markdown report.
       Reason: JSON is consumed by downstream tools or CI systems
       that want to parse results programmatically. Markdown is
       rendered directly in the GitHub Actions summary and is
       readable by humans without any tooling.

    2. GITHUB ACTIONS STEP SUMMARY
       Writes the markdown report to GITHUB_STEP_SUMMARY env var
       if available.
       Reason: GitHub Actions renders this automatically in the
       workflow run summary page. Users see the BC report directly
       in the Actions UI without downloading any artifact.

    3. VERDICT DRIVES REPORT STRUCTURE
       The report is organized by verdict first:
         BREAKING CHANGES DETECTED -> shown prominently at top
         INCONCLUSIVE               -> shown with explanation
         NO BREAKING CHANGES        -> shown last
       Reason: users want to know immediately if something broke.
       Burying BC results under passing results would miss the
       point of the tool.

    4. METHOD LEVEL DETAIL IN REPORT
       For each breaking change the report shows:
         - which class and method triggered the BC
         - what the test failure message was
         - the library version that caused the failure
       Reason: the user needs to know exactly which API call
       broke so they can decide whether to upgrade, pin the
       version, or fix their code.

    5. INCONCLUSIVE EXPLAINED
       When verdict is INCONCLUSIVE the report explains why:
         - all generated tests were bad (failed on pre)
         - main compilation failed on pre
       Reason: INCONCLUSIVE without explanation is confusing.
       The user needs to know whether to trust the result.

    6. SUMMARY STATISTICS AT TOP
       The report starts with a summary table showing counts
       across all rows before diving into per-row details.
       Reason: users with many rows in bc-config.csv want a
       quick overview before reading details.
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime


def generate_markdown(report):
    lines = []

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("# LLMBreakGuard Report")
    lines.append(f"\nGenerated: {now}\n")

    summary = report["summary"]
    lines.append("## Summary\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Rows analyzed | {summary['total_rows']} |")
    lines.append(f"| Breaking changes detected | {summary['rows_with_bc']} |")
    lines.append(f"| No breaking changes | {summary['rows_no_bc']} |")
    lines.append(f"| Inconclusive | {summary['rows_inconclusive']} |")
    lines.append(f"| Total methods tested | {summary['total_methods_tested']} |")
    lines.append(f"| Total breaking methods | {summary['total_breaking_methods']} |")
    lines.append("")

    # breaking changes first
    bc_rows = [r for r in report["rows"] if r["verdict"] == "BREAKING CHANGES DETECTED"]
    if bc_rows:
        lines.append("## Breaking Changes Detected\n")
        for row in bc_rows:
            lines.append(
                f"### {row['library']} "
                f"{row['old_version']} -> {row['new_version']}"
            )
            lines.append(f"**Verdict:** {row['verdict']}\n")

            # compilation bc
            if row["results"]["compilation_bc"]:
                lines.append("**Compilation Breaking Change:**\n")
                for bc in row["results"]["compilation_bc"]:
                    lines.append(f"- {bc['description']}")
                lines.append("")

            # method level bc
            method_bcs = row["results"]["breaking_change"]
            if method_bcs:
                lines.append("**Breaking Methods:**\n")
                lines.append("| Class | Method | Failure Message |")
                lines.append("|-------|--------|-----------------|")
                for bc in method_bcs:
                    msg = bc.get("message", "").replace("|", "\\|")[:100]
                    lines.append(
                        f"| {bc['class']} "
                        f"| {bc['method']} "
                        f"| {msg} |"
                    )
                lines.append("")

            # no change methods for context
            no_change = row["results"]["no_change"]
            if no_change:
                lines.append(
                    f"**Methods with no change:** "
                    f"{len(no_change)}\n"
                )

    # inconclusive rows
    inc_rows = [r for r in report["rows"] if r["verdict"] == "INCONCLUSIVE"]
    if inc_rows:
        lines.append("## Inconclusive\n")
        for row in inc_rows:
            lines.append(
                f"### {row['library']} "
                f"{row['old_version']} -> {row['new_version']}"
            )
            lines.append(f"**Verdict:** {row['verdict']}\n")
            lines.append(
                "No valid tests were available to determine "
                "whether this bump is safe.\n"
            )
            if row["summary"].get("compilation_bc"):
                lines.append(
                    "Main compilation failed on pre — "
                    "check client code and library compatibility.\n"
                )

    # no breaking changes
    safe_rows = [r for r in report["rows"] if r["verdict"] == "NO BREAKING CHANGES"]
    if safe_rows:
        lines.append("## No Breaking Changes\n")
        for row in safe_rows:
            lines.append(
                f"### {row['library']} "
                f"{row['old_version']} -> {row['new_version']}"
            )
            lines.append(f"**Verdict:** {row['verdict']}\n")
            lines.append(
                f"All {row['summary']['total_methods_tested']} "
                f"tested method(s) passed on both versions.\n"
            )

    return "\n".join(lines)


def generate_report(results_dir, output_path):
    results_dir = Path(results_dir)

    # collect all per-row bc reports
    bc_report_files = sorted(results_dir.glob("bc_report_*.json"))

    all_rows = []
    for report_file in bc_report_files:
        with open(report_file) as f:
            data = json.load(f)
        all_rows.extend(data.get("rows", []))

    # if only one bc_report.json exists (single row)
    if not bc_report_files:
        single = results_dir / "bc_report.json"
        if single.exists():
            with open(single) as f:
                data = json.load(f)
            all_rows.extend(data.get("rows", []))

    total_rows            = len(all_rows)
    rows_with_bc          = sum(1 for r in all_rows if r["verdict"] == "BREAKING CHANGES DETECTED")
    rows_no_bc            = sum(1 for r in all_rows if r["verdict"] == "NO BREAKING CHANGES")
    rows_inconclusive     = sum(1 for r in all_rows if r["verdict"] == "INCONCLUSIVE")
    total_methods_tested  = sum(r["summary"]["total_methods_tested"] for r in all_rows)
    total_breaking_methods = sum(r["summary"]["breaking_changes"] for r in all_rows)

    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_rows":              total_rows,
            "rows_with_bc":            rows_with_bc,
            "rows_no_bc":              rows_no_bc,
            "rows_inconclusive":       rows_inconclusive,
            "total_methods_tested":    total_methods_tested,
            "total_breaking_methods":  total_breaking_methods
        },
        "rows": all_rows
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"json report written to {output_path}")

    # write markdown report
    markdown = generate_markdown(report)
    md_path  = Path(output_path).with_suffix(".md")
    with open(md_path, "w") as f:
        f.write(markdown)

    print(f"markdown report written to {md_path}")

    # write to github actions step summary if available
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "w") as f:
            f.write(markdown)
        print("written to github actions step summary")

    # print final summary
    print(f"\nfinal report summary")
    print(f"  rows analyzed          : {total_rows}")
    print(f"  breaking changes found : {rows_with_bc}")
    print(f"  no breaking changes    : {rows_no_bc}")
    print(f"  inconclusive           : {rows_inconclusive}")
    print(f"  methods tested         : {total_methods_tested}")
    print(f"  breaking methods       : {total_breaking_methods}")

    # write outputs for github actions
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"bc-report={output_path}\n")
            f.write(f"breaking-changes-detected={total_breaking_methods}\n")
            f.write(f"rows-with-bc={rows_with_bc}\n")


if __name__ == "__main__":
    results_dir = sys.argv[1]   # /tmp/llmbreakguard/results
    output_path = sys.argv[2]   # /tmp/llmbreakguard/results/bc_report.json

    generate_report(results_dir, output_path)