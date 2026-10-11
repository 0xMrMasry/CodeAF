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
| question | `Item.Question` + `QKind` | one open question; `Item.AnsweredQuestions` keeps the latest 100 answers `{by, text, at, event}` |
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
| `internal/factory/lease` | the claims desk: one lease per job, expiry, re-queue, the signal channel, `leases.json`; `Doors`, the runner doors every seam hangs | `store`, `run` |
| `internal/factory/worker` | the worker loop: attach, pull, run with this machine's Runner over a mirror store, report the runner's writes (`run.Diff`), heartbeat, carry signals, done | `client`, `run`, `lease` |
| `internal/factory/record` | append-only `events.jsonl`, bound approval ids (`evt_…`), export | `store` |
| `internal/factory/pipeline` | `.codeaf/factory.yaml` reader, `.md` conversion, `start:` evaluation, dry run | `internal/factory` (Recipe) |

`cmd/codeaf` gains `factory.go` dispatch for `codeaf factory …` and `attach`/`detach`, and the launch logic in §6.

## 4. The API (`/v1`)

All JSON. Errors: `{error, reason?, by?, at?, now_state?, request_id}`. `reason` is `not_allowed` (403), `not_found` (404), `conflict` or `stale` (409), or `bad_input` (422). A crash is 500 and has no `reason`. `request_id` is eight hex characters. Ids are the job ids (`int`). Every response carries `factory: {address, version}`. A job read includes `version` when a store holds the document.

### Read

| method path | returns | from |
|---|---|---|
| `GET /v1/queue` | `Queue{since, jobs[] (each live hold adds held_by), repos[], strip, capacity{machines_online, busy}, me, reason?}`. `since` includes `shipped`, `arrived`, `asked`, `handled`, `spent`, `spent_today` | `Seam.Load` plus the claims desk, then the caller's visible repositories; `spent_today` is the runner's day figure |
| `GET /v1/jobs/{id}` | `Job` (full: steps, results, claims, questions, cost, pr) | `Seam.Load` filtered |
| `GET /v1/jobs/{id}/thread` | `{factory, messages: []Message}` | `Item.Thread`, replicated from the holding machine; max 200 lines, 16 KiB each |
| `GET /v1/events` | SSE: `changed` (job id or `*`), `job` (a `Job` delta), `question`, `needs_you` | store watch |
| `GET /v1/repos` | watched + available | `Seam.Repos` |
| `GET /v1/repos/{owner}/{repo}/pipeline` | `Pipeline` plus `text`, `personal_text`, `personal`, `origin` (`main`, `working tree`, `defaults`), `start`, `size`, and each step's `until_sentence`. `until` stays `done`, `clean`, `green` or `proven` | `pipeline` |
| `GET /v1/repos/{owner}/{repo}/dryrun` | `DryRun{would_start[], would_ask[], would_wait[]}`. `would_wait[]` is `{job, reason}` | `pipeline` + `Seam.Load` |
| `GET /v1/github` | `{login, via, polling_every}` | `Seam.GitHub` |
| `GET /v1/about` | `{name, codeaf_version, contract, needs, access, polling_every, served_by}` before any token. `needs` is `token`, `device` or `none`. `access` is `loopback`, `token` or `tailnet`. `served_by` is `tui` or `factory` | host config |
| `GET /v1/audit?since=` | `[]Event` | `record` |
| `GET /v1/me` | `{codeaf_version, contract, handle, name, avatar_url, session_id, scopes[], machines[{name, os, codeaf_version, status, job, last_heartbeat}], takes_unassigned{}, access, polling_every, served_by}` | caller session; a token with no signed-in profile, the host token and open loopback use `Seam.PersonHandle`. `machines` is that handle only. `status` is `busy`, `idle` or `offline` (no heartbeat for three 2s intervals). `takes_unassigned` is the saved map, or `{}` |
| `PUT /v1/me/takes_unassigned` | `{repo: bool}` saved for that same handle; later pulls by that handle use it | the factory store. No store answers 501 |
| `GET /v1/members` | `{members: [{handle, name, avatar_url, joined_at, last_seen, sessions}]}` | live GitHub sign-ins, plus access-token rows that have bound a profile |
| `GET /v1/me/sessions` | `{sessions: [{id, device, created, last_seen, current}]}` | this caller's sign-ins; `device` is the User-Agent captured at sign-in |

