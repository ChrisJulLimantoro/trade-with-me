# Spec 09 — Interface: UI, MCP & Skills · Milestone M3

> M1 proved the signal. M2 sharpened it and added the learning loop. M3 makes
> the working system **observable and drivable** — without changing what it
> does. This spec merges three sibling surfaces over the same Postgres, plus the
> contract layer that binds them:
>
> 1. A **read-only FastAPI** HTTP API
> 2. A minimal **Next.js dashboard**
> 3. A **read-only MCP server** (stdio + SSE) so any coding agent can query the system
> 4. The **`.claude/skills/<name>/SKILL.md`** surface + the **CLI-parity contract**
>    that ties every skill to a `ats <verb>` twin
>
> Nothing here can mutate data. The CLI remains canonical; M3 is observation and
> portable invocation. This is also where the skill wrappers deferred through
> M1–M2 finally get authored — *after* the CLI surface they wrap is stable.

---

## Goal

1. A read-only FastAPI service over the existing Postgres
2. A minimal Next.js 15 dashboard consuming it
3. An SSE stream for live top-10 / new-signal updates
4. A read-only MCP server (stdio + SSE)
5. The full `SKILL.md` inventory + the test that proves every skill produces
   byte-identical artifacts to its CLI twin

---

## Milestone & scope

**Milestone:** M3 — Observe.

