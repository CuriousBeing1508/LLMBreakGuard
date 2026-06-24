"""
generate_prompt.py

PURPOSE:
    Reads the manifest and generates one prompt file per usage block.
    Prompts are written to the staged directory alongside where the
    generated test files will eventually live.

DESIGN DECISIONS:
    1. ONE PROMPT PER USAGE BLOCK
       - Each usage block represents one focal method that uses the library.
       - Generating one prompt per method keeps the LLM focused on a single
         method's behavior, producing more precise and targeted tests.
       - Reason: asking the LLM to generate tests for an entire class at once
                 produces vague tests. Per-method prompts produce sharper
                 assertions tied to specific API calls.

    2. FULL FOCAL CLASS SOURCE IN PROMPT
       - The full source of the focal class is appended to every prompt even
         though the prompt focuses on one method.
       - Reason: the LLM needs class-level context (fields, constructor,
                 other methods) to generate compilable tests. Without it the
                 LLM tends to invent fields or constructors that do not exist.

    3. METHOD SOURCE IN PROMPT
       - The exact source of the focal method is included separately from the
         full class source, highlighted at the top of the prompt.
       - Reason: guides the LLM to focus on that specific method while still
                 having full class context available.

    4. DEPENDENCY SIGNATURES DEDUPLICATED
       - Library usages are deduplicated before being added to the prompt.
       - Reason: Spoon sometimes reports the same usage multiple times from
                 different AST contexts. Duplicates waste prompt tokens and
                 confuse the LLM.

    5. PACKAGE DECLARATION IN PROMPT
       - The prompt explicitly instructs the LLM to use the test package name
         including the llmtests subpackage.
       - Reason: without this the LLM defaults to the original class package
                 which causes compilation failure after transplanting since
                 the file lives under llmtests/ on disk.

    6. NO MOCKING CONSTRAINT
       - The prompt explicitly forbids mocking frameworks.
       - Reason: mocked tests do not actually call the library under test and
                 therefore cannot detect breaking changes in the library API.

    7. PROMPT FILES SAVED ALONGSIDE STAGED TEST FILES
       - Prompt files are saved to the same staged directory as the generated
         test files, just with a _prompt.txt suffix.
       - Reason: keeps prompt and its output together for debugging. If a
                 generated test is wrong, the prompt that produced it is
                 right next to it.
"""

import re
import sys
import json
from pathlib import Path


def _split_params_smart(param_str):
    """Split comma-separated params respecting angle bracket nesting."""
    params, depth, current = [], 0, []
    for ch in param_str:
        if ch == '<':
            depth += 1
            current.append(ch)
        elif ch == '>':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            params.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        params.append(''.join(current).strip())
    return [p for p in params if p]


def parse_focal_method_signature(method_source, method_name):
    """
    Extracts method_name(ParamType1, ParamType2): ReturnType from source.
    Falls back to just method_name if parsing fails.
    """
    flat = ' '.join(method_source.split())

    pattern = (
        r'(?:(?:public|private|protected|static|final|synchronized|'
        r'abstract|native|default|transient|volatile|strictfp|@\w+)\s+)*'
        r'([\w<>\[\]?,\s.]+?)\s+'
        r'(?<!\.)' + re.escape(method_name) + r'\s*\('
    )
    m = re.search(pattern, flat)
    if not m:
        return method_name

    return_type = m.group(1).strip()

    # find matching ) starting from right after the opening (
    start, depth, i = m.end(), 1, m.end()
    while i < len(flat) and depth > 0:
        if flat[i] == '(':
            depth += 1
        elif flat[i] == ')':
            depth -= 1
        i += 1

    param_str = flat[start:i - 1].strip()
    if param_str:
        param_types = []
        for p in _split_params_smart(param_str):
            p = re.sub(r'@\w+\s*', '', p).strip()
            parts = p.rsplit(None, 1)
            param_types.append(parts[0].strip() if len(parts) == 2 else p)
        params_str = ', '.join(param_types)
    else:
        params_str = ''

    return f"{method_name}({params_str}): {return_type}"


