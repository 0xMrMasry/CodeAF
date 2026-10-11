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
| needs_you | derived | `{kind, since, who_can_answer, question?}`. Kinds: `approve_plan`, `approve_proof`, `question`, `spending_limit`, `out_of_attempts`, `publish_failed`. The queue's `needs_you` groups job ids by that kind |
| failure | `Item.Failure` | a step that errored: `{step, code, detail, attempts, machine, next, at}` on the job and on that step. `next` is `requeued` or `stopped`. Not a question |
| ask | `Item.Ask`, or the issue | `{text, by, at, source}` for work typed in the app or the TUI; `{issue:{number,url,title}, text}` for a GitHub issue |
| state | `Item.State` | `new`→**backlog**, `queued`, `running`, `needs you`→**needs_you**, `landed`→**needs_you** (sub-state `ready`), `shipped`→**done**, `dismissed`→**dropped**. **done** stays the published job. PR open and Merged are `pr.state` (`open`, `merged`, `closed`), not a new job state |
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
| `internal/factory/live` | bounded `live-events.json` notification record; write-time transitions and replay ids | `internal/factory` |
| `internal/factory/record` | append-only `events.jsonl`, bound approval ids (`evt_…`), export; accepted approval/take notifications | `store`, `live` |
| `internal/factory/pipeline` | `.codeaf/factory.yaml` reader, `.md` conversion, `start:` evaluation, dry run | `internal/factory` (Recipe) |

`cmd/codeaf` gains `factory.go` dispatch for `codeaf factory …` and `attach`/`detach`, and the launch logic in §6.

## 4. The API (`/v1`)

All JSON. Errors: `{error, reason?, by?, at?, now_state?, request_id}`. `reason` is `not_allowed` (403), `not_found` (404), `conflict` or `stale` (409), or `bad_input` (422). A crash is 500 and has no `reason`. `request_id` is eight hex characters. Ids are the job ids (`int`). Every response carries `factory: {address, version}`. A job read includes `version` when a store holds the document.

### Read

| method path | returns | from |
|---|---|---|
| `GET /v1/queue` | `Queue{since, jobs[] (each live hold adds held_by and CODEOWNERS adds owners[]), people{handle:{name,avatar_url}}, repos[], strip, capacity{machines_online, busy}, me, reason?}`. `since` includes `shipped`, `arrived`, `asked`, `handled`, `spent`, `spent_today`, `merged_today`, `merged_week` | `Seam.Load` plus the claims desk, then the caller's visible repositories; `spent_today` is the runner's day figure. Merge counts are pull requests this factory opened whose `merged_at` falls today or this Monday-start week |
| `GET /v1/jobs/{id}` | `Job` (full: steps, results, claims, questions, cost, pr) | `Seam.Load` filtered |
| `GET /v1/jobs/{id}/thread` | `{factory, messages: []Message}` | `Item.Thread`, replicated from the holding machine; max 200 lines, 16 KiB each |
| `GET /v1/events` | SSE: `changed` (job id or `*`), plus typed `needs_you`, `question`, `approved`, `taken`, `publishing`, `published`, `publish_failed`, `machine`; 20s comment keepalive; `Last-Event-ID` replays the last 200 typed events | store watch + durable notification record |
| `GET /v1/repos` | watched + available | `Seam.Repos` |
| `GET /v1/repos/{owner}/{repo}/pipeline` | `Pipeline` plus `text`, `personal_text`, `personal`, `origin` (`main`, `working tree`, `defaults`), `start`, `size`, and each step's `until_sentence`. `until` stays `done`, `clean`, `green` or `proven` | `pipeline` |
| `GET /v1/repos/{owner}/{repo}/dryrun` | `DryRun{would_start[], would_ask[], would_wait[]}`. `would_wait[]` is `{job, reason}` | `pipeline` + `Seam.Load` |
| `GET /v1/github` | `{login, via, polling_every}` | `Seam.GitHub` |
| `GET /v1/about` | `{name, codeaf_version, contract, needs, access, polling_every, served_by}` before any token. `needs` is `token`, `device` or `none`. `access` is `loopback`, `token` or `tailnet`. `served_by` is `tui` or `factory` | host config |
| `GET /v1/audit?since=` | `[]Event` | `record` |
| `GET /v1/me` | `{codeaf_version, contract, handle, name, avatar_url, session_id, scopes[], machines[{name, os, codeaf_version, status, job, last_heartbeat}], takes_unassigned{}, access, polling_every, served_by}` | caller session; a token with no signed-in profile, the host token and open loopback use `Seam.PersonHandle`. `machines` is that handle only. `status` is `busy`, `idle` or `offline` (no heartbeat for three 2s intervals). `takes_unassigned` is the saved map, or `{}` |
| `PUT /v1/me/takes_unassigned` | `{repo: bool}` saved for that same handle; later pulls by that handle use it | the factory store. No store answers 501 |
| `GET /v1/members` | `{members: [{handle, name, avatar_url, joined_at, last_seen, sessions}]}` | live GitHub sign-ins, plus access-token rows that have bound a profile |
| `GET /v1/people/{handle}` | `{handle,name,avatar_url,html_url,role,last_seen?,owns:[{repo,path}],assigned_now?,waiting_on?,questions?,machines[]}` | public GitHub profile, member/session history, visible jobs, default-branch CODEOWNERS and machine presence; Contract 1 |
| `GET /v1/me/sessions` | `{sessions: [{id, device, created, last_seen, current}]}` | this caller's sign-ins; `device` is the User-Agent captured at sign-in |

