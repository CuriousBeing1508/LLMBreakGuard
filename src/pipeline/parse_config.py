import csv
import sys
import json
import os

USER_REQUIRED_FIELDS = [
    "client_github_url",
    "pre_breaking_commit",
    "library_group_id",
    "library_name",
    "old_version",
    "new_version",
    "java_version",
    "build_tool",
    "build_tool_version",
    "testing_framework",
    "test_source_root",
    "llm_tests_folder"
]

RESOLVED_FIELDS = USER_REQUIRED_FIELDS + [
    "import_prefix",
]

VALID_BUILD_TOOLS = ["maven", "gradle"]
VALID_FRAMEWORKS  = ["junit5", "junit4", "testng"]


def derive_import_prefix(library_group_id):
    parts = library_group_id.strip().split(".")
    if len(parts) > 1:
        return ".".join(parts[:-1])
    return library_group_id


def parse_github_url(url):
    try:
        parts       = url.strip().rstrip("/").split("/pull/")
        clone_url   = parts[0] + ".git"
        pull_number = parts[1]
        return clone_url, pull_number
    except IndexError:
        return None, None


def validate_row(row, row_number, is_resolved=False):
    errors  = []
    cleaned = {}

    required = RESOLVED_FIELDS if is_resolved else USER_REQUIRED_FIELDS

    for field in required:
        value = row.get(field, "").strip()
        if not value:
            errors.append(f"  row {row_number}: missing required field '{field}'")
        else:
            cleaned[field] = value

    if errors:
        return None, errors

    # validate client_github_url
    if "/pull/" not in cleaned["client_github_url"]:
        errors.append(
            f"  row {row_number}: client_github_url must be a PR url "
            f"e.g. https://github.com/org/repo/pull/77"
        )
    else:
        clone_url, pull_number = parse_github_url(cleaned["client_github_url"])
        if not clone_url or not pull_number:
            errors.append(
                f"  row {row_number}: could not parse clone url and pull number "
                f"from client_github_url"
            )
        else:
            cleaned["clone_url"]   = clone_url
            cleaned["pull_number"] = pull_number

    # validate build_tool
    if cleaned.get("build_tool", "").lower() not in VALID_BUILD_TOOLS:
        errors.append(
            f"  row {row_number}: invalid build_tool '{cleaned.get('build_tool')}' "
            f"must be one of {VALID_BUILD_TOOLS}"
        )
    else:
        cleaned["build_tool"] = cleaned["build_tool"].lower()

    # validate testing_framework only in resolved config
    if is_resolved:
        if cleaned.get("testing_framework", "").lower() not in VALID_FRAMEWORKS:
            errors.append(
                f"  row {row_number}: invalid testing_framework "
                f"'{cleaned.get('testing_framework')}' "
                f"must be one of {VALID_FRAMEWORKS}"
            )
        else:
            cleaned["testing_framework"] = cleaned["testing_framework"].lower()

    # validate java_version is a number
    try:
        int(cleaned["java_version"])
        cleaned["java_version"] = str(int(cleaned["java_version"]))
    except ValueError:
        errors.append(
            f"  row {row_number}: java_version '{cleaned.get('java_version')}' "
            f"must be a number e.g. 17, 11, 21"
        )

    # derive import_prefix for user to validate in detect mode
    if not is_resolved:
        cleaned["import_prefix"] = derive_import_prefix(
    cleaned["library_group_id"]
)

    cleaned.setdefault("llm_tests_folder", "bc_generated_tests")

    return cleaned, errors


def parse_config(config_path, is_resolved=False):
    if not os.path.exists(config_path):
        print(f"config file not found: {config_path}")
        sys.exit(1)

    rows       = []
    all_errors = []
    row_number = 0

    with open(config_path, newline="", encoding="utf-8") as f:
        lines = [l for l in f if not l.strip().startswith("#")]

    reader = csv.DictReader(lines)

    if reader.fieldnames is None:
        print(f"csv file is empty: {config_path}")
        sys.exit(1)

    required        = RESOLVED_FIELDS if is_resolved else USER_REQUIRED_FIELDS
    missing_headers = [f for f in required if f not in reader.fieldnames]
    if missing_headers:
        print(f"csv is missing columns: {missing_headers}")
        print(f"found columns: {list(reader.fieldnames)}")
        sys.exit(1)

    for row in reader:
        row_number += 1

        if not any(v.strip() for v in row.values()):
            continue

        cleaned, errors = validate_row(row, row_number, is_resolved)
        if errors:
            all_errors.extend(errors)
        else:
            rows.append(cleaned)

    if all_errors:
        print(f"\nfound {len(all_errors)} error(s) in {config_path}:\n")
        for error in all_errors:
            print(error)
        print(f"\nplease fix the above errors and re-run\n")
        sys.exit(1)

    if not rows:
        print(f"no valid rows found in {config_path}")
        sys.exit(1)

    print(f"parsed {len(rows)} valid row(s) from {config_path}", file=sys.stderr)
    return rows


if __name__ == "__main__":
    config_path = sys.argv[1]
    is_resolved = "--resolved" in sys.argv
    rows        = parse_config(config_path, is_resolved)
    print(json.dumps(rows))