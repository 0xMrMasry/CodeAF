# `.codeaf/factory.yaml` — the pipeline file

One file per repo, committed, read from `main` (a PR can never change the rules for its own run).
Every key is optional. **A repo with no file gets the defaults and works.**
A personal `~/.codeaf/factory.yaml` may use the same keys and may only be stricter.

```yaml
# ── what starts on its own ────────────────────────────────────────────────
start:
  labels: [agent-ready]          # default: [] — nothing auto-starts until you name a label
  max_size: M                    # S · M · L — default M
  max_cost: 2                    # USD per job — default 2; over → the job asks you first
  never: [auth/**, migrations/**]              # paths; a job touching these always asks first
  ask_first:                                   # sentences; the model judges, the job asks first
    - anything that changes how secrets are read or stored

# ── how size is decided (the thresholds, exposed; delete to keep defaults) ─
size:
  pr_lines: [50, 400]            # ≤50 S · ≤400 M · more L — a PR is measured, never guessed
  issue: model                   # model (codeaf's rubric) · label (size/S · size/M · size/L)

# ── which steps a job runs, by kind ───────────────────────────────────────
pipeline:
  issue: [plan, approve, write, test, review, proof]      # default
  pr:    [read, review, proof]                            # default
  ci:    [read, write, test, proof]                       # a red CI run — default

# ── steps — a step is a prompt ────────────────────────────────────────────
steps:
  test: run the test suite this repo uses; fix what fails; stop when it is green
  security: if the change touches api/ or auth/, review it for injection, authz gaps and secret handling and list findings as file:line; otherwise say so and stop
  proof:
    prompt: show each claim in its own medium; a screenshot for any UI change; a benchmark for anything under hot/
    until: every claim has evidence
    max: 3
  migrate-check: if the change adds a migration, run it against a fresh database and show the schema diff

fixed: [approve, proof]          # steps the job's manager may never drop (a person can edit this file)

# ── sentences every job obeys ─────────────────────────────────────────────
rules:
  - tests pass before anything posts
  - no pull request without a green check
  - a stranger's PR never runs write
```

---

## Factory display name belongs to the host config

`name` belongs in `~/.codeaf/factory/config.yaml`, separate from this repository's
pipeline file. Use `codeaf factory name "Our team"` to save it, or
`codeaf factory name` to print it; `--config path` selects another host config.
The optional value is trimmed, at most 40 Unicode characters, and accepts no
control characters. `codeaf factory name ""` clears it. An unset name uses the
first watched repository's owner in the saved, sorted selection, or the host's
short hostname without repositories. Restart the service to apply a change.
All `/v1` responses carrying `factory` include `factory.name`; Contract stays 1.

## A step

A step is a prompt. codeaf starts a conversation with the model inside the job's worktree, gives it the prompt, and adds the context it always adds: the issue or PR, the repository, `rules`, every earlier step's result, CODEOWNERS. The model has read, edit and run tools, so "run the tests" is a sentence, not a step kind.

```yaml
steps:
  <name>: <prompt>               # the short form

  <name>:
    prompt: <prompt>
    until: <sentence>            # the loop: keep going until the model judges this true (default: the step says it is done)
    max: 2                       # rounds before the job asks you
```

That is the whole shape. There is no condition language: write the condition in the prompt. There are no step kinds: `approve` is the one reserved name, a person, with no prompt.

### Built-in steps

codeaf ships these as prompts. A pipeline names them; a repo overrides one by writing the same name under `steps:`. `codeaf factory steps` prints every built-in prompt to copy from.

| name | the prompt, in short | until |
|---|---|---|
| `plan` | read the issue and the code it names; write a plan and the list of files it will touch | the plan names its files |
| `approve` | — a person — | you |
| `write` | carry out the plan in this worktree | the plan is done |
| `test` | find and run the repo's tests; fix what fails | green |
| `review` | review the diff as a careful colleague; fix what you find | clean |
| `security` | review for injection, authz, secrets handling | no findings |
| `proof` | show each claim in its own medium | every claim has evidence |
| `read` | (PRs) read the change at its head and the thread; say what it does and what it risks | summarised |

### What codeaf decides, what the repo decides

| codeaf | the repo (this file) |
|---|---|
| the context every step gets · the worktree · the branch · the tools · the round cap | the prompt of any step, and new steps |
| the loop mechanic | `until` and `max` |
| the size rubric and thresholds, by default | the thresholds or `label` when the default is wrong |
| which `rules` sentences it enforces mechanically | the sentences; unrecognised ones are given to the model as text |
| that `approve` and `proof` exist | whether they are `fixed`, and where they sit |
| publishing (comment, label, PR) after the final approve | what `rules` allow it to post |

### Where the numbers live today

Until this file lands, the same values are constants in code and are the defaults above: PR size by changed lines in `internal/factory/github/source.go`; issue size, risk and priority from the triage prompt in `internal/factory/triage`; the default step list and prompts in `internal/factory/recipe.go`; the five recognised rule sentences in `internal/factory/run/policy.go`; the cap behaviour in `internal/factory/run/money.go`. The current `.codeaf/factory.md` is converted to this YAML on first read.
