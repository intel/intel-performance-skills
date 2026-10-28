Our `solver` batch job is slower than we would like on the `case1` input, and
before anyone starts changing code I want a write-up of where its time goes.

I profiled it on a production host; everything I collected is in
`/app/profiles/` (`CAPTURE.txt` lists the exact commands), and the solver sources
are in `/app/src/`. That host is not reachable from here and `perf` is not
installed in this environment, so work from the recorded files.

Write a hotspot report I can share with the team to `/app/hotspot_report.md`
(Markdown). This is analysis only: do not modify the sources or the profiles.