Version skew: `codeaf_version` is the build stamp; `contract` is `api.Contract`, an integer starting at 1 that is bumped only on a breaking wire change. `/v1` only adds fields; additive fields never bump it. An app newer than the factory shows "this factory runs codeaf X; update it to use Y" and keeps the read-only views.

A signed-in GitHub viewer sees only repositories `GET https://api.github.com/repos/{owner}/{repo}` accepts for their token. The answer is cached for 10 minutes. Queue jobs, repositories, the strip, queued count, daily total, audit rows, `GET /v1/repos`, pipeline, dry run and a changed tick that names a job all use that set. Daily for a partial view is the sum of visible job costs, and since-counters are cleared because they are not stored per repository. `machines_online`, `busy` and `spent_today` stay floor-wide only when the viewer can see every repository; a narrower view leaves them at zero. A viewer who can see every repository still receives the factory-wide totals. A viewer who can see none receives HTTP 200 with an empty queue and `reason: "no_visible_repos"`. Loopback without a signed-in profile, and the host access token, are not filtered. A hidden job or repository answers 404.

### Verbs (`POST /v1/jobs/{id}/<verb>`, body as noted)

| verb | body | Seam |
|---|---|---|
| `run` | optional `expect` | `Launch` |
| `stop` · `pause` · `resume` | optional `expect` | `Stop` · `Hold(on)` · `Hold(off)` |
| `drop` | `{words?, expect?}` | `Dismiss`; words are the audit detail; a holder is signaled `stop` and the branch is kept |
| `answer` | `{yes?: bool, words?: string, expect?}` | `Answer` |
| `steer` | `{words, expect?}` | `Steer` (reply-level; records in the thread) |
| `approve` | `{edited?: bool, expect?}` → `{event: "evt_…"}` | `SignOff` (proof gate) or `Answer(yes)` (plan gate). A claim not shown refuses before any receipt. The proof receipt is written after that check and before the publish door, which reads it. A plan receipt is written only after the answer door accepts |
| `request_changes` | `{words, expect?}` | `SendBack` |
| `recheck` | optional `expect` | `Reverify` |
| `take` | optional `expect` | `lease.Assign(me)` |
| `release` | optional `expect` | `lease.Release(me)` — un-take; the branch is kept; audit `released` |
| `prioritize` | `{to: "top", expect?}` | `Mark` |
| `reply` | `{words, expect?}` | store a pending message; `reply {id, words}` signal on heartbeat/pull; worker acknowledgment changes it to delivered |

`expect` is `{state?, step?, version?}`. A field that no longer matches is HTTP 409 `{error, reason: "stale", by?, at?, now_state}`. The sentence is `already approved by @raj at 11:58`, `already taken by @mei`, or `job moved on while you looked`.

Also: `POST /v1/jobs {repo, title, text, autostart?}` → `Seam.New`; `PUT /v1/repos {watched[]}` → `SetRepos`; `POST /v1/github/connect {token}` → `ConnectGitHub`.

### Workers

**Change facts (V7, additive Contract 1).** Every job may carry
`change: {files: [{path, added?, removed?, status}], stat?: {files, added?, removed?}, branch?, tests?: [{step, passed?, failed?, skipped?}]}`.
The holding machine reads its worktree after completed rounds, including failed
checks and rechecks. Files include committed, staged, unstaged and untracked
paths, sorted by path without the GitHub detail list's twenty-file limit.
Statuses are `added`, `modified`, `removed`; renames report both paths. Binary
files have no line counts. The comparison is pinned before the first round:
an issue's initial HEAD, a PR's merge base with its target. `desk.change_base`
and worker `change_base` preserve that commit across leases and restarts.

Check counts come from explicit Go verbose/JSON outcomes, TAP outcomes, or a
pytest summary, read before the output tail is cut. A package's `ok`, green CI,
or exit 0 alone does not establish test counts. Each check's latest counts also
appear at `steps[].tests`; a later count-free run removes its previous counts.
No counts, zero totals, an empty stat or an unread change are omitted. A failed
worktree read keeps the last successful snapshot. The factory never reads a
remote machine's files itself; reports travel through the existing live lease
check, `run.Diff`, codecs and store. Jobs and queue reads expose identical facts.
Source PR jobs have optional `draft` (known true or false); published receipts
carry `pr.draft`. Existing fields and their meaning stay intact.

