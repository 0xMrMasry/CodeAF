# Factory service — architecture as built

Contract 2 on `app/v2`, 2026-10-12. Job behaviour follows codeaf-desk's
S0/S1/S2/X3 at canvas commit `d39636d`. Spec boards beat screens; newer screens
beat older screens; the glossary wins. The binary owns factory state, AI calls
and numbered rules. The app owns navigation, saved views, Inbox Later/Done,
notifications and Mac integration.

## 1. Ownership and execution

One binary runs the factory service and runner processes. The service owns
jobs, approvals, leases and append-only Activity. A runner executes the
assignee's jobs with that person's checkout, keys and spending limits and
reports results over `/v1/workers`. The app calls `/v1`; the service serves no
web dashboard. `/` names the API and the app.

`store` keeps versioned job documents, written under per-item locks with
atomic rename. `lease` keeps one live runner lease per job and rejects late
writes from stopped or replaced leases. `record` keeps append-only decisions
and consent receipts in `events.jsonl`. `live` keeps bounded SSE replay.
`serve` holds consent under a cross-process lock, including five-second Undo.
`run` executes pipeline steps and publishing. `github` polls source facts and
mirrors assignment decisions. Secrets never enter approval documents.

## 2. Jobs and transitions

The public job states are exactly `needs_you`, `running`, `queued`, `backlog`,
`done`. Internal historical engine states round-trip through
`desk.engine_state`; they are not additional public states.

| Trigger | Result |
| --- | --- |
| issue read / New job | Backlog |
| Run / Start now | Queued; Start now takes precedence over the ordinary queue |
| free runner | Running |
| plan / proof / start approval, question, budget, out of rounds, failure | Needs you |
| approval after Undo, answer, raise budget | run resumes or queues |
| published PR waiting on CI or review | Running, `reason: github` |
| changes requested / merge conflict | Needs you, `reason: changes_requested` / `conflict` |
| PR merged / closed without merge | Done, `done_how: merged` / `closed` |
| no-diff answer published as a comment | Done, `done_how: answered` |
| Stop | Backlog; branch kept; five-second Undo restores the saved run |
| Drop | Done, `done_how: dropped`; final; GitHub issue stays open |
| Unassign | Running work stops first; Backlog with no assignee |
| Run again | failed work or a closed, unmerged PR queues again; merged and dropped work refuses |

Dismiss is a per-person `dismissed_by` flag, never a state. GitHub activity
clears it. The default queue hides the caller's flagged rows;
`q=is:dismissed` retrieves them. Direct job reads retain the caller's
`dismissed` flag. No state transition or runner stop accompanies Dismiss.

`done_at` supports the app's one-day Done retention in the Running view.
Jobs and Activity are kept forever. A dropped branch has `branch_delete_at`
seven days after Drop. The service deletes its GitHub factory ref and the
local factory worktree/branch where present, then writes `branch_deleted`.
Failed cleanup retries; `branch_deleted_at` is stamped only after success.
The person's checkout is never removed or reset.

## 3. Fields and derivations

`id` is a human reference such as `api#1498`. `document_id` preserves numeric
binary identity; job routes accept either. `ref` is retained as an additive
reference alias. Encode `#` in route URLs. Runner protocol ids remain numeric.

`priority` is `{value,set_by?}`: urgent/high/medium/low. A person's override
wins. Otherwise labels supply priority:urgent/P0, priority:high/P1,
priority:medium/P2, priority:low/P3; bug defaults to medium only without an
explicit priority label. Unset sorts last. Runner pulls order by Start now,
priority, then oldest creation time. AI priority is not exposed as job priority.

`size` comes only from size:XS/S/M/L/XL. `budget` comes from pipeline
`default_budget`, default $3, not a task cap. A personal pipeline can tighten
it. Raise accepts a higher $5/$10/$20/$30 budget. A reached budget is a question.

`reason` is a finite Needs you / Running explanation. `waiting_for: @login`
is derived for non-assignees. `overlap` is derived from two Running jobs in
the same repository changing the same file, not their planned file lists.
Needs-you groups use `approve_start`, `approve_plan`, `approve_proof`,
`question`, `budget`, `out_of_rounds`, `publish_failed`, `runner_offline`,
`failed`, `changes_requested` and `conflict`. Failed work and PR attention
are grouped even when the underlying engine state is not a historical wait.
No app UI state is stored on a job; hand-offs retain an originating agent ref.

