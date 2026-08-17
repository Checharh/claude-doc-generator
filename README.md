# Claude Doc Generator

Turn any local repository into a client-ready Google Slides deck from inside Claude Code.

The tool reads a codebase — README, manifests, git history, dependencies — works out what
the project _is_, and fills a Google Slides master template with the result. A local MCP
server does the Google Workspace work; Claude Code does the understanding.

```
repository  →  Claude Code  →  MCP server  →  Google Slides
               (extraction)    (rendering)     (your deck)
```

**Status:** the rendering pipeline is working end to end. Repository extraction is next —
today the values are supplied by hand.

---

## How it works

Generation is deliberately split in two, because the halves have different failure modes:

| Stage                                                             | Who         | Determinism                                                       |
| ----------------------------------------------------------------- | ----------- | ----------------------------------------------------------------- |
| **Extraction** — read the repo, decide what each field should say | Claude Code | Stochastic. Verified by showing you the values before generating. |
| **Rendering** — clone the template, replace tags, verify          | MCP server  | Deterministic. Every replacement is counted.                      |

The template lives in **Google Drive**, not on disk. `templates.json` (or `.env`) holds a
Drive file ID; each generation copies that live file, so editing the master in Google
Slides changes the next deck immediately.

### Placeholders

The master template uses `{{TAG}}` placeholders. Six are supported:

| Tag              | Content                                                     |
| ---------------- | ----------------------------------------------------------- |
| `{{AGENT_NAME}}` | Project or agent name                                       |
| `{{TAGLINE}}`    | One line, benefit-oriented, ≤ 12 words                      |
| `{{TEAM_NAMES}}` | Comma-separated, no emails                                  |
| `{{DATE}}`       | Presentation date                                           |
| `{{STATUS}}`     | `Concept` \| `Prototype` \| `MVP` \| `Beta` \| `Production` |
| `{{INDUSTRY}}`   | Fixed taxonomy (Fintech, Developer Tools, …)                |

`setup_template.py` normalizes other conventions (`[AGENT_NAME]`, `[ Agent / Project Name ]`)
into this form, so a hand-made deck can be adopted without retyping it.

A tag with **no slot in the template** is reported rather than silently dropped, and any
`{{TAG}}` left unfilled is blanked before the deck is returned — a deck must never reach a
client with `{{TAGLINE}}` printed on it.

---

## Setup

### 1. Install

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 2. Google Cloud

In one Google Cloud project:

1. Enable the **Drive**, **Slides**, and **Docs** APIs — all three, same project
2. **OAuth consent screen** → External, publishing status _Testing_, and add your Google
   account under **Test users**
3. **Credentials → Create OAuth client ID → Desktop app** → download the JSON
4. Save it as `credentials.json` in the project folder

> Leave the app in _Testing_. The `drive` scope is **restricted**, so publishing to
> Production triggers Google's full verification process — weeks of work for an internal
> tool. Testing mode allows 100 test users. The trade-off is that refresh tokens expire
> after 7 days, so you re-run `auth.py` about weekly.

### 3. Authorize

```bash
./venv/bin/python auth.py
```

Opens a browser once and writes `token.json`. You'll see a _"Google hasn't verified this
app"_ warning — expected; choose **Advanced → Go to … (unsafe)** and grant all three scopes.

### 4. Register a template

Point it at a Slides deck already in your Drive:

```bash
./venv/bin/python setup_template.py --list                    # browse your decks
./venv/bin/python setup_template.py --drive "<URL or ID>"     # register one
```

A local `.pptx` works too — it gets uploaded and converted:

```bash
./venv/bin/python setup_template.py --pptx MyTemplate.pptx
```

---

## Testing

### End-to-end smoke test

Pushes six hardcoded values into a real deck. No repository analysis, no agent — this
tests only the Google pipeline.

```bash
./venv/bin/python smoke_test.py
```

Passing output:

```
Template placeholders:
  {{AGENT_NAME}}, {{DATE}}, {{INDUSTRY}}, {{STATUS}}, {{TAGLINE}}, {{TEAM_NAMES}}

Generating deck with hardcoded values...

Replacements:
  [ok  ] {{AGENT_NAME}}   x1   Doc Generator
  [ok  ] {{TAGLINE}}      x1   Turns any repository into a client-ready deck
  [ok  ] {{TEAM_NAMES}}   x1   Cesar Hinojosa
  [ok  ] {{DATE}}         x1   17 August 2026
  [ok  ] {{STATUS}}       x1   Prototype
  [ok  ] {{INDUSTRY}}     x1   Developer Tools

Deck: https://docs.google.com/presentation/d/.../edit

PASS: every required placeholder was replaced.
```

