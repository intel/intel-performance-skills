We moved our `ingestd` scoring service onto 32-core machines and it is barely
faster than it was on the old 8-core boxes. Adding worker threads stopped helping.

On a production host I ran a thread-count sweep and recorded CPU profiles at
1 thread and at 32 threads. Everything is in `/app/profiles/` (`CAPTURE.txt` says
how each file was produced), and the relevant source is in `/app/src/`. That host
is not reachable from here and `perf` is not installed in this environment, so
work from the recorded files.

Tell me what is keeping it from scaling, and what I should measure next on the
production host to confirm it before we start changing code. Write your answer
to `/app/diagnosis.json`:

```json
{
  "bottlenecks": [
    {"function": "<symbol name as it appears in the profiles>",
     "category": "<one of the categories below>",
     "evidence": "<short free text>"}
  ],
  "next_measurement": "<the perf command or measurement to run next, and what it should show>"
}
```

List every function you hold responsible for the lack of scaling, one entry each.
`category` is one of: `compute`, `memory_bandwidth`, `lock_spin`,
`shared_counter`, `false_sharing`, `shared_data`, `syscall_or_io`.

Do not modify anything under `/app/src/` or `/app/profiles/`.
