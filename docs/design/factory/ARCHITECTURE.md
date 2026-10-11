# Factory service — architecture as built

Status: contract for the build on `factory/page` (2026-10-10). Every lane reads this before it codes. When the code and this file disagree, fix one of them in the same change.

Companion documents: `PRODUCT.md` (what and why), `FACTORY-YAML.md` (the pipeline file), `DESIGN-BRIEF.md` (the dashboard, for the designer).

---

## 1. The shape

```
                 ┌──────────────── factory (server) ────────────────┐
  GitHub ◄─poll──┤ sources · intake · leases · store · record · api  │
                 │ serves: /v1/* (JSON + SSE)  and  /  (dashboard)   │
                 └──────▲───────────────▲───────────────▲───────────┘
                        │ /v1 client    │ /v1 + /        │ /v1/workers (long-poll)
                   codeaf TUI       browser / phone   codeaf worker loop
                   (desk)           (the wall)        (executes, with its person's keys)
```

One binary. `codeaf factory` runs the server. Plain `codeaf` is the desk and the worker, **always a client of a factory**: the one it starts itself on loopback, or the one it is attached to. There is no solo code path: the TUI never reads the store directly, and no process owns execution (`run.lock` and the store mailbox are gone).

What stays where it is: `internal/factory` (types, LocalSeam, recipe, policy), `internal/factory/run` (the runner and the stage executors), `internal/factory/store` (JSON files), `internal/factory/github` (the source), `internal/tui3/factory*` (the page). The page keeps calling `factory.Seam`; only *which* Seam it is given changes.

## 2. Vocabulary: outside vs inside

The API and the dashboard speak the product vocabulary. Go internals keep their names for now; renaming 25k lines is not this wave's job. The codec is the one place that maps.

| API / UI | Go today | note |
|---|---|---|
| job | `factory.Item` | `Item.ID` is the job id |
| step | `factory.Stage` (definition) · `factory.Phase` (run state) | |
| pipeline | `factory.Recipe` | |
| claim | `factory.Claim` | `Item.Proof` |
| question | `Item.Question` + `QKind` | one open question at a time today; the thread carries history |
| state | `Item.State` | `new`→**backlog**, `queued`, `running`, `needs you`→**needs_you**, `landed`→**needs_you** (sub-state `ready`), `shipped`→**done**, `dismissed`→**dropped** |
| worker | — (new) | |
| record / audit | — (new, `events.jsonl`) | |
| cost | `Stream.Spent`, `Item.Cap` | |

## 3. Packages (new)

| package | holds | depends on |
|---|---|---|
| `internal/factory/api` | wire types (`Queue`, `Job`, `Step`, `Claim`, `Question`, `Message`, `Event`, `Worker`, `Repo`, `Pipeline`, `DryRun`), the codec from `factory.Snapshot`/`Item`, the verb names | `internal/factory` |
| `internal/factory/serve` | `http.Handler` over a `factory.Seam` + a `store.Store`: `/v1/*`, SSE, the worker endpoints, the record, access check; mounts the dashboard at `/` | `api`, `store` |
| `internal/factory/client` | a `factory.Seam` over HTTP (+ SSE for change ticks) | `api` |
| `internal/factory/serve/web` | the dashboard: Vite + React + TypeScript + Tailwind + shadcn; `dist/` committed and `go:embed`ded | — |
| `internal/factory/lease` | the claims desk: one lease per job, expiry, re-queue, the signal channel, `leases.json`; `Doors`, the runner doors every seam hangs | `store`, `run` |
| `internal/factory/worker` | the worker loop: attach, pull, run with this machine's Runner over a mirror store, report the runner's writes (`run.Diff`), heartbeat, carry signals, done | `client`, `run`, `lease` |
| `internal/factory/record` | append-only `events.jsonl`, bound approval ids (`evt_…`), export | `store` |
| `internal/factory/pipeline` | `.codeaf/factory.yaml` reader, `.md` conversion, `start:` evaluation, dry run | `internal/factory` (Recipe) |

`cmd/codeaf` gains `factory.go` dispatch for `codeaf factory …` and `attach`/`detach`, and the launch logic in §6.

## 4. The API (`/v1`)

All JSON. Errors: `{error: "sentence"}` with the HTTP status. Ids are the job ids (`int`). Every response carries `factory: {address, version}`.

### Read

