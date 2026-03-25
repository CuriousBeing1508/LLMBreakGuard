import os
import sys
import json
import xml.etree.ElementTree as ET
import re

# ── Maven namespace ────────────────────────────────────────────
MAVEN_NS = {"m": "http://maven.apache.org/POM/4.0.0"}

def detect_test_source_root(client_dir):
    standard = os.path.join(client_dir, "src", "test", "java")
    if os.path.exists(standard):
        return "src/test/java"

    # Walk to find non-standard test root
    for root, dirs, _ in os.walk(client_dir):
        for d in dirs:
            if d == "test":
                candidate = os.path.join(root, d, "java")
                if os.path.exists(candidate):
                    rel = os.path.relpath(candidate, client_dir)
                    print(f"[detect] Non-standard test root found: {rel}")
                    return rel

    # Default even if not yet created — Maven will create it
    print("[detect] test source root not found, defaulting to src/test/java")
    return "src/test/java"


def detect_framework_from_pom(pom_path):
    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()

        # Try with namespace first, then without
        deps = (
            root.findall(".//m:dependency", MAVEN_NS) +
            root.findall(".//dependency")
        )

        for dep in deps:
            artifact = (
                dep.findtext("m:artifactId", namespaces=MAVEN_NS) or
                dep.findtext("artifactId") or ""
            ).lower()

            if "junit-jupiter" in artifact or "junit-platform" in artifact:
                return "junit5"
            if artifact == "junit":
                version = (
                    dep.findtext("m:version", namespaces=MAVEN_NS) or
                    dep.findtext("version") or ""
                )
                # junit 4.x → junit4
                if version.startswith("4"):
                    return "junit4"
                return "junit4"
            if "testng" in artifact:
                return "testng"

    except ET.ParseError as e:
        print(f"[detect] Failed to parse pom.xml: {e}")

    return None


def detect_framework_from_gradle(gradle_path):
    try:
        with open(gradle_path, encoding="utf-8") as f:
            content = f.read()

        if "junit-jupiter" in content or "junit5" in content:
            return "junit5"
        if re.search(r"junit:junit", content):
            return "junit4"
        if "testng" in content.lower():
            return "testng"

    except IOError as e:
        print(f"[detect] Failed to read gradle file: {e}")

    return None


def detect_testing_framework(client_dir, build_tool):
    if build_tool == "maven":
        pom_path = os.path.join(client_dir, "pom.xml")
        framework = detect_framework_from_pom(pom_path)
    else:
        # Try build.gradle first, then build.gradle.kts
        gradle_path = os.path.join(client_dir, "build.gradle")
        if not os.path.exists(gradle_path):
            gradle_path = os.path.join(client_dir, "build.gradle.kts")
        framework = detect_framework_from_gradle(gradle_path)

    if framework:
        print(f"[detect] testing_framework detected: {framework}")
        return framework

    # Safe default
    print("[detect] Could not detect testing framework, defaulting to junit5")
    return "junit5"


def detect_all(client_dir, build_tool):
    print(f"\n[detect] Scanning project at: {client_dir}")

    test_source_root   = detect_test_source_root(client_dir)
    testing_framework  = detect_testing_framework(client_dir, build_tool)

    result = {
        "testing_framework": testing_framework,
        "test_source_root":  test_source_root,
        "llm_tests_folder":  "bc_generated_tests"
    }

    print(f"[detect] Results:")
    print(f"         testing_framework : {result['testing_framework']}")
    print(f"         test_source_root  : {result['test_source_root']}")
    print(f"         llm_tests_folder  : {result['llm_tests_folder']}")

    return result


if __name__ == "__main__":
    client_dir  = sys.argv[1]
    build_tool  = sys.argv[2]   # passed from entrypoint after parsing CSV
    result      = detect_all(client_dir, build_tool)
    print(json.dumps(result))