| method path | who | what |
|---|---|---|
| `POST /v1/workers/attach {name, handle, os?, codeaf_version?}` | a codeaf | → `{worker_id, credential}`; handle is the worker's connected GitHub login, resolved by `Seam.PersonHandle` as for take, approve and `/v1/me`; an absent handle uses the factory's connected login, never the OS account. Loopback: no access token needed; off-loopback: the access token |
| `GET /v1/workers/pull` | a worker (bearer credential) | long-poll (≤ 30s): `{lease, job}` or `204`. A lease is one job for one worker, with `expires_at`; heartbeat renews. Jobs are offered only to workers of the job's person, or any worker that opted into unassigned for that repo. Pending replies can lend an idle manager with `lease.thread_only: true`; the existing machine affinity still applies. |
| `POST /v1/workers/leases/{id}/heartbeat` | worker | renews and returns signals, including unacknowledged manager replies; expired leases re-queue |
| `POST /v1/workers/leases/{id}/report` | worker | step results, log lines, cost, claims, questions, PR facts, `thread` messages and `delivered` reply IDs — the worker's writes |
| `POST /v1/workers/leases/{id}/done` | worker | final state; a `thread_only` lease releases the manager without changing job state or steps |

Solo uses exactly these over loopback. The runner (`internal/factory/run`) keeps its state machine; its *store writes* become reports, and its *ownership* becomes a lease.

**The worker's write path.** The worker's Runner runs over a mirror store on the worker's machine holding only the jobs it leased. Every `ReportEvery` (500 ms) the worker diffs the mirror against what it last sent (`run.Diff`) and posts the difference: changed steps (state, round, result), new log lines, new cost, changed claims, a new question for the person, the PR, the manager conversation, added notes, and `pipeline` when the runner reshaped the steps (the server refuses one that removes an approve step). Two things never ride a report: the job's state (the server moves it on a person's verb and on `done`) and an approve step passing (only the person's answer passes it). The final report leaves out the landing line, which `done` writes.

**Replicated manager thread.** Workers read spoken manager lines on their own machine and report stable IDs in batches of at most 32. The factory merges repeated IDs once, bounds the thread to 200 messages (16 KiB per text), and never evicts pending replies. Reply writes directly to the job before signaling; heartbeat/pull offers it until `delivered` acknowledges its ID. A manager-only lease permits thread writes but no step results or costs, and done preserves the job. Offline managers keep replies pending; the pending words survive process restarts and lease expiry. `/answer` keeps the question in `questions` with `state: answered` and its answer receipt, clock and person (latest 100). All existing fields remain; Contract stays 1.

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
| shape | — when a worker holds the job. Nobody holding it: the server's Shape door runs the turn where that job's worker lives | `shape` | `Runner.ShapeAsked` (one turn, nothing launched) |
| drop | `Dismiss` → `dropped` | `stop` | `Runner.Stop` (the branch is kept); the worker does not report the job back as backlog |

Signals are dropped with the lease (expiry, stop, done). Because the doors only write files under the desk's flock, every process with the store hangs them; there is no mailbox. Shape is the one verb that is not only a lease door: a job nobody holds is shaped by the process serving the floor.

### Access

Loopback stays open as the owner. Remote sign-in requires an existing host or
access token or `/access` cookie; GitHub sign-in does not bypass that gate.
Host config `github_client_id` enables the OAuth app's device flow (which must
also be enabled at GitHub). Optional `team: [priya, "@sam"]` restricts signed-in
handles; others receive 403. The host token retains owner access.

| method path | body | returns |
|---|---|---|
| `POST /v1/auth/device` | absent or `{}` | `{factory, device_code, user_code, verification_uri, interval, expires_in}` |
| `POST /v1/auth/device/poll` | `{device_code}` with the original credential | 202 `{factory, error, interval}` while pending; 200 `{factory, access_token, token_type, expires_in, me}` plus HttpOnly cookie after GitHub `/user` succeeds |
| `DELETE /v1/me/sessions/{id}` | — | `{factory}`; 404 `session not found` when the id is not this caller's |
| `POST /v1/me/sessions/signout-others` | — | `{factory, signed_out}` — the calling session stays; an access-token row is not revoked |

The opaque device code belongs to the requesting credential and expires on
GitHub's schedule. Polling honors `interval`; `slow_down` adds five seconds.
Expired codes return 410, declined sign-in or an excluded handle returns 403,
and foreign/replayed codes return 400. The factory requests `read:user` and
`repo` (classic device flow has no read-only private-repository scope). The
returned scopes are GitHub's granted scopes. The GitHub access token stays in
process memory for the visibility check; it is never returned and never written
to `tokens.json`.

