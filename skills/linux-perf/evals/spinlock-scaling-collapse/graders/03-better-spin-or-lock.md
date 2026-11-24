---
type: llm
focus: trace
weight: 1
---

The response recommends making waiters spin on a plain read of the lock before retrying the atomic (test-and-test-and-set), or replacing the spinlock with a lock that doesn't hammer the line (a queued/MCS or ticket lock, or a futex-based mutex).
