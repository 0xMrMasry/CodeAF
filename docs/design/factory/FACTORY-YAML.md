# `.codeaf/factory.yaml` — the pipeline file

One file per repo, committed, read from `main` (a PR can never change the rules for its own run).
Every key is optional. **A repo with no file gets the defaults below and works.**
A personal `~/.codeaf/factory.yaml` may use the same keys and may only be stricter.

```yaml
# ── what starts on its own ────────────────────────────────────────────────
start:
  labels: [agent-ready]            # default: [] — nothing auto-starts until you name a label
  max_size: M                      # S · M · L — default M
  max_cost: 2                      # USD per job — default 2; over → the job asks you first
  never_paths: []                  # globs; a job touching these always asks first (checked at triage and after plan)
  ask_first: []                    # sentences the model judges, e.g. "anything that changes how secrets are read"

# ── how size is decided ───────────────────────────────────────────────────
size:
  pr: lines                        # lines (default) · model · label
  pr_lines: [50, 400]              # with `lines`: ≤50 S · ≤400 M · more L
  issue: model                     # model (default: codeaf's rubric) · label
  labels: [size/S, size/M, size/L] # with `label`: read these labels, else model

# ── which steps a job runs, by kind ───────────────────────────────────────
pipeline:
  issue: [plan, approve, write, test, review, proof]      # default
  pr:    [read, review, proof]                            # default
  ci:    [read, write, test, proof]                       # a red CI run — default

fixed: [approve, proof]            # steps the job's manager may never drop (a person can edit this file)

# ── step definitions — override a built-in, or add your own ──────────────
steps:
  test:
    kind: check
    run: make test
  security:
    ask: review the diff for injection, authz gaps and secret handling; list findings as file:line
    when: paths match [api/**, auth/**]
  proof:
    ask: show each claim in its own medium; a screenshot for any UI change; a benchmark for anything under hot/

# ── sentences every job obeys ─────────────────────────────────────────────
rules:
  - tests pass before anything posts
  - no pull request without a green check
  - a stranger's PR never runs write
```

---

## A step

A step is one object. Only `kind` changes what the other keys mean.

```yaml
steps:
  <name>:                 # one lowercase word; this is what the pipeline list refers to
    kind: chat            # chat (default) · check · approve · post
    ask: …                # chat, post: the instruction the model is given
    run: …                # check: the shell command; exit 0 = pass
    until: …              # chat: keep going until this sentence holds (model judged)
    max: 2                # chat: at most this many rounds · check: fix rounds before it asks you
    effort: medium        # low · medium · high — how hard the model thinks
    when: …               # only run when this holds, e.g. `paths match [ui/**]`, `kind is pr`
    proof: []             # evidence this step must leave: test · screenshot · benchmark · transcript
```

### The four kinds

| kind | what happens | ends when | you see |
|---|---|---|---|
| **chat** | a conversation with the model inside the job's worktree, with read/edit/run tools. codeaf adds the standard context: the issue or PR, repo facts, `rules`, every earlier step's result, CODEOWNERS. The repo owns `ask`. | `until` holds, or `max` rounds | the chat (TUI), the result and cost (dashboard) |
| **check** | a shell command, no model. Output is attached to the job. | exit code | pass / fail and the output |
| **approve** | a gate: a person. Shows the previous step's result and asks to continue. | you answer: continue · send back · continue with words | Needs you |
| **post** | writes to GitHub: comment, label, open the PR. Only what `rules` allow. Always behind an `approve`. | the write succeeds | the receipt (URL) |

A failed `check` sends the job back to the nearest earlier `chat` step with the output, up to `max` fix rounds; then it asks you.

### Built-in steps

codeaf ships these; a repo lists the name and overrides only what differs.

| name | kind | default ask / run | until |
|---|---|---|---|
| `plan` | chat | read the issue and the code it names; write a plan and the list of files it will touch | the plan names its files |
| `approve` | approve | — | you |
| `write` | chat | carry out the plan in this worktree | the plan is done |
| `test` | check | the repo's test command if one is obvious (`make test`, `go test ./...`, `npm test`), else a chat step that finds and runs it | exit 0 |
| `review` | chat | review the diff as a careful colleague; fix what you find | clean |
| `security` | chat | review for injection, authz, secrets handling | no findings |
| `proof` | chat | show each claim in its own medium | proven |
| `read` | chat | (PRs) read the change at its head and the thread; summarise what it does and what it risks | summarised |

### What codeaf decides, what the repo decides

| codeaf | the repo (this file) |
|---|---|
| the standard context every chat step gets | the `ask` of any step |
| the loop (`until`, `max`), the worktree, the branch | which steps, in what order, per kind |
| the size rubric and thresholds **by default** | the thresholds, or `label`, when the default is wrong |
| what `rules` sentences it enforces mechanically | the sentences; unrecognised ones are given to the model as text |
| that `approve` and `proof` exist | whether they are `fixed` |

### Where the numbers live today

Until this file lands, the same values are constants in code and are the defaults above: PR size by changed lines in `internal/factory/github/source.go`; issue size, risk and priority from the triage prompt in `internal/factory/triage`; the default step list and asks in `internal/factory/recipe.go`; the five recognised rule sentences in `internal/factory/run/policy.go`; the cap behaviour in `internal/factory/run/money.go`. The current `.codeaf/factory.md` is converted to this YAML on first read.