Each sign-in creates a 30-day cookie/bearer session with a separate public id.
Restart/revoke-sessions drops sessions. Access-token rows retain their expiry
and revocation. Their first signed-in profile is persisted in `tokens.json`
under the same lock as add and revoke. Until then reads use `Seam.PersonHandle`.
The original bearer resolves to that profile afterward, including across
restarts. `/v1/me` lists this caller's machines and the saved `takes_unassigned` map.

## 5. Events and SSE

`record` is the source of truth for "what happened": `{id, at, kind, job, handle, cost, detail}` appended to `events.jsonl`. HTTP job actions use the caller's handle; worker posts use the registered worker's handle. Approval ids are `evt_<8 hex>`; the publisher writes `Factory-Approval: evt_… @handle` as the PR trailer. `/v1/events` streams a `changed` tick whenever the store or the record changes (fs watch on the factory root, debounced 100 ms); clients re-`GET` what changed. No state rides on SSE.

## 6. Launch logic (`codeaf`, ordinary start)

```
attach.json present?  → client Seam → that url            (team, or your other machine)
serve.json live?      → client Seam → that url            (another codeaf on this machine serves)
else                  → start serve in-process on 127.0.0.1:0, write serve.json {url, pid, token}
                        → client Seam → loopback
worker loop           → attach to the same url, pull, run, report   (unless --no-worker)
```

`codeaf factory [--listen 127.0.0.1:7420] [--config ~/.codeaf/factory/config.yaml]` runs the server alone (a team box). `codeaf attach <url> [--name]` / `codeaf detach` write `attach.json`. `codeaf factory token|revoke|steps` are the ops verbs.

`run.lock` and the file mailbox are retired: every process's verbs go through `lease.Doors`, and execution happens wherever a worker pulls. The worker loop dials through the same order each time, so a worker whose serving window closed follows the factory to whoever serves next. Its credentials are cached per factory url and slot in `~/.codeaf/v3/factory/worker.json`; a 401 re-attaches. On loopback a worker takes every unassigned job (`takes_unassigned: {"*": true}`); attached elsewhere, only repos it has a checkout of. A saved `PUT /v1/me/takes_unassigned` for that handle replaces this consent on every later pull. A process that serves in-process removes `serve.json` and releases `serve.lock` when its surface or session host ends.

**Local job doors on an attached floor.** `api.ComposeJobs` leaves TUI-only
job-id doors nil outside loopback unless `Seam.Job(id)` finds a live lease on
this machine. Workers keep a factory-URL-bound lease receipt beside each job
mirror, renew it on heartbeat, and remove it when they let go. `Load` discovers
these mirrors off the UI loop; job selection reads cached capabilities, and
each captured door checks its receipt again before acting on the mirror.
Matching ids on this machine's own floor grant nothing. Repository-local
doors remain local, and wired actions still reach the attached factory.

## 7. The dashboard

The dashboard lives in `Agent-Field/codeaf-desk` (the codeaf app), not in this binary. The factory serves no page: `GET /` answers one plain line pointing at the TUI and the app, and `/v1` (with SSE, the access token and the session cookie) is its only face. The app's build, tests and browser smokes belong to that repository.

## 8. Tests that gate every lane

- **Contract test** (`client_test.go`): `FixtureSeam` vs `client → serve → FixtureSeam`: identical `Snapshot` after every verb. Every exported `Seam` field is either wired or named in `api.TUIOnly` (a structural test fails on a new field that is neither).
- **The existing page tests** (`internal/tui3/factory*_test.go`, ~40) run against the client Seam over an in-process server.
- **E2E on a real model**: `TestTUIE2E/FactoryRunsAnIssue` on `santoshkumarradha/factory-demo` (private) with `CODEAF_E2E_MODEL=deepseek/deepseek-v4.1-flash`, plus an API-level test (`serve/publish_test.go`): `POST /v1/jobs/{id}/approve` on a fixture job → the post stage's PR body carries the trailer.
- `make pr-ready` before any landing; `make build`; manual pages updated in the same change (the manual law).

## 9. Not in this wave

GitHub App, overlap warnings, routed questions, relay/compute, hosted. The API shape leaves room (`handle` on every event and lease; `repo` on every job) and nothing else.