def build_dependency_signatures(library_usages):
    """
    Builds a deduplicated list of dependency method signatures
    from the library usages in a usage block.
    """
    dep_set = set()

    for u in library_usages or []:
        usage_type = u.get("usageType", "")
        fq         = u.get("fullyQualifiedName", "Unknown")

        if usage_type == "method_call":
            args = ", ".join(u.get("argumentTypes") or [])
            ret  = u.get("returnType", "Unknown")
            dep_set.add(f"{fq}({args}): {ret}")

        elif usage_type == "type_reference":
            dep_set.add(f"{fq}   // type reference")

        elif usage_type == "constructor_call":
            args = ", ".join(u.get("argumentTypes") or [])
            dep_set.add(f"{fq} constructor({args})")

        elif usage_type == "field_access":
            dep_set.add(f"{fq}   // field access")

    return sorted(dep_set)


def generate_prompt(row_entry, class_entry, usage_block):
    """
    Builds the full prompt string for one usage block.
    """
    test_class_name   = usage_block["test_class_name"]
    test_package_name = class_entry["test_package_name"]
    method_name       = usage_block["method_name"]
    method_source     = usage_block["method_source"]
    focal_class_source = usage_block["focal_class_source"]
    library_usages    = usage_block["library_usages"]
    testing_framework = row_entry["testing_framework"]
    library_name      = row_entry["library_name"]
    old_version       = row_entry["old_version"]
    new_version       = row_entry["new_version"]
    client_class_fqn  = class_entry["class_fqn"]

    method_sig       = parse_focal_method_signature(method_source, method_name)
    focal_method_fqn = f"{client_class_fqn}#{method_sig}"

    deps = build_dependency_signatures(library_usages)
    deps_str = (
        "\n".join(f"    {d}" for d in deps)
        if deps else "    // none"
    )

    prompt = f"""---- 1. Metadata ----
- Client Project  : {row_entry['client_name']}
- OSS Library     : {library_name}
- Old Version     : {old_version}
- New Version     : {new_version}

---- 2. Program context ----
- Focal class FQN    : {client_class_fqn}
- Focal method FQN   : {focal_method_fqn}
- Test package       : {test_package_name}
- Test class name    : {test_class_name}

---- 3. Focal method source ----
{method_source}

---- 4. Library API calls made by this method ----
{deps_str}

---- 5. Test code format ----
/* Test framework: {testing_framework} */

package {test_package_name};

public class {test_class_name} {{

    @Test
    public void test_{method_name}_<scenario>() {{
        // Arrange: set up necessary objects

        // Act: call the focal method

        // Assert: strong deterministic checks
        //         (values, ordering, exception types, invariants)
    }}
}}

---- 6. Test goal ----
/**
 * PURPOSE
 * -------
 * Detect whether upgrading {library_name} from {old_version} to {new_version}
 * breaks the client's existing usage of the library.
 *
 * A breaking change means: code that worked correctly with {old_version} no
 * longer works, or produces different observable output, with {new_version}.
 * A test detects this by PASSING on {old_version} and FAILING on {new_version}.
 * A test that passes on both versions detects nothing — write zero such tests.
 *
 * WHAT TO TEST: THE CLIENT'S USAGE CHAIN, NOT THE LIBRARY IN ISOLATION
 * ---------------------------------------------------------------------
 * The focal method (section 3) calls the library APIs listed in section 4.
 * Your tests must replicate that same usage chain with concrete inputs and
 * then assert on the OBSERVABLE OUTPUT or SIDE EFFECT it produces.
 *
 * Do NOT write tests that call each library API in isolation as a smoke test.
 * What matters is the result of the client's usage: a returned value, a
 * transformed object, a written resource, or a thrown exception. Assert on
 * the CONTENT and SHAPE of those results.
 *
 * STEP-BY-STEP REASONING (apply this to every test you write)
 * ------------------------------------------------------------
 * Step 1 — Identify the usage chain.
 *   Read section 3. Find the sequence of library calls the focal method makes
 *   and the final result it computes (return value, state change, side effect).
 *
 * Step 2 — Choose a simple, old-version-safe input.
 *   Tests run on {old_version} FIRST. If a test cannot run on {old_version},
 *   it is discarded entirely and detects nothing.
 *   Choose the simplest input that exercises the library calls:
 *   · Prefer scalar or primitive types: String, Integer, Boolean, Long, Double.
 *   · If a POJO is needed, define a small inner class with only String or
 *     primitive fields — no generic type parameters, no collection fields.
 *   · Avoid Map<K,V>, Optional<T>, List<T>, or any type with unbound generic
 *     type parameters as the primary input. These may trigger internal library
 *     code paths that rely on features absent from {old_version}, causing a
 *     JVM-wide class-initialisation failure that crashes every test in the run.
 *   · Avoid using library features or annotations that did not exist in
 *     {old_version}. If unsure, stay with the core API shown in section 4.
 *
 * Step 3 — Replicate the usage chain in the test.
 *   Call the same library APIs in the same order with the same configuration
 *   as the focal method does. Do not skip configuration steps (e.g. if the
 *   client registers a module or sets a flag, your test must do so too).
 *   If a configuration step may fail on {old_version} (e.g. registering an
 *   optional module), guard it with try-catch so it does not abort the test.
 *
 * Step 4 — Assert on the concrete output.
 *   Look at what the usage chain PRODUCES and assert on its content:
 *   · String output   → assert on substrings, format, length, or parsed
 *                       structure (e.g. parse as JSON and check field values).
 *   · Numeric output  → assertEquals on the exact value or a tight range.
 *   · Collection      → assert on size, specific elements, ordering.
 *   · Object          → call getter/inspection methods; assertEquals on fields.
 *   · Boolean/flag    → assertEquals(true/false), not just assertNotNull.
 *   · Exception path  → use expectedException or try/catch; assert the exact
 *                       exception class and a stable message substring.
 *   · Side effect     → check the state that was changed (a list that grew,
 *                       a file that was written, a flag that flipped).
 *
 * Step 5 — Ask the version-sensitivity question.
 *   Before finalising each assertion, ask: "Could this assertion ever fail if
 *   {library_name} changed how it handles this input between versions?"
 *   If the answer is NO — the assertion would pass on every version ever
 *   shipped — discard the test and write a more specific one.
 *
 * STRICTLY FORBIDDEN (these tests detect nothing)
 * ------------------------------------------------
 * - assertNotNull(x) as the ONLY assertion in a test.
 * - assertTrue(x != null) as the ONLY assertion.
 * - Asserting only that a method did not throw an exception.
 * - Catching all exceptions with an empty or pass-through catch block.
 * - Asserting only on the return type or class of an object, not its content.
 * - Any assertion guaranteed to pass regardless of library version.
 *
 * FORMAT REQUIREMENTS
 * -------------------
 * - Output ONLY a complete compilable Java test class.
 * - First line MUST be: package {test_package_name};
 * - Class name MUST be exactly: {test_class_name}
 * - Use ONLY {testing_framework} annotations and assertions.
 * - Do NOT use mocking or stubbing (Mockito, EasyMock, etc.).
 * - Do NOT leave empty catch blocks.
 * - Do NOT include unused imports.
 * - All braces must be properly closed.
 * - Do NOT output explanations or text outside the Java class.
 */

---- 7. Full focal class source (for context) ----
{focal_class_source}
"""
    return prompt


def generate_all_prompts(manifest_path):
    with open(manifest_path) as f:
        manifest = json.load(f)

    total_prompts = 0

    for row_entry in manifest["rows"]:
        row_num = row_entry["row_index"]

        for class_entry in row_entry["classes"]:
            class_fqn = class_entry["class_fqn"]

            for usage_block in class_entry["usage_blocks"]:
                test_class_name = usage_block["test_class_name"]
                staged_path     = Path(usage_block["staged_path"])

                # prompt file lives alongside the staged test file
                prompt_path = staged_path.parent / f"{test_class_name}_prompt.txt"
                prompt_path.parent.mkdir(parents=True, exist_ok=True)

                prompt = generate_prompt(row_entry, class_entry, usage_block)

                with open(prompt_path, "w", encoding="utf-8") as f:
                    f.write(prompt)

                print(f"  row {row_num} | {test_class_name} -> {prompt_path}")
                total_prompts += 1

    print(f"\nprompts generated: {total_prompts}")
    print(f"written alongside staged test files")


if __name__ == "__main__":
    manifest_path = sys.argv[1]   # /tmp/llmbreakguard/manifest.json
    generate_all_prompts(manifest_path)