## 4. HTTP doors

All reads and errors carry factory identity. `/v1/about` and `/v1/me` report
`contract: 2`. Existing authentication, visibility, idempotency and runner
routes remain. An in-process app host supplies `ServedBy: "app"` and its
per-launch `AccessToken`. This selects `access: "token"` and requires that
bearer on every person route, including `/v1/about`; socket loopback and
browser sessions cannot grant app ownership. Runner credentials continue to
authorize runner protocol routes. A hidden repository and a missing job both answer 404.

| Job verb | Body and effect |
| --- | --- |
| `run`, `start_now` | optional expect; queue Backlog work; start approval cannot be bypassed |
| `stop`, `undo` | optional expect; end a run or withdraw this caller's pending decision |
| `dismiss`, `drop` | optional expect; Drop also accepts words for Activity |
| `assign_to_me`, `unassign` | optional expect; authenticated ownership decision |
| `change_assignee` | assignee plus optional expect |
| `nudge` | optional words and expect; addressed to the assignee in Activity |
| `priority` | priority plus optional expect; records set_by |
| `raise_budget` | budget plus optional expect; assignee only |
| `run_again` | optional expect; closed without merge only |
| `approve` | commit, edited?, words?, expect?; creates a delayed decision |
| `answer` | yes / words / choice, optional expect; first accepted plain answer wins |

`POST /v1/jobs` accepts repo, title, text, source, from_ref, assignee and start.
A `hand-off` source requires `{kind:"agent",id,title}` and stores it under
`ask.from_ref`. Start is now (queue precedence), queued or backlog; the first
two apply the pipeline budget. An explicitly empty team assignee can only
create Backlog. Invalid metadata is refused before creation. Idempotency
prevents repeat sends from creating another job. Legacy autostart remains.
Assign to me is only for unassigned jobs. Change assignee and Unassign belong
to the current assignee, and GitHub writes precede local ownership changes.

The existing pause/resume, take/release, prioritize, steer, reply, shape,
request_changes, recheck and retry_publish doors remain for binary clients.
`expect` can carry state, step and document version. Stale reads refuse with
409 before invoking a door; malformed inputs use 422, ownership failures 403.

`GET /v1/activity` reads Activity and exports CSV with `format=csv`; `since`
limits by timestamp. Old took/released records are presented as
assigned/unassigned. The `/v1/audit` alias preserves historical record names.
Activity cannot be edited or deleted. Existing numeric event `job` ids remain.
`/v1/events` durably replays typed `job.state {job,state,reason}`, `runner`
and `activity`. State events include creation, projected state changes and
reason changes without a state change, including PR review/conflict updates.
An empty reason clears the old explanation. Unrelated writes emit no state
notification; visibility filters apply equally to replay and live delivery.

## 5. Approvals and collisions

Approval kinds are plan, proof and start. Start retains `matched`, the rule
and fact that asked first. Only an assigned job's assignee approves. Proof
pins the exact head commit and checks unproven claims before scheduling.

Approve stores `state: approving`, `decided_by`, `at`, `undo_until`, commit and
edited consent. Until decided_at + five seconds, it invokes no answer or
publish door and writes no approved receipt. The assignee can Undo. A timer
settles the decision; reads and restart recovery also settle due decisions
under the same cross-process consent lock. Settlement rechecks the head and
ownership. Refusal leaves `state: waiting` and an error; success records
approved and resumes or publishes. Approval receipts provide PR trailers.
A publish retry keeps the existing approval.

The assignee's leased runner owns publishing in both team and solo factories.
It resolves a local GitHub credential, checks its login against the assignee,
and uses that one credential to push the approved SHA and open or update the
PR body, or post the proof comment. An app GitHub handoff takes precedence over
shell and profile credentials, including when the app removes its key. The
runner scopes its source to the leased job's repository without requiring a
local watch list. The factory server polls checks, reviews and merge/close
state and records the reported receipt; its GitHub connection never publishes
the job. Publish errors keep their stable codes and `publish_failed` gate.
An expired publish lease stays Needs you with `runner_offline` and
“Runner went offline mid-step”; `try_once_more` preserves the recovered trailer
and resumes on the same machine. HTTP `Idempotency-Key` replays are unchanged.

