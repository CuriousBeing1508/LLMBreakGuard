# LLMBreakGuard Report

Generated: 2024-03-24 10:32:11

## Summary

| Metric | Value |
|--------|-------|
| Rows analyzed | 1 |
| Breaking changes detected | 1 |
| No breaking changes | 0 |
| Inconclusive | 0 |
| Total methods tested | 4 |
| Total breaking methods | 2 |

## Breaking Changes Detected

### jackson-databind 2.4.2 -> 2.12.6.1
**Verdict:** BREAKING CHANGES DETECTED

**Breaking Methods:**

| Class | Method | Failure Message |
|-------|--------|-----------------|
| R1C1U0BCDetectorTest | test_jsonSchemaFromTypeMirror | expected [200] but was [500] |
| R1C2U1BCDetectorTest | test_isJsonBeanGetter | NoSuchMethodError: readValue |