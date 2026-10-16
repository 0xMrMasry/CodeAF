# Diagnostic profile — developer-only CPU and heap capture

For real performance investigation of one codeaf process. **Off unless you set
a variable.** Code: `internal/diagprofile`; the only hook is
`startDiagnostics` in `cmd/codeaf/diagnostics.go`, called first in `main`, so
every command (chat, do, exec, run, engine, app, ...) is covered and none has an
API, port or protocol field for it.

## Use

```sh
d=$(mktemp -d)                      # mode 0700, owned by you
CODEAF_DIAGNOSTIC_PROFILE_DIR=$d \
CODEAF_DIAGNOSTIC_PROFILE_SECONDS=20 \
CODEAF_DIAGNOSTIC_PROFILE_HEAP=1 \
  bin/codeaf do ...
go tool pprof -top bin/codeaf $d/codeaf-cpu-*.pprof
```

| Variable | Meaning |
| --- | --- |
| `CODEAF_DIAGNOSTIC_PROFILE_DIR` | Absolute, clean path. Unset or empty = off, and the other two are ignored. Created `0700` if missing (parent must exist). An existing directory must be a real directory (not a symlink), owned by you, with no group/other access; it is never chmod'ed. |
| `CODEAF_DIAGNOSTIC_PROFILE_SECONDS` | Optional whole seconds `1`..`60`, default `30`. Digits only. |
| `CODEAF_DIAGNOSTIC_PROFILE_HEAP` | Optional; exactly `1` also writes a heap profile. |

## Guarantees

- **Off means nothing.** No goroutine, file, directory, profiler or output.
- **A bad value refuses.** Exit status 2, one fixed sentence on stderr, e.g.
  `codeaf: diagnostic profile refused: the duration must be a whole number of seconds from 1 to 60`.
  The value is never quoted back. Nothing else runs.
- **Files** `codeaf-cpu-<utc>-p<pid>-<random64>.pprof` (and `-heap-`, same
  suffix), mode `0600`, created with `O_EXCL|O_NOFOLLOW`: never truncated,
  never through a symlink, and the pid plus a fresh random component mean a
  child process or a second run cannot collide with its parent's file.
- **Window.** The CPU profile starts at the top of `main` and ends after the
  interval, or when the command exits, whichever is first. Ending (an
  expensive flush) happens on the profiler's own goroutine; the exit waits for
  it, so a command that finishes early is delayed by that flush only.
- **Heap** is a snapshot of the runtime's existing allocation sampling at the
  end of the window (or exit). `runtime.GC` is not called and the sampling rate
  is not changed, so the data is as of the last completed collection.
- **stdout is never written**, so JSON protocols (`engine`, `app`, `--json`)
  are unaffected. Signals, exit codes and panic handling are unchanged; no
  signal handler is installed.
- **No metadata is added**: no environment, config, prompts or request bodies.
  A pprof file does contain the binary's path and Go symbol names.

## Limits

- **Children inherit the variables** (not cleared, deliberately): the engine
  daemon a chat launch spawns, or a `codeaf` a worker runs, profiles itself too
  into the same directory under its own names. Unset the variable for a child
  you do not want measured.
- A process ended by signal, by `os.Exit` outside `main` (for example the wall in
  `chatwall.go`), or by a runtime crash does not flush: its CPU file is
  incomplete or empty and its heap file absent. Let the command exit, or use
  `SECONDS` shorter than the run.
- One profiler per process: a second `pprof.StartCPUProfile` anywhere would
  refuse, and a codeaf started with the variable set under a profiled test run
  refuses.
- Function names are readable in a normal build (`make build`, `make debug`);
  the runtime symbolizes from its own tables, which `-s -w` does not strip. A
  `garble` build (the app sidecar, `make app-sidecar`) obfuscates package and
  function names, so such a profile is only as readable as that build; profile a
  non-garbled build of the same commit.
- The variables are read through `internal/env`, which also honours the
  retired spelling of any `CODEAF_` name; do not rely on it.