Version skew: `codeaf_version` is the build stamp; `contract` is `api.Contract`, an integer starting at 1 that is bumped only on a breaking wire change. `/v1` only adds fields; additive fields never bump it. An app newer than the factory shows "this factory runs codeaf X; update it to use Y" and keeps the read-only views.

People cards use `GET /users/{handle}`, so an outside issue author needs no factory sign-in. Profiles are cached for 24 hours and revalidated with ETag; queue profile failures leave a summary from member history or blank fields, keeping the floor readable. `role` is `member` for a person in member/session history or the connected host, otherwise `outside`. Unknown last-seen and zero counts are omitted. `assigned_now` counts that person's visible jobs except shipped/dismissed; `waiting_on` counts those with a needs-you reason and `questions` counts plain questions among them. No issue author is treated as assigned merely for reporting it.

Ownership reads `.github/CODEOWNERS`, then root `CODEOWNERS`, then `docs/CODEOWNERS`, stopping at the first existing file on GitHub's default branch. Reads, missing files and failures are cached until the next poll. `owns` lists declared patterns for individual handles, qualified by repository. `job.owners` is the sorted union of the last matching line's handles for planned files, source PR files and worker-reported changes. Ownerless lines clear earlier ownership. Team/email owners are not expanded into people; unsupported negation, bracket ranges and escaped patterns are skipped. Queue `people` includes every mentioned assignee, author, approver, reviewer, answerer, comment author, owner and live holder, projected onto visible jobs only. Cards filter counts and ownership before enrichment; a partial viewer sees only machines holding a visible job, so a hidden hold or idle roster cannot describe another repository.

A signed-in GitHub viewer sees only repositories `GET https://api.github.com/repos/{owner}/{repo}` accepts for their token. The answer is cached for 10 minutes. Queue jobs, repositories, the strip, queued count, daily total, audit rows, `GET /v1/repos`, pipeline, dry run and a changed tick that names a job all use that set. Daily for a partial view is the sum of visible job costs, and since-counters are cleared because they are not stored per repository. `machines_online`, `busy` and `spent_today` stay floor-wide only when the viewer can see every repository; a narrower view leaves them at zero. A viewer who can see every repository still receives the factory-wide totals. A viewer who can see none receives HTTP 200 with an empty queue and `reason: "no_visible_repos"`. Loopback without a signed-in profile, and the host access token, are not filtered. A hidden job or repository answers 404.

### Live notifications (additive Contract 1)

`changed` keeps its plain job id or `*` body and no id. Typed events carry a
monotonic `id: live_<number>` and small JSON `data`:

| event | payload |
|---|---|
| `needs_you` | `{job, kind, since}`; kinds match `job.needs_you.kind`: `approve_plan`, `approve_proof`, `question`, `spending_limit`, `out_of_attempts`, `publish_failed`; empty kind clears the need |
| `question` | `{job, kind}`; free asks use `text`, `yes_no` or `options`, approval asks use their Needs you kind |
| `approved` · `taken` | `{job, by}` from accepted audit receipts, including actions in another process |
| `publishing` | `{job, state}` for every `queued`, `pushing`, `opened`, `failed` transition |
| `published` · `publish_failed` | `{job, pr}` (`pr` is `{number,url}`, null for a comment) · `{job, reason}` (stable failure code, or detail when no code) |
| `machine` | `{name, status}` (`busy`, `idle`, `offline`); own idle machines or a machine attached to a visible job |

Store writers record each transition in `live-events.json` under a flock before
returning. The record retains 200 typed events across restarts. The filesystem
watch flushes within a 100ms window even during continuous writes; its 2s scan is
only a fallback for a missing watch. SSE reads that record on a push tick, rather
than polling jobs. Rapid transitions remain distinct, and a slow subscriber
catches up from the retained record. Reconnect with `Last-Event-ID` to replay
strictly later events. An unknown/expired cursor replays the retained window and
sends `changed` with `*` to refresh current state. A first connection starts live.
Every job event is filtered by the viewer's repository access. Machine events
tied to a job use the same filter; idle machines without a job reach their owner. Silence becomes an offline machine event at
its heartbeat deadline. `: keepalive` comments arrive every 20s, including on a
factory without a store. A keepalive has no id and is not a change.

### Verbs (`POST /v1/jobs/{id}/<verb>`, body as noted)

| verb | body | Seam |
|---|---|---|
| `run` | optional `expect` | `Launch` |
| `stop` · `pause` · `resume` | optional `expect` | `Stop` · `Hold(on)` · `Hold(off)` |
| `drop` | `{words?, expect?}` | `Dismiss`; words are the audit detail; a holder is signaled `stop` and the branch is kept |
| `answer` | `{yes?: bool, words?: string, commit?, choice?, reason?, expect?}` | `Answer`. `choice` is `continue`, `try_once_more`, `send_back` or `cancel_job`. `send_back` requires `reason`. An affirmative plan answer names `commit` |
| `steer` | `{words, expect?}` | `Steer` (reply-level; records in the thread) |
| `approve` | `{commit, edited?: bool, expect?}` → `{event: "evt_…"}` | `SignOff` (proof gate) or `Answer(yes)` (plan gate). A claim not shown refuses before any receipt. The proof receipt is written after that check and before the publish door, which reads it. A plan receipt is written only after the answer door accepts |
| `retry_publish` | optional `expect` | Reuses the same commit's proof receipt after a failed publish; the job's person only; no new approval event |
| `request_changes` | `{words, expect?}` | `SendBack` |
| `recheck` | optional `expect` | `Reverify` |
| `take` | optional `expect` | `lease.Assign(me)` |
| `release` | optional `expect` | `lease.Release(me)` — un-take; the branch is kept; audit `released` |
| `prioritize` | `{to: "top", expect?}` | `Mark` |
| `reply` | `{words, expect?}` | store a pending message; `reply {id, words}` signal on heartbeat/pull; worker acknowledgment changes it to delivered |

`expect` is `{state?, step?, version?}`. A field that no longer matches is HTTP 409 `{error, reason: "stale", by?, at?, now_state}`. The sentence is `already approved by @raj at 11:58` (also `approved_by`, `event`, `commit`), `already taken by @mei` (also `taker`, `at`, `machine`), or `job moved on while you looked`. A moved head is `{reason:"changed_since_opened", head_commit, by, at, commit, current_commit}`.

Every job carries `can` for this caller: `{approve, run_again, cancel_job, retry_publish, answer, assign_to_me, reply}`. Approve, run again, cancel job and retry publishing are the job's person. A plain question is anyone who can see the repository. Reply is anyone who can see the job. `assign_to_me` is true only when the job has no person. A false verb answers HTTP 403 `{reason:"not_job_person", job_person}`. A wrong state stays 409. Take of someone else's job stays 409. Release of someone else's take stays `not_allowed`.