| method path | returns | from |
|---|---|---|
| `GET /v1/queue` | `Queue{since, jobs[], repos[], strip, capacity, me}` | `Seam.Load` |
| `GET /v1/jobs/{id}` | `Job` (full: steps, results, claims, questions, cost, pr) | `Seam.Load` filtered |
| `GET /v1/jobs/{id}/thread` | `[]Message` (the manager thread) | `Seam.Talk` transcript, read-only |
| `GET /v1/events` | SSE: `changed` (job id or `*`), `job` (a `Job` delta), `question`, `needs_you` | store watch |
| `GET /v1/repos` | watched + available | `Seam.Repos` |
| `GET /v1/repos/{owner}/{repo}/pipeline` | `Pipeline` (the parsed file, or defaults + `source: "defaults"`) | `pipeline` |
| `GET /v1/repos/{owner}/{repo}/dryrun` | `DryRun{would_start[], would_ask[]}` | `pipeline` + `Seam.Load` |
| `GET /v1/github` | `{login, via, polling_every}` | `Seam.GitHub` |
| `GET /v1/audit?since=` | `[]Event` | `record` |
| `GET /v1/me` | `{handle, machines[], takes_unassigned{}}` | sessions + workers |

### Verbs (`POST /v1/jobs/{id}/<verb>`, body as noted)

| verb | body | Seam |
|---|---|---|
| `run` | — | `Launch` |
| `stop` · `pause` · `resume` · `drop` | — | `Stop` · `Hold(on)` · `Hold(off)` · `Dismiss` |
| `answer` | `{yes?: bool, words?: string}` | `Answer` |
| `steer` | `{words}` | `Steer` (reply-level; records in the thread) |
| `approve` | `{edited?: bool}` → `{event: "evt_…"}` | `SignOff` (proof gate) or `Answer(yes)` (plan gate); the record writes the event first |
| `request_changes` | `{words}` | `SendBack` |
| `recheck` | — | `Reverify` |
| `take` | — | `lease.Assign(me)` |
| `prioritize` | `{to: "top"}` | `Mark` |
| `reply` | `{words}` | `Say` |

Also: `POST /v1/jobs {repo, title, text, autostart?}` → `Seam.New`; `PUT /v1/repos {watched[]}` → `SetRepos`; `POST /v1/github/connect {token}` → `ConnectGitHub`.

### Workers

| method path | who | what |
|---|---|---|
| `POST /v1/workers/attach {name, handle}` | a codeaf | → `{worker_id, credential}`; loopback: no access token needed; off-loopback: the access token |
| `GET /v1/workers/pull` | a worker (bearer credential) | long-poll (≤ 30s): `{lease, job}` or `204`. A lease is one job for one worker, with `expires_at`; heartbeat renews. Jobs are offered only to workers of the job's person, or any worker that opted into unassigned for that repo. |
| `POST /v1/workers/leases/{id}/heartbeat` | worker | renews; expired leases re-queue |
| `POST /v1/workers/leases/{id}/report` | worker | step results, log lines, cost, claims, questions, PR facts — the runner's writes, over the wire |
| `POST /v1/workers/leases/{id}/done` | worker | final state |

Solo uses exactly these over loopback. The runner (`internal/factory/run`) keeps its state machine; its *store writes* become reports, and its *ownership* becomes a lease.

**The worker's write path.** The worker's Runner runs over a mirror store on the worker's machine holding only the jobs it leased. Every `ReportEvery` (500 ms) the worker diffs the mirror against what it last sent (`run.Diff`) and posts the difference: changed steps (state, round, result), new log lines, new cost, changed claims, a new question for the person, the PR, the manager conversation, added notes, and `pipeline` when the runner reshaped the steps (the server refuses one that removes an approve step). Two things never ride a report: the job's state (the server moves it on a person's verb and on `done`) and an approve step passing (only the person's answer passes it). The final report leaves out the landing line, which `done` writes.

**Starting a pulled job.** A pull carrying `recheck` re-runs a landed job's checks (`Runner.Reverify`); a job whose run has not begun is launched (`Runner.Launch`, so the manager shapes it); one whose run had begun — a resume, a request for changes, a worker that went away — goes on at its step (`Runner.Hold(off)` → revive).

**The signal channel (server → worker).** A person's verb reaches the server, never the worker. The server's runner doors are `lease.Doors`: each changes the job's state in the store under the desk's lock and, when a worker holds the job, appends a signal to `leases.json` (`signals: {job: [{verb, yes, words}]}`). The heartbeat answer `{factory, lease, signals}` drains them (a worker beats every 2 s); a pull drains the ones left on a job before it was leased. The verbs:

| person's verb | server's write (`run` helpers) | signal to the holder | worker does |
|---|---|---|---|
| run | `QueueItem` → `queued` (a paused job: as resume) | — | pulls, `Launch` |
| stop | `StopItem` → `backlog`; the lease is **released** | — (the next beat or report answers 409) | `Runner.Stop`, lets go |
| pause / resume | `Stream.Paused`; unheld resume → `queued` | `hold {yes}` | `Runner.Hold` |
| answer | `AnswerHeld` (question down, an approve step passed on yes); unheld → `KeepAnswer` + `queued`; ask-first yes → run | `answer {yes, words}` | `Runner.Answer` |
| steer | — | `steer {words}` | `Runner.Steer` |
| approve (proof) | the record's approval read as the trailer (none: refused); `PublishItem` → `queued` (held: stays landed until done, which re-queues) | `publish {words: trailer}` (on the pull) | `Runner.Publish`: commit the worktree with the trailer, push the branch (never force), open the PR (a `pr` job: one comment); reports `pr`, done `done` → shipped beside the recorded approval, record `posted` |
| request changes | `SendBackItem` → `queued` | — | pulls, goes on |
| recheck | `RecheckItem` → `queued` | `recheck` (on the pull) | `Runner.Reverify` |

Signals are dropped with the lease (expiry, stop, done). Because the doors only write files under the desk's flock, every process with the store hangs them; there is no mailbox.

### Access

- Loopback: open. Off loopback: `Authorization: Bearer <access token>` (from `~/.codeaf/factory/config.yaml`), then sessions (later wave: GitHub device flow).
- The dashboard holds a session cookie after the token once.

## 5. Events and SSE

`record` is the source of truth for "what happened": `{id, at, kind, job, handle, cost, detail}` appended to `events.jsonl`. Approval ids are `evt_<8 hex>`; the publisher writes `Factory-Approval: evt_… @handle` as the PR trailer. `/v1/events` streams a `changed` tick whenever the store or the record changes (fs watch on the factory root, debounced 100 ms); clients re-`GET` what changed. No state rides on SSE.

## 6. Launch logic (`codeaf`, ordinary start)

```
attach.json present?  → client Seam → that url            (team, or your other machine)
serve.json live?      → client Seam → that url            (another codeaf on this machine serves)
else                  → start serve in-process on 127.0.0.1:0, write serve.json {url, pid, token}
                        → client Seam → loopback
worker loop           → attach to the same url, pull, run, report   (unless --no-worker)
```

`codeaf factory [--listen 127.0.0.1:7420] [--config ~/.codeaf/factory/config.yaml]` runs the server alone (a team box). `codeaf attach <url> [--name]` / `codeaf detach` write `attach.json`. `codeaf factory token|revoke|steps` are the ops verbs.

`run.lock` and the file mailbox are retired: every process's verbs go through `lease.Doors`, and execution happens wherever a worker pulls. The worker loop dials through the same order each time, so a worker whose serving window closed follows the factory to whoever serves next. Its credentials are cached per factory url and slot in `~/.codeaf/v3/factory/worker.json`; a 401 re-attaches. On loopback a worker takes every unassigned job (`takes_unassigned: {"*": true}`); attached elsewhere, only repos it has a checkout of. A process that serves in-process removes `serve.json` and releases `serve.lock` when its surface or session host ends.

**Local job doors on an attached floor.** `api.ComposeJobs` leaves TUI-only
job-id doors nil outside loopback unless `Seam.Job(id)` finds a live lease on
this machine. Workers keep a factory-URL-bound lease receipt beside each job
mirror, renew it on heartbeat, and remove it when they let go. `Load` discovers
these mirrors off the UI loop; job selection reads cached capabilities, and
each captured door checks its receipt again before acting on the mirror.
Matching ids on this machine's own floor grant nothing. Repository-local
doors remain local, and wired actions still reach the attached factory.

## 7. The dashboard build

`internal/factory/serve/web/`: `package.json`, `vite.config.ts`, `src/`. `make web` runs `npm ci && npm run build` → `dist/`, which is **committed** (like `pack.gz`) so Go CI needs no Node; a law test fails if `src/` is newer than `dist/`. Served at `/` by `serve`; API base is same-origin `/v1`. Stock shadcn, zinc, no theme; `data-testid` on every verb for the Playwright smoke.

## 8. Tests that gate every lane

- **Contract test** (`client_test.go`): `FixtureSeam` vs `client → serve → FixtureSeam`: identical `Snapshot` after every verb. Every exported `Seam` field is either wired or named in `api.TUIOnly` (a structural test fails on a new field that is neither).
- **The existing page tests** (`internal/tui3/factory*_test.go`, ~40) run against the client Seam over an in-process server.
- **E2E on a real model**: `TestTUIE2E/FactoryRunsAnIssue` on `santoshkumarradha/factory-demo` (private) with `CODEAF_E2E_MODEL=deepseek/deepseek-v4.1-flash`, plus a Playwright smoke per dashboard lane: open queue → open job → approve → the PR exists with the trailer.
- `make pr-ready` before any landing; `make build`; manual pages updated in the same change (the manual law).

## 9. Not in this wave

GitHub App and device-flow sessions, per-viewer filtering, overlap warnings, routed questions, relay/compute, hosted. The API shape leaves room (`handle` on every event and lease; `repo` on every job) and nothing else.
