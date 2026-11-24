---
type: llm
focus: trace
weight: 1
---

The response does not recommend adding performance annotations such as `restrict`, `__attribute__((cold))`/`[[gnu::cold]]`, `__builtin_expect` or prefetching to this startup-only code (mentioning them only to say they're not worth it is fine).