**Needs you (additive, Contract 1).** `job.needs_you` is `{kind, since, who_can_answer, question?}`. The six kinds are fixed: `approve_plan`, `approve_proof`, `question`, `spending_limit`, `out_of_attempts`, `publish_failed`. Adding one is a code change. `GET /v1/queue` groups waiting jobs as `needs_you: [{kind, jobs}]` in that order, and leaves out a kind with nothing in it. `questions[]` is unchanged.

`question` is `{id, kind, text, options?, from_step?, asks?, who_can_answer, answered?}`. `kind` is `text`, `yes_no` or `options`. A free question's `who_can_answer` is `anyone_with_repo`. The other five kinds are `job_person`: when the job has a person, anyone else gets HTTP 403 `only @raj can answer this`. A job with no person yet can still be answered.

`spending_limit` is `{limit, spent, next_limit, currency}`. `currency` is `USD`. Answering `continue` (or yes) raises this one job's cap to `next_limit`, which is the cap doubled in codeaf. The app does not send the number. The raise is an `answered` event, not a setting. Stop does not raise it.

`out_of_attempts` is a question, options `try_once_more`, `send_back` and `cancel_job`. The run asks `try once more, send it back, or cancel the job?`. `send_back` needs a reason and returns to the previous step. `cancel_job` stops the job. A loop step keeps `rounds: [{round, summary, at}]`, bounded by the step's max (or the round a try-once-more reached) and never more than 32.

A step that errored is `job.failure` and the same object on that step (`tool_error`, `provider_error`, `crash`, or `machine_lost` when a lease expires), with `attempts` and `machine`. `next` `requeued` leaves the step runnable. `stopped` ends the run. The record kind is `step_failed`. It is not a question, and the job does not ask to skip it.

A publish that fails stays landed with `publishing.state` `failed`, `publish_error` set, and `needs_you.kind` `publish_failed`. `POST /v1/jobs/{id}/retry_publish` uses the approval already recorded and does not write a second one.

A job typed in the app or the TUI carries `ask: {text, by, at, source}` with `source` `app` or `tui`. A job from a GitHub issue carries `ask: {issue: {number, url, title}, text}`. The pull request body starts with that ask.

Also: `POST /v1/jobs {repo, title, text, autostart?}` → `Seam.New`; `PUT /v1/repos {watched[]}` → `SetRepos`; `POST /v1/github/connect {token}` → `ConnectGitHub`.

### Publishing (V1, additive Contract 1)

`job.head_commit` is the full SHA reported by the holding worker. The proof gate
commits outstanding work before opening, so a person approves an existing object.
`approve {commit}` and affirmative plan `answer {commit,yes:true}` must name the
head the person saw. A missing commit for a reported head is 422; a mismatch is
409 `{reason:"changed_since_opened",head_commit}`. Legacy jobs without a reported
head keep the earlier approval body. The record's approval event and PR trailer
carry the commit. The client seam uses its last loaded head, never a fresh read
inside SignOff. A publishing worker checks both HEAD and cleanliness before pushing.

Optional `job.publishing` is `{state,code?,detail?,pr?:{number,url},attempt,at}`.
Transitions are approval → queued → pushing → opened or failed; retry moves failed
→ queued and increments attempt. The older `desk.publishing` flag is retained.
Worker reports carry head and publishing snapshots through Diff and ApplyReport.
A failure stays landed (`needs_you`, `gate:"publish_failed"`, red in the app), with
its same-commit approval intact. `retry_publish` queues its publish signal without
recording new consent. A changed commit requires approval again. Queued publishing
on an offline holding machine exposes `machine_offline` and waits for that machine.
`GET /v1/publish/codes` returns `{factory,codes:[{code,next}]}` from the fixed table
in `internal/factory/publish/codes.go`. Connectivity and rate-limit failures are
retried by a person; machine-offline waiting resumes through the existing lease path.

Every POST accepts `Idempotency-Key`: the same credential, route and key within
24 hours replay the first status, headers and body, including errors. Store-backed
responses are locked across processes and persisted before being returned; handlers
without a store use memory. Keys are hashed with the credential and route, so tokens
never enter the file name. Approval and retry additionally share a consent lock:
two simultaneous approvals cannot mint two receipts even with different keys.

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

