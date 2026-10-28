We are about to ship the scoring module in `/app/src` (`kernels.c`, `pool.cpp`,
`scoring.h`). It is the core of our embedding search service: one process per
host on multi-socket Xeon servers, 64-128 worker threads, millions of candidate
vectors scored per second. `make -C /app` builds it.

Before it goes out, review it for performance problems.

Write your review to `/app/review.json` as a JSON list with one entry per distinct
problem:

```json
[
  {"file": "src/example.c", "line": 42, "problem": "what is slow and why", "fix": "what to change"}
]
```

- `file` is the path relative to `/app`; `line` is the line where the problem is.
- Report only things you would ask the author to change before shipping; leave
  out code that is already fine.
- This is a review: do not modify the files under `/app/src`.