**In:**
- FastAPI with auto-generated OpenAPI docs; read-only endpoints
- SSE channel (Redis pub/sub at Tier 3; no-op fallback + polling at Tier 1)
- Next.js 15 dashboard (App Router, Tailwind, shadcn), localhost-only, no auth
- Read-only MCP server (stdio + SSE), tools mirroring the HTTP routes
- `.claude/skills/<name>/SKILL.md` authoring conventions + the 10-skill inventory
- The `prompts.py` single-source rule: `SKILL.md` is the source of truth for agent
  system prompts (migrating from M2's interim `prompts/` directory)
- pytest patterns: skill ↔ CLI parity, prompt parity, MCP read-only
- Portability checklist for non–Claude-Code runtimes

**Out:**
- No POST/PUT/DELETE endpoints; no write MCP tools — **ever**
- No multi-user auth (single-operator, localhost)
- No execution actions
- No new tables, no new daemons, no new LLM calls beyond what M2 specifies

---

## Dependencies on prior specs

All of M1 and M2. The UI is a passive viewer; broken upstream → empty UI. The
skill inventory points at CLI commands delivered in specs 03–08.

---

## New deps to add

```bash
uv add fastapi 'uvicorn[standard]'
uv add mcp                              # stdio + SSE MCP server
```

Frontend (separate from uv):

```bash
mkdir ui && cd ui
npx create-next-app@latest . --typescript --tailwind --app --eslint --no-src-dir
npx shadcn@latest init
npx shadcn@latest add table card badge tabs
```

---

## API endpoints (read-only)

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{status, db, redis, ingestion_ok, last_cycle_ts}` |
| GET | `/api/regimes/current` | latest `regimes` row |
| GET | `/api/top-picks/latest` | top-10 for the most recent cycle |
| GET | `/api/top-picks/history?since=24h` | top picks over a window |
| GET | `/api/signals?status=active&limit=50` | paginated |
| GET | `/api/signals/{id}` | full signal with `agent_runs` + retrieved learnings |
| GET | `/api/narratives/active` | currently active narratives |
| GET | `/api/learnings/recent?limit=20` | most recent learnings |
| GET | `/api/reflections?since=30d` | reflection rows |
| GET | `/api/reflections/latest` | latest reflection (with markdown) |
| GET | `/api/journal?status=open` | paper trades |
| GET | `/api/journal/{id}` | paper trade detail |
| GET | `/api/replay/latest` | latest replay report summary + gate verdict |
| GET | `/api/stream` | **SSE** — `cycle_close`, `new_signal`, `signal_closed` |

All responses Pydantic-typed; OpenAPI auto-generated at `/docs` and `/redoc`.

---

## MCP server (read-only)

Sibling of the FastAPI app — same Postgres reads, different transport. Runs
inside `ats serve`; stdio by default, SSE under `/mcp/sse` when the API is up.

| MCP tool | Maps to | Returns |
|---|---|---|
| `get_regime_current` | `GET /api/regimes/current` | latest regime cell |
| `get_top_picks_latest` | `GET /api/top-picks/latest` | top-10 most recent cycle |
| `get_top_picks_history(since)` | `GET /api/top-picks/history` | top picks over a window |
| `list_signals(status, limit)` | `GET /api/signals` | paginated signals |
| `get_signal(id)` | `GET /api/signals/{id}` | full signal + `agent_runs` + learnings |
| `get_narratives_active` | `GET /api/narratives/active` | active narratives |
| `get_learnings_recent(k)` | `GET /api/learnings/recent` | recent learnings |
| `retrieve_relevant_learnings(setup_snapshot)` | (internal fn reuse) | top-3 similar learnings for an arbitrary snapshot |
| `get_journal(status)` | `GET /api/journal` | paper trades |
| `get_reflection_latest` | `GET /api/reflections/latest` | latest reflection markdown |

**Locked out of MCP forever** (anti-goal in `architecture.md`): no `create_*`,
`close_*`, `place_*`, `kick_*`, `run_*` — no write side-effects of any kind. If a
skill needs a write, it goes through the CLI, never through MCP.

**Why MCP is here:** it is the read surface for any coding agent (Claude Code,
OpenCode, Cursor) querying the system as it works — "look at my top-picks and
tell me which fits my preferred regime" without copy-pasting JSON.

---

## Skill surface

The skill wrappers deferred through M1–M2 are authored here. Each is a
`.claude/skills/<name>/SKILL.md` with a CLI twin already built in an earlier spec.

### Layout

```
.claude/skills/<name>/
├── SKILL.md              # frontmatter + body (required)
└── scripts/              # skill-private helpers (optional)
```

### Frontmatter (authoritative)

```yaml
---
name: agent-structure
description: Structure agent — pivots, S&R, breakouts/fakeouts on the active top pick
user_invocable: true
allowed-tools: Bash(uv run ats *) Read Write
entry-point: ats agent structure run --symbol {SYMBOL} --cycle-ts {CYCLE_TS}
---
```

| Field | Required | Notes |
|---|---|---|
| `name` | yes | kebab-case; matches the directory |
| `description` | yes | one line, present-tense, ≤ 120 chars |
| `user_invocable` | yes | `true` for human-invoked skills, `false` for orchestrated-only |
| `allowed-tools` | yes | minimum set; never `*` |
| `entry-point` | when a CLI twin exists | exact CLI command; `{PLACEHOLDERS}` map to skill args |

### Body sections (fixed order, so prompt extraction is mechanical)

```markdown
## System prompt
<single, version-controlled prompt — what prompts.py reads>

## Inputs
<which AgentInput fields the skill reads>

## Outputs
<which AgentScore / Pydantic schema the skill returns>

## Invocation
<exact CLI command(s); what files it writes under data/>

## When to use this skill
<one paragraph for the coding agent>
```

The `## System prompt` section is **byte-identical** to the prompt Python's
`run()` sends. `src/ats/agents/prompts.py` extracts it at import time;
`tests/test_prompt_parity.py` compares the two strings.

### Skill inventory

| Skill | CLI twin | Spec of CLI twin | `user_invocable` |
|---|---|---|---|
| `/cycle-now` | `ats cycle run --now` | 03 | yes |
| `/analyze-symbol <SYM>` | `ats analyze <SYM>` | 04 / 07 | yes |
| `/agent-structure` | `ats agent structure run …` | 04 / 07 | no |
| `/agent-momentum` | `ats agent momentum run …` | 04 | no |
| `/agent-funding` | `ats agent funding run …` | 04 / 07 | no |
| `/agent-liquidity` | `ats agent liquidity run …` | 04 / 07 | no |
| `/agent-sentiment` | `ats agent sentiment run …` | 07 | no |
| `/agent-narrative` | `ats agent narrative run --cycle-ts …` | 07 | no |
| `/post-mortem <id>` | `ats learn post-mortem <id>` | 08 | yes |
| `/weekly-reflection` | `ats reflect run --since 7d` | 08 | yes |

---

## CLI parity test pattern

The single most important test of this spec: **every skill with an `entry-point`
produces the same artifact bytes whether invoked via a coding agent or via the
CLI twin.**

```python
# tests/test_skill_cli_parity.py
@pytest.mark.parametrize("skill_dir", [d for d in SKILLS_DIR.iterdir() if (d / "SKILL.md").exists()])
def test_skill_has_callable_entrypoint(skill_dir):
    skill = frontmatter.load(skill_dir / "SKILL.md")
    entry = skill.metadata.get("entry-point")
    if entry is None:
        return
    cmd = render_template(entry, FIXTURE_ARGS[skill.metadata["name"]])
    subprocess.run(cmd, env={"ATS_DATA_DIR": str(out_dir), **base_env}, check=True)
    invoke_skill_python(skill.metadata["name"], FIXTURE_ARGS[skill.metadata["name"]], data_dir=out_dir2)
    assert tree_hash(out_dir) == tree_hash(out_dir2)
```

`tree_hash()` is sha256 of every file's contents in path-sorted order; JSON is
normalized with `sort_keys=True` first; intrinsically nondeterministic files
(chart PNGs) are excluded by extension.

---

## Portability checklist (non–Claude-Code runtimes)

- [ ] `entry-point` is a plain shell command — no Claude Code-specific tooling
- [ ] All required env vars (`ANTHROPIC_API_KEY`, `DATABASE_URL`,
      `X_BEARER_TOKEN`) documented in the README and the skill's `## Invocation`
- [ ] Skill body uses only standard Markdown — no `<thinking>`, no Claude
      Code-only XML directives
- [ ] The runtime can either read `## System prompt` and inject it, or invoke the
      CLI twin and let the CLI handle the LLM call (the latter is the recommended
      portable default)
- [ ] No skill assumes Claude Code's `data/` path; output paths come from
      `ATS_DATA_DIR` with a sane default

Manual portability acceptance check at spec close: install OpenCode in a clean
dir, symlink `.claude/skills/`, invoke `/analyze-symbol BTCUSDT` against the seed
DB, verify the produced `data/cycle_<ts>/agent_runs_BTCUSDT.json` matches the
Claude Code run byte-for-byte.

---

## Components

### Python

| Path | Responsibility |
|---|---|
| `src/ats/api/main.py` | FastAPI app factory, CORS for localhost, OpenAPI metadata |
| `src/ats/api/routes/*.py` | one module per resource group (health, top_picks, signals, narratives, learnings, reflections, journal, replay, stream) |
| `src/ats/api/schemas.py` | Pydantic response models |
| `src/ats/api/events.py` | event publisher (Tier 3 only) |
| `src/ats/mcp/server.py` | MCP server registering the read-only tool set; reuses the API query helpers |
| `src/ats/mcp/tools.py` | Pydantic-typed tool definitions and handlers |
| `src/ats/contracts/` | shared Pydantic models re-used by API + MCP (single source of truth) |
| `src/ats/cli/serve.py` | `ats serve`, `ats serve --mcp-only`, `ats serve --live` |
| `src/ats/cli/skills.py` | `ats skills list`, `ats skills validate` |
| `src/ats/cli/mcp.py` | `ats mcp list-tools`, `ats mcp validate` |
| `.claude/skills/*/SKILL.md` | the 10 skills above |

### Frontend (`ui/`)

| Path | Responsibility |
|---|---|
| `ui/app/page.tsx` | Home: regime banner, top-10 table, latest signals strip |
| `ui/app/signals/[id]/page.tsx` | Signal detail: agent breakdown, learnings, reasons, SL/TP sparkline |
| `ui/app/narratives/page.tsx` | active narratives + related symbols |
| `ui/app/reflection/page.tsx` | latest reflection markdown + reliability chart |
| `ui/app/journal/page.tsx` | paper trades table |
| `ui/lib/api.ts` / `ui/lib/sse.ts` | typed fetch wrappers; EventSource with polling fallback |

---

## CLI added

```text
ats serve [--host 127.0.0.1] [--port 8080]    # FastAPI + MCP (stdio + SSE)
ats serve --mcp-only                           # MCP on stdio only; no HTTP
ats serve validate                             # programmatic smoke test
ats skills list                                # list every SKILL.md + frontmatter
ats skills validate                            # structure-lint + prompt-parity + CLI-parity
ats mcp list-tools                             # print the MCP tool inventory
ats mcp validate                               # boot MCP on stdio, list tools, hit each
```

`ats serve --live` exists but is an **M4** concern (`specs/10-live-operations.md`).

---

## Validation

### Smoke test

```bash
uv run ats serve &
curl -s http://127.0.0.1:8080/api/health | jq
curl -s http://127.0.0.1:8080/api/top-picks/latest | jq '.[0]'
uv run ats mcp validate
uv run ats skills validate
cd ui && npm install && npm run dev    # open http://localhost:3000
```

### Acceptance criteria

- [ ] **No write methods:** scanning the route table yields zero POST/PUT/DELETE/PATCH handlers
- [ ] **No write MCP tools:** the registered tool set has zero side-effect-implying names (`create_*`, `update_*`, `delete_*`, `place_*`, `kick_*`, `run_*` except `retrieve_*`)
- [ ] **MCP ↔ HTTP schema parity:** every MCP tool with an HTTP twin returns a payload validating against the same Pydantic model the route uses
- [ ] **MCP starts on stdio:** `ats serve --mcp-only` boots, lists every tool, responds to one read call within 2s
- [ ] **OpenAPI valid:** `/openapi.json` validates against OpenAPI 3.1
- [ ] **SSE liveness + fallback:** publish `cycle_close` → client receives within 1s; with Redis stopped, client falls back to 15s polling
- [ ] **Skill inventory present:** all 10 skills exist at `.claude/skills/<name>/SKILL.md` with required frontmatter
- [ ] **Body shape:** every `SKILL.md` body has the five canonical H2 sections in order
- [ ] **Prompt parity:** `tests/test_prompt_parity.py` passes — each agent skill's `## System prompt` is byte-identical to what Python sends
- [ ] **CLI parity:** `tests/test_skill_cli_parity.py` passes — every skill with an `entry-point` produces artifacts byte-identical to its CLI twin
- [ ] **Localhost only:** server binds `127.0.0.1` by default; non-loopback requires explicit `--host`
- [ ] **Portability check (manual):** the OpenCode acceptance check above passes

### pytest + Vitest

| File | Asserts |
|---|---|
| `tests/test_api_readonly.py` | no non-GET handlers registered |
| `tests/test_api_top_picks.py` | seeded `top_picks` → matching response |
| `tests/test_api_sse.py` | publish event → fixture client receives within 1s |
| `tests/test_mcp_readonly.py` | no side-effect-named tools; every tool returns a valid shape |
| `tests/test_mcp_http_parity.py` | each MCP tool's payload validates against its HTTP route's model |
| `tests/test_prompt_parity.py` | each agent skill's system prompt == what Python sends |
| `tests/test_skill_cli_parity.py` | skill artifacts == CLI-twin artifacts |
| `ui/__tests__/home.test.tsx` | renders top-10 from a mocked API |
| `ui/__tests__/signal-detail.test.tsx` | renders agent breakdown |

### `ats serve validate` / `ats skills validate` / `ats mcp validate`

- `serve validate`: starts FastAPI on an ephemeral port; hits health + top-picks + signals; publishes a fake SSE event; shuts down; exits 0.
- `skills validate`: runs the structure-lint, prompt-parity, and CLI-parity tests; exits 0.
- `mcp validate`: boots the MCP server on stdio, lists tools, calls each against the seed DB; exits 0.

---

## Risks / open questions

- **Schema drift between API and MCP.** Mitigation: both import from
  `src/ats/contracts/` — one source of truth. The parity test runs both
  transports and diffs the JSON.
- **Frontmatter parser drift.** Pin `python-frontmatter`; if the runtime parses
  YAML differently than the CLI, parity tests will lie.
- **Case-insensitive filesystem (macOS).** `agent-Structure/` and
  `agent-structure/` collide; a pre-commit hook should reject case collisions.
- **Coding-agent-specific prompt envelopes.** The `## System prompt` text must be
  envelope-free; the Python `run()` applies any cache envelope before calling
  Anthropic. Each runtime wraps it its own way.
- **UI scope creep.** The signal-detail chart is the hardest part; ship a simple
  SVG of entry/SL/TP over a 100-bar sparkline — no interactive zoom.
- **SSE keepalive.** Long-lived connections need a ~20s heartbeat ping through
  proxies — include it from day one.
