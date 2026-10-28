`kvgate`, our request gateway, gets slower per request as we add worker threads:
going from 8 to 24 workers raised CPU use a lot and throughput barely moved.

A colleague captured a `perf c2c` report on a production host while it was under
load: `/app/profiles/c2c_report.txt` (`/app/profiles/CAPTURE.txt` has the commands).
The source files it points at are in `/app/src/`. That host is not reachable from
here and `perf` is not installed in this environment, so work from the recorded
report.

I want to hand the team a short list of what to change. Write it to
`/app/contention.json`, most important first:

```json
{
  "lines": [
    {"address": "<cache line address as printed in the report>",
     "sharing": "<true or false>",
     "data": "<struct or variable that lives there>",
     "fields": ["<the fields involved>"],
     "fix": "<the kind of change you recommend, one or two sentences>"}
  ]
}
```

`sharing` is `"true"` if the threads contend on the same datum and `"false"` if
they touch different data that happens to share a cache line. Include only the
cache lines you think are worth the team's time.

Do not modify anything under `/app/src/` or `/app/profiles/`.