**Pull request follow-up (additive Contract 1).** Each poll re-reads pull
requests the factory opened, including after they leave GitHub's open list.
`job.pr` gains `state` (`open`, `merged`, `closed`), `merged_by`, `merged_at`,
`review_decision` (`approved`, `changes_requested`, `review_required`),
`reviews[]` (`by`, `state`, `at`), `required_reviews`, and
`checks` (`passed`, `failed`, `pending`). `number`, `url`, `draft` and `ci`
stay. Job state `done` still means the factory published. PR open is
`pr.state` `open`. Merged is `pr.state` `merged` on that done job. The first
time a poll sees `merged` or `closed` it appends `pr_merged` or `pr_closed`
(`detail` `pr · <url>`). `since.merged_today` and `since.merged_week` count
those merges. A comment-only publish keeps `pr` null and is not followed.

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
| approve (proof) | the record's approval read as the trailer (none: refused); `PublishItem` → `queued` (held: stays landed until done, which re-queues) | `publish {words: trailer}` (on the pull) | `Runner.Publish`: check the approved head and a clean worktree, push that commit on the branch (never force), open the PR (a `pr` job: one comment); reports `pr`, done `done` → shipped beside the recorded approval, record `posted` |
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
and revocation. A presented bearer or `/access` token that is refused answers 401 `{reason: expired|revoked|unknown, invited_by?, created_at?}`. A request with no secret keeps the sign-in sentence. Revoke retires the row: the secret becomes a hash, list hides it, and a later use is `revoked`. Their first signed-in profile is persisted in `tokens.json`
under the same lock as add and revoke. Until then reads use `Seam.PersonHandle`.
The original bearer resolves to that profile afterward, including across
restarts. `/v1/me` lists this caller's machines and the saved `takes_unassigned` map.

## 5. Events and SSE

`record` is the source of truth for "what happened": `{id, at, kind, job, handle, cost, detail, commit?}` appended to `events.jsonl`. Kinds include `step_failed` when a step errors or a hold expires, and `pr_merged` or `pr_closed` when a poll first sees a factory-opened pull request merge or close. HTTP job actions use the caller's handle; worker posts use the registered worker's handle. Approval ids are `evt_<8 hex>`; the publisher writes `Factory-Approval: evt_… @handle <commit>` as the PR trailer. `/v1/events` streams a `changed` tick whenever the store or the record changes (fs watch on the factory root, debounced 100 ms); clients re-`GET` what changed. No state rides on SSE.

## 6. Launch logic (`codeaf`, ordinary start)

```
attach.json present?  → client Seam → that url            (team, or your other machine)
serve.json live?      → client Seam → that url            (another codeaf on this machine serves)
else                  → start serve in-process on 127.0.0.1:0, write serve.json {url, pid, token}
                        → client Seam → loopback
worker loop           → attach to the same url, pull, run, report   (unless --no-worker)
```

`codeaf factory [--listen 127.0.0.1:7420] [--config ~/.codeaf/factory/config.yaml]` runs the server alone (a team box). `codeaf attach <url> [--name]` / `codeaf detach` write `attach.json`. `codeaf factory name|token|revoke|steps` are the ops verbs.

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

## Factory display name (Contract 1)

The host config `~/.codeaf/factory/config.yaml` accepts optional `name`; omission
stays omission when other host settings are saved. `codeaf factory name` prints
the effective name, and `codeaf factory name "Our team"` saves it. Both accept
`--config path` before the text. Names are trimmed, at most 40 Unicode characters,
and reject control characters. An empty quoted string removes the setting.

At service start, an unset name uses the owner of the first watched repository
in the saved, sorted selection, or the host's short hostname when none is watched.
Changing the config or watched selection requires a service restart to update
that display name. All callers see the same name. Every response carrying
`factory` adds `name` beside `address` and `version`, including `/v1/about`,
`/v1/me`, `/v1/queue` and errors. `/v1/about` retains its top-level `name` with the
same value. This is additive: Contract remains 1, and names do not identify
credentials or change factory addresses.
