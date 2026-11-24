`pts/fluxsim` has been rebuilt with debug info, and `batch-run pts/fluxsim` now runs that binary. Instruments resolves it to the source line. It's still `-O2`, now with `-g`, and the machine code is the same as before, so your benchmark numbers are still comparable. `pts/fluxsim` resolves to the installed `pts/fluxsim-1.2.0`.

## Why plain `CFLAGS=-g` isn't enough on macOS

I tried `CFLAGS=-g ./bin/phoronix-test-suite force-install pts/fluxsim` on a scratch copy first. PTS reports success and records `-O2 -g`, but Instruments still gets no debug info:

- **The debug info gets deleted.** On macOS it never goes into the executable. clang writes it to a separate `fluxsim-bin.dSYM` bundle in the build directory. The profile's `install.sh` copies only `fluxsim-bin` and then runs `rm -rf fluxsim`, which deletes the dSYM. The binary is left pointing at a temporary `.o` file that no longer exists.
- **The source path can't exist.** The debug info records the source as `fluxsim/fluxsim.c` in the install dir, but `install.sh` puts the entry script, also called `fluxsim`, at that path. So the source view could never find the file.

## What I ran

From the directory that contains `bin/` and `pts-root/`:

```sh
DEST="$(pwd -P)/pts-root/installed-tests/pts/fluxsim-1.2.0"
CFLAGS="-g -fdebug-prefix-map=$DEST/fluxsim=$DEST/fluxsim-src -dsym-dir $DEST" \
  ./bin/phoronix-test-suite force-install pts/fluxsim
mkdir -p "$DEST/fluxsim-src" && tar -xzf "$DEST/fluxsim-1.2.tar.gz" -C "$DEST/fluxsim-src" --strip-components 1
```

- **`-g`:** PTS passes `CFLAGS` through to the build (`make CFLAGS="-O2 $CFLAGS"`), so this gives `-O2 -g`.
- **`-dsym-dir`:** clang writes the dSYM straight into the install dir, so the cleanup step doesn't delete it.
- **`-fdebug-prefix-map` plus the `tar` line:** the recorded source path now points to `fluxsim-src/`, and the 1.2 source is unpacked there, so Instruments' source view works.
- **No files edited:** I didn't change the test profile or `bin/phoronix-test-suite`. The install went through PTS's normal path, so `pts-install.json` records the flags. The long absolute paths in the recorded flags are expected.

The install dir `pts-root/installed-tests/pts/fluxsim-1.2.0/` now holds:
- `fluxsim-bin`
- `fluxsim-bin.dSYM`, with UUID `3E650611-B3D1-3174-B031-6C378107D3E9`, which matches the binary
- `fluxsim-src/`
- the `fluxsim` entry script, unchanged

## Verification

- **Debug info is valid:** the dSYM's UUID matches the binary, `dwarfdump --verify` finds no errors, and the code signature is valid.
- **lldb finds it automatically:** it loads the dSYM from next to the binary. It resolves the address `main+1668` to `[inlined] residual at fluxsim.c:62`, and `source list` shows the code.
- **Time Profiler works:** I recorded with `xctrace`, which is the command-line version of Instruments, using PTS's arguments.
  - Before: 99.9% of samples showed only as `main`, with no line numbers.
  - After: `column_flux` at fluxsim.c:52 took 66.8%, `residual` 21.9% and `diffuse` 11.3%, with the source file found.
  - Attaching to the `fluxsim-bin` process that `batch-run` starts gave the same result.
- **Code is unchanged:** the hot loops are instruction-for-instruction identical to the original binary. The only differences are a few string addresses in the final `printf` code, because the embedded build-flags string got longer. A test build with that string held fixed came out byte-identical.
- **Speed is unchanged:** five alternating runs averaged 581.8 MCells/s for the original and 580.1 for the rebuild, within the ±1.5% run-to-run noise. The checksums were identical.
- **`batch-run pts/fluxsim` works:** it averaged 380.18 MCells/s (runs of 367.45, 414.30 and 358.78).
  - The machine was busy at the time, with a load average of about 8; the original build gave 334 ±23% under the same conditions.
  - So treat the saved result in `pts-root/test-results/2026-11-23-174820-fluxsim/` as affected by that load. Delete it or re-run on a quiet machine if you want a reference number.

## Profiling it

- **Launch directly (easiest):** in Instruments, choose Time Profiler.
  - Set the target to `pts-root/installed-tests/pts/fluxsim-1.2.0/fluxsim-bin`, with arguments `--iterations 1500 --grid 1024`.
  - From the command line: `cd pts-root/installed-tests/pts/fluxsim-1.2.0 && xcrun xctrace record --template 'Time Profiler' --launch -- ./fluxsim-bin --iterations 1500 --grid 1024`, then open the `.trace` file.
- **During `batch-run`:** attach to `fluxsim-bin`. Each of the 3 trials is a new process, so you attach once per trial.
  - Don't launch `phoronix-test-suite` itself under Instruments. That only profiles the Python wrapper, and I got zero `fluxsim-bin` samples that way.
- **Inlined functions:** at `-O2` the main computation functions are inlined into `main`. Instruments still shows them by name, with line numbers.
- **First result:** most of the time (60–67%) is in `column_flux` line 52, which reads `next` one column at a time, jumping 8 KB per step.

## Notes

- If you copy the binary somewhere else, copy `fluxsim-bin.dSYM` with it.
- To go back to the normal build, run `./bin/phoronix-test-suite force-install pts/fluxsim` without `CFLAGS` set. Any `force-install` wipes the install dir, so re-run the commands above to get the debug build back.
- If you rebuild with other flags, keep `-dsym-dir <path>` last. Otherwise the sandbox PTS's flag logger records a stray `-g"` in `compiler-options`. That's only cosmetic.
- If you'd rather plain `CFLAGS=-g force-install` just worked, I can change the profile's `install.sh` to keep the `.dSYM`. I left the profile alone because it's the upstream file.