A plain question is open to everyone with repository access. Its answer is
serialized under the same consent lock. The second writer receives exactly
“@jm answered this 10s ago. Your answer wasn't sent.” with the actual person
and elapsed seconds. Their answer is not sent to the runner.

## 6. Contract compatibility and evidence

Contract 2 is a deliberate breaking semantic change: job id becomes text,
Drop becomes Done, open published PRs remain Running, and approval returns a
pending decision instead of an immediate receipt. Numeric document and runner
identities, old verb aliases, `gate`, and failure `attempts`/`machine` remain
for binary compatibility. Canonical failure fields are `rounds` and `runner`;
canonical job approval is `approval`.

Model, wire and real HTTP transition tests live in `jobs_test.go`,
`api/jobs_contract_test.go`, and `serve/jobs_v2_test.go`. They advance clocks
without sleeps, exercise consent across server restart, and prove personal
Dismiss, final Drop, Stop Undo, assignment, budget, priority, question
collision and closed-PR rerun. GitHub-write tests stub only transport;
branch cleanup tests use a real temporary git checkout.

Gates: `make test-quick` and
`go test ./internal/factory/... ./cmd/...`.

## 7. Runners, access and HTTPS

The v2 runner roster, people directory, solo/team modes and operator access
links are documented in `docs/factory-v2-people-runners.md`. Solo responses
omit team assignment; team operators manage access without inheriting job
approval rights. Runner heartbeat expiry places held work in Needs you with
`reason: runner_offline` instead of silently reassigning it. Activity includes
runner, access-link and repository decisions as well as job decisions, and
its list and CSV export share the same filters.

HTTPS configuration and trusted reverse proxies are documented in
`docs/design/factory/HTTPS.md`. The host supports automatic certificates,
certificate files and HTTP behind a proxy. Public factory identity retains
the HTTPS URL; local discovery uses a separate loopback listener. Only
explicitly trusted proxy addresses supply forwarded client addresses and
schemes. HTTPS requests set Secure cookies and HSTS, and SSE disables proxy
buffering. `/v1/about` exposes the effective scheme and factory identity.

## App factory lifecycle and the shared runner

`codeaf app serve` mounts its solo `serve.Handler` at `/v1/` on the app's
loopback listener. Person routes require the launch bearer, including `/v1/about`;
worker pull, lease and presence routes authenticate factory-issued credentials
inside the same Origin/Host boundary. Before solo creation they return
`404 no_factory`. The app reuses a live solo service discovered from the TUI
through an authenticated proxy instead of starting another floor. Runner routes
retain their factory-issued credentials through that proxy. The mount follows
service election when the hosting window closes, allowing another app to take
over the same store.
The app's discovery file contains an address, PID and app marker, without its
launch bearer. Other local windows obtain that bearer from the elected host's
private runtime socket; terminal-only discovery retains its existing format.

The app and TUI share `~/.codeaf` (or `CODEAF_HOME`), `serve.lock`, and
`worker.lock`. One elected worker process serves all attached factories, each
with an isolated hashed mirror below `workers/<pid>/`. A private runtime socket under `/tmp/codeaf-runners-<uid>/`
accepts factory sessions from another app process; those sessions remain in
memory. Worker registration credentials keep their separate private takeover
file. `attach.json` contains a first-team URL for older terminal clients and a
`factories` array of stable ids, addresses and modes. App session tokens and
provider keys never enter that file.

The app's power route stops its connected processes with a sleep receipt and
reports asleep/awake using the elected runner's existing saved credential.
It does not register a second runner. Factory event streams merge person-scoped
Inbox items and relay typed runner, job and Activity events to the app stream.
Hand-off cards choose a factory, create the job with its source reference, and
only then stop the origin with "Handed to the factory". Task ownership stays
with the task resource through the typed `FactoryTaskSource` interface.