Exit code is `1` if any required tag replaced nothing. `x0` means the tag isn't in the
template — a mapping problem, not an API problem.

### Inspect a template

```bash
./venv/bin/python setup_template.py --drive "<ID>" --dump
```

Prints every text string in the deck. This is the tool for "why didn't my tag get
replaced" — usually the template says something slightly different from what you expect.

### Verify the MCP server

Confirms the server starts, handshakes over stdio, and exposes its tools:

```bash
./venv/bin/python -c "
import asyncio, server
print([t.name for t in asyncio.run(server.mcp.list_tools())])"
```

Expected: `['generate_slides', 'generate_brochure', 'list_template_placeholders']`

### From inside Claude Code

Restart Claude Code so it picks up `.mcp.json`, then:

```
/mcp                                  → doc-generator should be connected
"list the template placeholders"      → returns the six tags
```

---

## Configuration

Config resolves in this order, so a deployment can override the checked-out files without
editing anything:

```
process env  >  .env  >  templates.json  >  built-in default
```

```bash
cp .env.example .env
```

| Variable                          | Default                               |
| --------------------------------- | ------------------------------------- |
| `DOCGEN_PRESENTATION_TEMPLATE_ID` | from `templates.json`                 |
| `DOCGEN_BROCHURE_TEMPLATE_ID`     | from `templates.json`                 |
| `DOCGEN_OUTPUT_FOLDER_ID`         | none — decks land in My Drive root    |
| `DOCGEN_CONFIG_DIR`               | the project folder                    |
| `DOCGEN_CREDENTIALS`              | `$DOCGEN_CONFIG_DIR/credentials.json` |
| `DOCGEN_TOKEN`                    | `$DOCGEN_CONFIG_DIR/token.json`       |
| `DOCGEN_TEMPLATES`                | `$DOCGEN_CONFIG_DIR/templates.json`   |
| `DOCGEN_ENV_FILE`                 | `./.env`                              |

Template IDs accept a bare ID **or** a pasted browser URL, everywhere.

---

## Project layout

| File                | Role                                                                              |
| ------------------- | --------------------------------------------------------------------------------- |
| `workspace.py`      | OAuth, template resolution, Slides/Docs rendering, error translation              |
| `server.py`         | MCP server — `generate_slides`, `generate_brochure`, `list_template_placeholders` |
| `auth.py`           | One-time interactive Google authorization                                         |
| `setup_template.py` | Register a template; normalize placeholders; inspect decks                        |
| `smoke_test.py`     | End-to-end test with hardcoded values                                             |
| `.mcp.json`         | Registers the server with Claude Code                                             |
| `.env.example`      | Documents every configuration variable                                            |

Generated at runtime, all gitignored: `credentials.json`, `token.json`, `templates.json`, `.env`.

---

## MCP tools

| Tool                         | Arguments                                                           |
| ---------------------------- | ------------------------------------------------------------------- |
| `generate_slides`            | `agent_name`, `tagline`, `team_names`, `date`, `status`, `industry` |
| `generate_brochure`          | `project_name`, `use_case`, `capabilities`                          |
| `list_template_placeholders` | `kind` — `presentation` or `brochure`                               |

`generate_slides` returns the deck URL plus a per-tag replacement count, so a template
mismatch is visible in the tool result rather than discovered by opening the deck.

The server never starts the interactive OAuth flow — it would hang the tool call. If the
token is missing or expired it returns instructions to run `auth.py`.

---

## Troubleshooting

| Symptom                                                               | Fix                                                                                                |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `Google Drive API is not enabled in project N`                        | Enable Drive, Slides **and** Docs in that project; wait ~2 min                                     |
| `Access blocked: … has not completed the Google verification process` | Add your account under **Test users** in the project that owns your `client_id`                    |
| `Not authorized with Google`                                          | Run `auth.py`                                                                                      |
| `RefreshError`, or auth stops working after ~a week                   | Testing-mode tokens expire in 7 days. Delete `token.json`, re-run `auth.py`                        |
| Tags report `x0`                                                      | The template lacks those tags. `--dump` to see its text, then re-run `setup_template.py`           |
| `does not match the pattern "^[^/]+$"`                                | A URL reached the API raw — fixed; update if you're on older code                                  |
| `No presentation template configured`                                 | Run `setup_template.py --drive "<URL>"`                                                            |
| Slides API 400 on `batchUpdate`                                       | Template is an unconverted `.pptx` in Drive. Re-register with `--drive`; it converts automatically |

Errors route through `workspace.explain()`, which turns Google's `HttpError` into one
actionable sentence — including a direct enable-this-API link where relevant.

