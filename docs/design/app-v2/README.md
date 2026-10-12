# codeaf v2 app — binary side

The codeaf v2 desktop app (Tauri, `codeaf-desk`) is a pure UI. This binary owns
all state, every AI call and every numbered rule, and the app reaches it over one
local API.

- [API.md](API.md) — the design: transport and key handoff (§1), every resource
  and verb with its status (§2), the one typed event stream (§3), the port plan
  from the desktop bridge with lanes A1–A16 (§4), the engine rule changes the
  design requires (§5), and open questions for the owner (§6).

Status count of `/app/v1` routes in API.md §2: 26 PORT (from
`internal/desktopbridge` on `work/8f3c2a9d`), 3 PORT+NEW, 37 NEW. The factory's
`/v1` (about 30 routes) EXISTS and is reused as is, plus deltas F-1…F-7 for the
factory lanes.

Design source of truth: codeaf-desk `main`, `src/design/source/text/` (S0–S2,
X1–X5, U1–U7). The board wins over this folder.
