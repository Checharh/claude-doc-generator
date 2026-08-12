# Claude Doc Generator — Project Plan

A system that turns any local repository into a client-ready Google Slides deck via
`/generate-slides` in Claude Code, backed by a local MCP server.

**Status:** Phase 0 complete. Phase 0.5 written, awaiting one interactive OAuth run.
**Last updated:** 12 August 2026

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Prerequisites](#2-prerequisites)
3. [Architecture](#3-architecture)
4. [Phase 0 — Make It Run](#phase-0--make-it-run-complete)
5. [Phase 0.5 — Hardcoded Proof of Life](#phase-05--hardcoded-proof-of-life)
6. [Phase 1 — Reliable Extraction](#phase-1--reliable-extraction)
7. [Phase 2 — UX](#phase-2--ux)
8. [Phase 3 — Distribution & DevEx](#phase-3--distribution--devex)
9. [Phase 4 — Hardening](#phase-4--hardening)
10. [Reference: Extraction Contract](#10-reference-extraction-contract)
11. [Reference: Config Files](#11-reference-config-files)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Current State

### What exists

| File | Role | Status |
|---|---|---|
| `workspace.py` | OAuth, template management, Slides/Docs rendering | Rewritten, compiles |
| `server.py` | MCP server, 3 tools | Rewritten, handshake verified |
| `auth.py` | One-time interactive Google authorization | New |
| `setup_template.py` | Upload `.pptx` → native Slides + normalize placeholders | New, not yet run |
| `smoke_test.py` | Phase 0.5 hardcoded end-to-end proof | New, not yet run |
| `.mcp.json` | Registers the server with Claude Code | New |
| `.gitignore`, `requirements.txt` | Hygiene | New |
| `Hackathon_Final_Presentation_Template.pptx` | 9-slide master (presentation) | Source of truth |
| `Hackathon_Brochure_Template.pptx` | 6-slide master (brochure) | Not yet wired |

### Verified facts (not assumptions)

- `mcp` **2.0.0** removed `mcp.server.fastmcp`. The class is `mcp.server.MCPServer`.
- MCP protocol version negotiated: **2025-11-25**. Tools exposed: `generate_slides`,
  `generate_brochure`, `list_template_placeholders`.
- `credentials.json` is an **OAuth installed-app client** (`{"installed": {...}}`),
  not a service account key.
- **Neither `.pptx` contains `{{TAGS}}`.** The presentation master uses human-readable
  bracket placeholders on slide 1 only:
  `[ Agent / Project Name ]`, `[ One-line project tagline ]`,
  `[ Presenter name(s), role(s) ]`, `[ Date ]`.
- The old `PRESENTATION_TEMPLATE_ID` was 33 chars — an **unconverted binary `.pptx`**
  in Drive. The Slides API cannot `batchUpdate` those. Native Google files have 44-char IDs.
- The template has **no slot for `STATUS` or `INDUSTRY`** on any of its 9 slides.

### Known open issues

| Issue | Impact | Owner decision needed |
|---|---|---|
| No `{{STATUS}}` / `{{INDUSTRY}}` slot in the template | Those two values are dropped silently (now reported as `x0`) | Add text boxes to slide 1, or remove from the tool signature |
| Brochure Docs template tags unverified | `generate_brochure` may replace nothing | Run `list_template_placeholders('brochure')` after auth |
| Repo is not yet under git | No history, no review | Safe to `git init` now — secrets are out and `.gitignore` is in place |

---

## 2. Prerequisites

### Accounts & access

- [x] Google Cloud project with **Drive API**, **Slides API**, and **Docs API** enabled
- [x] OAuth 2.0 Client ID of type **Desktop app**, downloaded as JSON
- [ ] Google account added as a **test user** on the OAuth consent screen
      (required while the app is in "Testing" mode, or the grant is rejected)
- [ ] *(Optional, Phase 2)* A shared Drive folder for generated decks — note its folder ID

### Local environment

- [x] Python 3.14 virtualenv at `./venv`
- [x] Dependencies installed (`mcp==2.0.0`, `google-api-python-client`, `google-auth-oauthlib`)
- [x] Claude Code CLI installed
- [x] Secrets directory `~/.config/claude-doc-generator/` (mode `700`)
- [x] `credentials.json` moved there (mode `600`), out of the repo

### Reinstall from scratch

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
mkdir -p ~/.config/claude-doc-generator && chmod 700 ~/.config/claude-doc-generator
cp /path/to/downloaded/client_secret.json ~/.config/claude-doc-generator/credentials.json
chmod 600 ~/.config/claude-doc-generator/credentials.json
```

### Environment variables (all optional — defaults shown)

| Variable | Default |
|---|---|
| `DOCGEN_CONFIG_DIR` | `~/.config/claude-doc-generator` |
| `DOCGEN_CREDENTIALS` | `$DOCGEN_CONFIG_DIR/credentials.json` |
| `DOCGEN_TOKEN` | `$DOCGEN_CONFIG_DIR/token.json` |
| `DOCGEN_TEMPLATES` | `$DOCGEN_CONFIG_DIR/templates.json` |
| `DOCGEN_PRESENTATION_TEMPLATE_ID` | from `templates.json` |
| `DOCGEN_OUTPUT_FOLDER_ID` | none (decks land in Drive root) |

---

## 3. Architecture

### The core principle: separate extraction from rendering

```
repo → [Claude: evidence gathering] → extraction manifest (JSON) → [confidence gate] → MCP tool → Slides
        nondeterministic                                                                deterministic
        tested via snapshots                                                            tested via mocks
```

Inserting a typed manifest between the agent and the tool is the decision everything
else depends on. It makes each field auditable (`value`, `source`, `confidence`),
enables the confirmation UX, and lets extraction be regression-tested without ever
calling the Google API.

### Manifest shape

```json
{
  "AGENT_NAME": { "value": "Atlas Router", "source": "README.md:1", "confidence": "high" },
  "TAGLINE":    { "value": "Routes tickets in under a second", "source": "package.json:description", "confidence": "medium" },
  "TEAM_NAMES": { "value": "Cesar Hinojosa, Ana Ruiz", "source": "git shortlog", "confidence": "high" },
  "DATE":       { "value": "12 August 2026", "source": "date +%Y-%m-%d", "confidence": "high" },
  "STATUS":     { "value": "Beta", "source": "package.json:version=0.7.1 + .github/workflows", "confidence": "medium" },
  "INDUSTRY":   { "value": "Developer Tools", "source": "deps: mcp, click", "confidence": "high" }
}
```

### Three-tier source cascade

Resolve each field by walking tiers in order; stop at the first hit.

| Tier | Source | Confidence |
|---|---|---|
| **1** | `.docgen.yml` at repo root — explicit override, used verbatim | `high` |
| **2** | `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `composer.json`, `*.csproj`, README H1 + first paragraph, `CODEOWNERS`, `git shortlog` | `high`–`medium` |
| **3** | Entrypoints, docstrings, system prompts, dependency fingerprints, directory naming | `medium`–`low` |

### Two defense layers against a broken deck

A slide reaching a client with `{{TAGLINE}}` printed on it is the one unrecoverable
failure mode, so it is defended twice — the agent can be wrong, the server cannot:

1. **Agent side** — confidence gate + user confirmation before generating.
2. **Server side** — `_sanitize()` maps empty/unfilled values to fallbacks, and
   `_sweep_orphans()` blanks any `{{TAG}}` left in the copy after `batchUpdate`.

Plus **occurrence verification**: every replacement reports `occurrencesChanged`, so a
tag that matches nothing surfaces as `x0` instead of a silent success.

---

## Phase 0 — Make It Run *(COMPLETE)*

Every item below was verified, not assumed.

- [x] **0.1** Fix MCP import — `mcp.server.fastmcp` → `from mcp.server import MCPServer`
- [x] **0.2** Add `if __name__ == '__main__': mcp.run()` entrypoint
- [x] **0.3** Implement `get_credentials()` — installed-app OAuth, token refresh,
      `RefreshError` → fresh grant
- [x] **0.4** Move secrets to `~/.config/claude-doc-generator/` (`600`), env-overridable,
      with a repo fallback so the move is non-breaking
- [x] **0.5** Resolve all paths absolutely — the MCP server runs with the *target repo*
      as cwd, so relative paths would break
- [x] **0.6** Template IDs read from `templates.json` by kind, no longer hardcoded/crossed
- [x] **0.7** Rewrite `generate_google_doc` against its own tags (was referencing
      undefined names and the wrong tag set)
- [x] **0.8** Import `generate_google_slides` correctly in `server.py`
- [x] **0.9** Add `_sanitize()` + `_sweep_orphans()` safety net
- [x] **0.10** Add `_occurrences()` verification on every `batchUpdate`
- [x] **0.11** Add `list_template_placeholders` MCP tool (self-describing templates)
- [x] **0.12** Add `.gitignore`, `requirements.txt`, `.mcp.json`, `auth.py`
- [x] **0.13** Verify all modules compile
- [x] **0.14** Verify MCP stdio handshake — 3 tools listed, clean actionable error on
      missing config
- [ ] **0.15** `git init && git add . && git commit` — safe now that secrets are out

**Acceptance:** ✅ Server starts, handshakes, lists 3 tools, and fails with
`Configuration error: No presentation template configured...` rather than a `NameError`.

---

## Phase 0.5 — Hardcoded Proof of Life

Goal: prove the Google pipeline works end to end with **no agent and no repo analysis** —
just six hardcoded values landing in a real deck.

Code is written and compiles. These three steps need a browser, so they must be run
interactively. In Claude Code, prefix with `!` to run them in-session.

### Step 1 — Authorize

- [ ] `! ./venv/bin/python auth.py`

Opens a browser for the Google consent screen. Writes `token.json` (mode `600`) to
`~/.config/claude-doc-generator/`. Run once; the refresh token persists.

**Expected:** `Authorized. Token saved to /Users/.../token.json`

### Step 2 — Build the template

- [ ] `! ./venv/bin/python setup_template.py`

Uploads `Hackathon_Final_Presentation_Template.pptx` **converted to native Google
Slides**, then rewrites the four bracket placeholders into `{{TAGS}}`. Idempotent.
Registers the resulting ID in `templates.json`.

**Expected:**
```
[ok  ] [ Agent / Project Name ]           -> {{AGENT_NAME}}   x1
[ok  ] [ One-line project tagline ]       -> {{TAGLINE}}      x1
[ok  ] [ Presenter name(s), role(s) ]     -> {{TEAM_NAMES}}   x1
[ok  ] [ Date ]                           -> {{DATE}}         x1
```

If every line reads `MISS`, the converted text differs from the raw XML. Diagnose with:

- [ ] *(only if needed)* `! ./venv/bin/python setup_template.py --reuse <ID> --dump`

### Step 3 — Smoke test

- [ ] `! ./venv/bin/python smoke_test.py`

Clones the template, pushes the hardcoded payload, verifies each tag landed, prints the URL.

**Expected:**
```
[ok  ] {{AGENT_NAME}}   x1   Doc Generator
[ok  ] {{TAGLINE}}      x1   Turns any repository into a client-ready deck
[ok  ] {{TEAM_NAMES}}   x1   Cesar Hinojosa
[ok  ] {{DATE}}         x1   12 August 2026
[n/a ] {{STATUS}}       x0   Prototype
[n/a ] {{INDUSTRY}}     x0   Developer Tools
Deck: https://docs.google.com/presentation/d/.../edit
PASS: every required placeholder was replaced.
```

`STATUS` and `INDUSTRY` showing `x0` is **expected** — the template has no slot for them.

### Step 4 — Wire into Claude Code

- [ ] Restart Claude Code so `.mcp.json` is picked up
- [ ] Run `/mcp` — confirm `doc-generator` is connected with 3 tools
- [ ] Ask Claude: *"list the template placeholders"* — confirm it returns the 4 tags

### Step 5 — Decide on the two orphan fields

- [ ] Add `{{INDUSTRY}}` and `{{STATUS}}` text boxes to slide 1 of the master
      (e.g. a subtitle line under the tagline), **or** remove them from the
      `generate_slides` signature

**Acceptance:** A real Google Slides URL opens to a title slide reading
"Doc Generator / Turns any repository into a client-ready deck / Cesar Hinojosa / 12 August 2026".

---

## Phase 1 — Reliable Extraction

*Estimated: 2–3 days*

- [ ] **1.1** Write `~/.claude/commands/generate-slides.md` (see [§11](#11-reference-config-files))
- [ ] **1.2** Write the extraction rulebook — `CLAUDE.md` / `docgen-extraction.md`
      (see [§10](#10-reference-extraction-contract))
- [ ] **1.3** Implement `.docgen.yml` Tier-1 override parsing
- [ ] **1.4** Build the golden-repo fixture set — 5 adversarial cases:
  - [ ] rich Node repo (full `package.json` + README)
  - [ ] bare Python repo, no README
  - [ ] monorepo (which `package.json` wins?)
  - [ ] fork with noisy git history (bots, ex-employees)
  - [ ] scaffold-only repo (should yield `Concept`)
- [ ] **1.5** Snapshot-test the **extraction manifest**, never the slides
- [ ] **1.6** Unit-test the render layer with a mocked Drive/Slides service
- [ ] **1.7** Verify brochure template tags, wire `generate_brochure` to the same manifest

**Acceptance:** All 5 fixtures produce sane manifests. Zero fabricated fields.

---

## Phase 2 — UX

*Estimated: 2 days*

- [ ] **2.1** Confirmation table + single batched question round
- [ ] **2.2** Low-confidence fields become **multiple choice with a recommendation**,
      never open-ended questions
- [ ] **2.3** `--yes` non-interactive path with documented fallbacks + a report of which
      fields were defaulted
- [ ] **2.4** `.docgen.yml` write-back after confirmation — run #2 asks nothing
- [ ] **2.5** Place output in a shared Drive folder via `DOCGEN_OUTPUT_FOLDER_ID`
      (already supported in `render_presentation`)

**Target interaction:**

```
Extracted from claude-doc-generator/

  AGENT_NAME   Doc Generator            ← package/dir name       [medium]
  TAGLINE      Generate client-ready decks from any repo
                                        ← README first line      [medium]
  TEAM_NAMES   Cesar Hinojosa           ← git shortlog           [high]
  DATE         12 August 2026           ← system clock           [high]
  STATUS       Prototype                ← no CI, 14 commits      [high]
  INDUSTRY     Developer Tools          ← MCP + CLI deps         [high]

Generate with these? [Enter to accept / type a field name to edit]
```

---

## Phase 3 — Distribution & DevEx

*Estimated: 2–3 days*

- [ ] **3.1** Package as a **Claude Code plugin** (command + skill + MCP config) —
      one install, works in every repo
- [ ] **3.2** Move the command to user scope so no per-repo `CLAUDE.md` copy is needed
- [ ] **3.3** `docgen auth` as a friendly wrapper; clear errors on expired/revoked tokens
      instead of raw Google tracebacks
- [ ] **3.4** README with a 60-second quickstart
- [ ] **3.5** Restore `generate_brochure` on the shared extraction layer — two outputs,
      one manifest

> **Why plugin, not per-repo `CLAUDE.md`:** the goal is "run `/generate-slides` in *any*
> local repository". Copying a `CLAUDE.md` into every repo contradicts that. Per-repo
> files should carry **overrides only** (`.docgen.yml`), never the procedure.

---

## Phase 4 — Hardening

*Ongoing*

- [ ] **4.1** Confirm the auth model. **Recommendation: stay on per-user OAuth.** Files
      land in the user's own Drive with normal ownership and sharing. A service account
      owns files in a Drive with no UI and needs explicit sharing on every copy — wrong
      shape for an internal tool. Revisit only for unattended/CI generation.
- [ ] **4.2** Structured logging of every manifest → build a corpus of where extraction is
      weak, tune the rulebook against real misses instead of guesses
- [ ] **4.3** Drive quota / rate-limit backoff
- [ ] **4.4** Template versioning — pin a template ID per deck type so a designer editing
      the master can't silently break generation
- [ ] **4.5** Rotate the OAuth client secret before any wider rollout

---

## 10. Reference: Extraction Contract

Drop this into `CLAUDE.md` or `docgen-extraction.md`.

### Per-field rules

| Field | Source order | Normalization | Never |
|---|---|---|---|
| `AGENT_NAME` | `.docgen.yml` → README H1 → manifest `name` → dir name | Title Case; strip `-mcp`, `-api`, `my-`, `@scope/`; hyphens → spaces | Use the dir name if it's `app`, `test`, `untitled`, `new-project` — ask instead |
| `TAGLINE` | `.docgen.yml` → README subtitle / first sentence → `description` → repo description | ≤ 12 words, one line, benefit-oriented, no trailing period | Invent metrics ("3x faster", "99.9% uptime") — this lands on a customer slide |
| `TEAM_NAMES` | `.docgen.yml` → `CODEOWNERS` → `git shortlog -sne --no-merges` top 5 → `author` | "First Last, First Last"; dedupe aliases by email; **strip all emails** | Include `[bot]`, dependabot, renovate, github-actions, `noreply@` |
| `DATE` | System clock via `date +%Y-%m-%d` | Locale format, e.g. `12 August 2026` | Use the model's own sense of today's date — it will be wrong |
| `STATUS` | Evidence rules below | Enum only | Free text |
| `INDUSTRY` | Dependency fingerprint → README domain nouns | Taxonomy only | Free text |

### Closed enums

Constraining the two soft fields is the single highest-leverage anti-hallucination move —
open-ended fields are where models improvise.

**`STATUS`** — first matching rule wins:

| Value | Evidence |
|---|---|
| `Production` | Version ≥ 1.0.0 **and** CI config **and** deploy manifest (Dockerfile / k8s / vercel.json / fly.toml) |
| `Beta` | Version ≥ 0.5.0 or a passing test suite, plus CI |
| `MVP` | Working entrypoint + README usage section, no CI |
| `Prototype` | < 20 commits, or README says WIP/experimental |
| `Concept` | Scaffolding only, no functional entrypoint |

**`INDUSTRY`** — classify by dependency fingerprint first (deterministic), README nouns second:

| Value | Signals |
|---|---|
| Fintech | `stripe`, `plaid`, `braintree`, ledger models, ISO-20022 |
| Healthtech | `fhir`, `hl7`, `dicom`, HIPAA mentions |
| Developer Tools | MCP servers, CLIs, linters, SDKs, CI plugins |
| E-Commerce | `shopify`, `commercetools`, cart/checkout models |
| Logistics | routing, fleet, warehouse, `osrm` |
| Marketing & Sales | `hubspot`, `salesforce`, campaign models |
| Data & Analytics | `dbt`, `airflow`, `spark`, warehouse connectors |
| Enterprise SaaS | multi-tenant auth, RBAC, billing + org models |
| Education, Media, Gaming, Real Estate, Legal, HR, Security | domain-specific vocabulary |
| **General Technology** | terminal fallback — honest, on-brand, never wrong |

### Missing-information SOP

**Never hallucinate, never leave blank, never ship a visible `{{TAG}}`.**

| Confidence | Meaning | Behavior |
|---|---|---|
| `high` | Tier 1, or an exact Tier 2 field | Use silently |
| `medium` | Tier 2 prose or a strong Tier 3 signal | Use, but **show it** in the confirmation |
| `low` / absent | No usable evidence | **Ask** — multiple choice, recommendation first |

Non-interactive fallbacks (`--yes`):

| Field | Fallback |
|---|---|
| `AGENT_NAME` | Title-cased repo directory name |
| `TAGLINE` | `"A {INDUSTRY} project"` — deliberately bland, obviously a placeholder |
| `TEAM_NAMES` | Top git authors; `"Internal Team"` if not a git repo |
| `STATUS` | `Prototype` — never over-claim maturity to a client |
| `INDUSTRY` | `General Technology` |

### Hard constraints

- **NEVER** invent metrics, customer names, funding, headcount, or dates.
- **NEVER** put an email address in `TEAM_NAMES`.
- **NEVER** pass an empty string — use the documented fallback.
- **NEVER** claim a maturity level above the evidence. When torn, pick the lower `STATUS`.
- **ALWAYS** cite a source for every field. "It seemed right" is not a source.

---

## 11. Reference: Config Files

### `.mcp.json` *(done)*

Absolute paths are mandatory — the server launches with the target repo as cwd.

```json
{
  "mcpServers": {
    "doc-generator": {
      "command": "/Users/cesarhinojosa/Development/claude-doc-generator/venv/bin/python",
      "args": ["/Users/cesarhinojosa/Development/claude-doc-generator/server.py"],
      "env": {
        "DOCGEN_CONFIG_DIR": "/Users/cesarhinojosa/.config/claude-doc-generator"
      }
    }
  }
}
```

### `~/.claude/commands/generate-slides.md` *(Phase 1)*

```markdown
---
description: Generate a Google Slides deck from this repository
allowed-tools: Read, Grep, Glob, Bash(git shortlog:*), Bash(git log:*), Bash(date:*), mcp__doc-generator__*
argument-hint: "[--yes]"
---

Generate a presentation for the repository at $(pwd).

## Step 1 — Extract
Follow the extraction contract in @docgen-extraction.md. Produce an extraction manifest
(do not call any MCP tool yet):

{ "field": { "value": ..., "source": "<file:line or command>", "confidence": "high|medium|low" } }

Prefer parallel reads. Do not read more than ~15 files; stop once every field is resolved.

## Step 2 — Gate
- All fields high/medium → print the summary table, ask for one confirmation.
- Any field low → include it as a multiple-choice question, your recommendation first.
- `--yes` passed → skip confirmation, apply the documented fallbacks, and report which
  fields were defaulted.

## Step 3 — Generate
Call `mcp__doc-generator__list_template_placeholders` to confirm the supported tags, then
`mcp__doc-generator__generate_slides` with the confirmed values. Return the URL.
If the tool reports x0 for a tag, surface that — it means the template has no slot for it.
If the tool returns an error, surface it verbatim; do not retry with altered arguments.

## Step 4 — Persist
Offer to write `.docgen.yml` with the confirmed values.
```

### `.docgen.yml` — per-repo Tier-1 override *(Phase 1)*

```yaml
agent_name: Atlas Router
tagline: Routes support tickets to the right team in under a second
industry: Enterprise SaaS
team_names: Cesar Hinojosa, Ana Ruiz
status: Beta
```

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: mcp.server.fastmcp` | mcp 2.0 renamed it | `from mcp.server import MCPServer` |
| `Configuration error: No presentation template configured` | `templates.json` missing | Run `setup_template.py` |
| `Not authorized with Google` | No `token.json` | Run `auth.py` |
| All tags report `x0` | Template still has bracket placeholders, or is an unconverted `.pptx` | Run `setup_template.py`; verify the ID is 44 chars |
| `setup_template.py` reports all `MISS` | Converted text differs from `PLACEHOLDER_MAP` | `setup_template.py --reuse <ID> --dump`, then fix the map |
| Slides API 400 on `batchUpdate` | Template is a binary `.pptx` in Drive, not native Slides | Re-upload with `mimeType: application/vnd.google-apps.presentation` |
| `access_denied` on the consent screen | Account not a test user | Add it under OAuth consent screen → Test users |
| `RefreshError` | Token revoked or scopes changed | Delete `token.json`, re-run `auth.py` |
| Server can't find credentials when run from another repo | Relative path | Already fixed — paths resolve absolutely |

---

## Progress Summary

| Phase | Items | Done |
|---|---|---|
| Prerequisites | 8 | 6 |
| Phase 0 — Make It Run | 15 | 14 |
| Phase 0.5 — Proof of Life | 8 | 0 |
| Phase 1 — Extraction | 11 | 0 |
| Phase 2 — UX | 5 | 0 |
| Phase 3 — DevEx | 5 | 0 |
| Phase 4 — Hardening | 5 | 0 |

**Next action:** `! ./venv/bin/python auth.py`
