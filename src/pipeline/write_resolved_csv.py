"""
write_resolved_csv.py

PURPOSE:
    Merges user-provided config values with auto-detected values
    and writes bc-config-resolved.csv for user review.
    Called during detect mode after detect_project_info.py runs.

DESIGN DECISIONS:
    1. USER VALUES ALWAYS WIN
       If the user already provided a value for a field in
       bc-config.csv it is never overwritten by auto-detected
       values.
       Reason: the user knows their project. Auto-detection is
       a convenience not an override.

    2. IMPORT_PREFIX DERIVED AND FLAGGED FOR VALIDATION
       import_prefix is derived from library_group_id by dropping
       the last segment and written to the resolved config.
       It is flagged in the confirmation table because it directly
       affects Spoon analysis correctness.
       Reason: if import_prefix is wrong Spoon will find no usages
       and the entire pipeline produces no results. Early validation
       prevents a silent empty run.

    3. EMPTY DETECTED VALUES WRITTEN AS EMPTY STRINGS
       If detection fails for a field it is written as an empty
       string not omitted from the CSV.
       Reason: the user can see exactly which fields need manual
       input. An omitted field is invisible. An empty field is
       obvious.

    4. CONFIRMATION TABLE PRINTED TO STDOUT
       A human readable table is printed showing each field,
       its value, and whether it was user-provided, auto-detected,
       derived, or needs manual input.
       Reason: this table appears in the GitHub Actions log and
       is the primary way the user knows what to review in the
       resolved CSV.

    5. RESOLVED CSV COLUMN ORDER FIXED
       Columns are always written in the same order regardless
       of input order.
       Reason: predictable column order makes the CSV easy to
       read and edit in any spreadsheet tool.
"""

import sys
import csv
import json
from pathlib import Path


RESOLVED_FIELD_ORDER = [
    "client_github_url",
    "pre_breaking_commit",
    "library_group_id",
    "library_name",
    "old_version",
    "new_version",
    "java_version",
    "build_tool",
    "build_tool_version",
    "import_prefix",
    "testing_framework",
    "test_source_root",
    "llm_tests_folder"
]

USER_FIELDS = [
    "client_github_url",
    "pre_breaking_commit",
    "library_group_id",
    "library_name",
    "old_version",
    "new_version",
    "java_version",
    "build_tool",
    "build_tool_version"
]

DETECTED_FIELDS = [
    "testing_framework",
    "test_source_root",
    "llm_tests_folder"
]

DERIVED_FIELDS = [
    "import_prefix"
]


def derive_import_prefix(library_group_id):
    parts = library_group_id.strip().split(".")
    if len(parts) > 1:
        return ".".join(parts[:-1])
    return library_group_id


def merge_row(user_entry, detected):
    """
    Merges one user config row with detected values.
    User values always win.
    Returns merged row and source map for printing.
    """
    merged  = {}
    sources = {}

    for field in RESOLVED_FIELD_ORDER:
        user_val     = user_entry.get(field, "").strip()
        detected_val = detected.get(field, "").strip()

        if field in USER_FIELDS:
            merged[field]  = user_val
            sources[field] = "user-provided"

        elif field in DERIVED_FIELDS:
            # derive import_prefix from library_group_id
            derived        = derive_import_prefix(user_entry.get("library_group_id", ""))
            merged[field]  = derived
            sources[field] = "derived — please validate"

        elif field in DETECTED_FIELDS:
            if user_val:
                # user overrode detected value
                merged[field]  = user_val
                sources[field] = "user-provided"
            elif detected_val:
                merged[field]  = detected_val
                sources[field] = "auto-detected"
            else:
                merged[field]  = ""
                sources[field] = "not detected — fill in manually"

        else:
            merged[field]  = user_val or detected_val
            sources[field] = "unknown"

    return merged, sources


def print_confirmation_table(merged, sources, row_num):
    print(f"\nrow {row_num}: {merged.get('client_github_url', '')}")
    print(f"  library: {merged.get('library_name')} "
          f"{merged.get('old_version')} -> {merged.get('new_version')}")
    print("")
    print(f"  {'field':<25} {'value':<35} {'source'}")
    print(f"  {'-'*25} {'-'*35} {'-'*25}")

    for field in RESOLVED_FIELD_ORDER:
        value  = merged.get(field, "")
        source = sources.get(field, "")
        flag   = " <- fill in" if "fill in" in source else ""
        flag   = " <- validate" if "validate" in source else flag
        print(f"  {field:<25} {value:<35} {source}{flag}")

    print("")


def write_resolved_csv(all_resolved_json, output_path):
    all_resolved = json.loads(all_resolved_json)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESOLVED_FIELD_ORDER)
        writer.writeheader()
        for row in all_resolved:
            writer.writerow({
                field: row.get(field, "")
                for field in RESOLVED_FIELD_ORDER
            })

    print(f"resolved config written to {output_path}")
    print(f"  {len(all_resolved)} row(s)")


if __name__ == "__main__":
    # sys.argv[1] -> path to configs.json   (from parse_config.py)
    # sys.argv[2] -> path to detected.json  (from detect_project_info.py)
    # sys.argv[3] -> output path for bc-config-resolved.csv
    all_configs_path = sys.argv[1]   # /tmp/llmbreakguard/configs.json
    detected_path    = sys.argv[2]   # /tmp/llmbreakguard/detected.json
    output_path      = sys.argv[3]   # $GITHUB_WORKSPACE/bc-config-resolved.csv

    with open(all_configs_path) as f:
        all_configs = json.load(f)

    with open(detected_path) as f:
        all_detected = json.load(f)

    all_resolved = []

    for i, user_entry in enumerate(all_configs):
        detected   = all_detected[i] if i < len(all_detected) else {}
        merged, sources = merge_row(user_entry, detected)
        print_confirmation_table(merged, sources, i + 1)
        all_resolved.append(merged)

    write_resolved_csv(
        json.dumps(all_resolved),
        output_path
    )