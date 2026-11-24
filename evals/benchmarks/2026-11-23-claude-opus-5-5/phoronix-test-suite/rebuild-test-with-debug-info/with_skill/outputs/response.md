## pts/fluxsim rebuilt with debug info

`phoronix-test-suite batch-run pts/fluxsim` now runs a binary built with **`-O2 -g`** (`pts/fluxsim` resolves to `pts/fluxsim-1.2.0`). That's the same optimization level as before, plus debug info. On macOS the debug info lives in a separate `fluxsim-bin.dSYM` bundle, and it now sits next to the binary, which is where Instruments looks for it.

### What changed
Everything is in `pts-root/installed-tests/pts/fluxsim-1.2.0/`:

| File | Change |
|---|---|
| `fluxsim-bin` | Rebuilt with `install.sh`'s own build step plus `-g`: `cc -O2 -g … -o fluxsim-bin fluxsim.c -lm` |
| `fluxsim-bin.dSYM/` | New. The debug info for this binary (UUID `D55CE939-32AC-3A66-A3D5-817282794B3D`, same as the binary). |
| `src/fluxsim/` | Source re-extracted from the cached tarball and kept. The debug info points here, so Instruments can show source. |
| `fluxsim-bin.orig` | Backup of the original binary without debug info (safe to delete) |
| `pts-install.json` | Compiler options now recorded as `-O2 -g` (was `-O2`) |
| `fluxsim` (entry script) | Untouched (checksum verified) |

### Why not just `CFLAGS=-g phoronix-test-suite force-install pts/fluxsim`?
The profile honors CFLAGS, but on macOS that reinstall leaves you with no usable debug info:
- clang writes the debug info to `fluxsim-bin.dSYM` in the build directory.
- `install.sh` copies out only `fluxsim-bin`, then runs `rm -rf fluxsim`, which deletes the dSYM.

I tried it in a throwaway copy of `pts-root`. The new binary pointed at a temporary object file that no longer existed, and symbol lookup with `atos` returned only `main + 0`.

So I rebuilt by hand instead. The source went into `src/` rather than being extracted in place, because the tarball's `fluxsim/` directory has the same name as PTS's `fluxsim` entry script.

### Checks
- **Same machine code.** The code section is the same size, and all 542 instructions match except four. Those four load `printf` format strings that moved by 2 bytes, because the binary embeds its build flags as text, now `-O2 -g`. The output checksum is identical: `2.129678810452e+03`.
- **batch-run uses the new binary and still works.** It scored 582.74 MCells/s against 579.75 before the rebuild (5 runs each, about 2.1% spread), so there's no performance change.
- **Instruments now shows names and source lines.** At `-O2` every kernel is inlined into `main`, which is why you only saw `main` plus addresses. A Time Profiler recording of the installed binary (made with `xctrace`, the command-line Instruments) now shows each kernel by name with its source line:

  | Kernel | Source | Share of samples |
  |---|---|---|
  | `column_flux` | fluxsim.c:51 | ~59% |
  | `residual` | fluxsim.c:63 | ~29% |
  | `diffuse` | fluxsim.c:41 | ~12% |

  `column_flux` walks the grid column by column, jumping 8 KB per step, so it's a likely first target. I also attached `sample` to the process `batch-run` launched, and its stacks resolved to `fluxsim.c` lines too.

### Profiling it
batch-run runs `./fluxsim-bin --iterations 1500 --grid 1024` in the install directory. To profile exactly that from the working directory:

```sh
cd pts-root/installed-tests/pts/fluxsim-1.2.0
xcrun xctrace record --template 'Time Profiler' --output fluxsim.trace --launch -- ./fluxsim-bin --iterations 1500 --grid 1024
open fluxsim.trace
```

Or choose that binary and those arguments as the target in Instruments.app. Attaching while `batch-run` is going is tight, because each run lasts only about 3 s. If you move the binary, take `fluxsim-bin.dSYM` with it.

### If PTS reinstalls the test
Any `force-install` or reinstall wipes the install directory and puts back the build without debug info. To redo the rebuild from the working directory (I tested this on a freshly reinstalled copy):

```sh
D=pts-root/installed-tests/pts/fluxsim-1.2.0
mkdir -p $D/src && tar -xzf $D/fluxsim-1.2.tar.gz -C $D/src
make -B -C $D/src/fluxsim CFLAGS="-O2 -g"
cp $D/src/fluxsim/fluxsim-bin $D/fluxsim-bin.new && mv -f $D/fluxsim-bin.new $D/fluxsim-bin
rm -rf $D/fluxsim-bin.dSYM && cp -R $D/src/fluxsim/fluxsim-bin.dSYM $D/
```

The binary is copied to a new name and then renamed on purpose. Overwriting a signed binary in place can trip macOS's code-signing cache and get the process killed on launch.

Both runs are logged in `home/.pts-results.json` (labels `baseline` and `debug-info-O2-g`) and in `pts-root/test-results/`.
