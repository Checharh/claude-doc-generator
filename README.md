# Claude Doc Generator

Turn any local repository into a client-ready Google Slides deck from inside Claude Code.

The tool reads a codebase — README, manifests, git history, dependencies — works out what
the project _is_, and fills a Google Slides master template with the result. A local MCP
server does the Google Workspace work; Claude Code does the understanding.

```
repository  →  Claude Code  →  MCP server  →  Google Slides
               (extraction)    (rendering)     (your deck)
```

**Status:** working end to end — `/generate-slides` reads a repository and builds a deck.
Packaging it for use in any repo is the remaining work.

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

The master template uses `{{TAG}}` placeholders. Seven are supported:

| Tag              | Content                                                     |
| ---------------- | ----------------------------------------------------------- |
| `{{AGENT_NAME}}` | Project or agent name                                       |
| `{{TAGLINE}}`    | One line, benefit-oriented, ≤ 12 words                      |
| `{{TEAM_NAMES}}` | Comma-separated, no emails                                  |
| `{{DATE}}`       | Presentation date                                           |
| `{{STATUS}}`     | `Concept` \| `Prototype` \| `MVP` \| `Beta` \| `Production` |
| `{{INDUSTRY}}`   | Fixed taxonomy (Fintech, Developer Tools, …)                |
| `{{LOGO}}`       | Image, not text — see [The logo image](#the-logo-image)     |

`setup_template.py` normalizes other conventions (`[AGENT_NAME]`, `[ Agent / Project Name ]`)
into this form, so a hand-made deck can be adopted without retyping it.

A tag with **no slot in the template** is reported rather than silently dropped, and any
`{{TAG}}` left unfilled is blanked before the deck is returned — a deck must never reach a
client with `{{TAGLINE}}` printed on it.

### The logo image

`{{LOGO}}` is the odd one out: it is swapped for a picture, not text. Google fetches images
**server-side, by URL**, and that single fact drives every rule below.

**Format** — PNG, JPEG or GIF. Nothing else is accepted; `.svg`, `.webp`, `.ico`, `.avif`,
`.bmp` and `.tiff` are refused *before* any API call, with the reason. SVG matters most
here, because it is what most projects actually ship — export a PNG instead.

**Limits** — under 50 MB, at most 25 megapixels, and if you pass a URL it must be under
2 kB long and publicly reachable without login.

**Shape — read this before designing the template.** The `{{LOGO}}` shape in the template
is only a *position and size marker*. `replaceAllShapesWithImage` **deletes** that shape and
drops a rectangular image into its bounding box; the geometry is discarded. Draw a circle,
a star or a hexagon and you still get a rectangle. `imageReplaceMethod` does not change
this — `CENTER_INSIDE` fits the whole image inside the box, `CENTER_CROP` fills it and cuts
the overflow, and both cuts are rectangular. The Slides API has no shape-masking request at
all; "Mask image" is a UI-only feature.

So to get a non-rectangular logo, **bake the silhouette into the image's alpha channel**:
save a PNG with transparency outside the shape you want. The image element stays a
rectangle, but the transparent pixels make it read as a circle — or a star — on any
background. A logo saved as opaque RGB on a white square will appear as a white square.

**Framing** — crop tight to the artwork. `CENTER_INSIDE` fits the *entire* image into the
shape, so built-in margin is dead space that shrinks the visible logo. A square-ish, tightly
cropped image fills the slot best.

**Where it comes from**, in the order the picker prefers:

| Source | Upload? | Notes |
| --- | --- | --- |
| `logo_url:` in `.docgen.yml` | No | Authoritative — nothing else is consulted |
| A file in the repository | **Yes** | Uploaded to Drive and shared with *anyone who has the link* |
| A README image | No | Only absolute URLs; badges are excluded |
| `opengraph.githubassets.com/1/<owner>/<repo>` | No | The repo's social card; always renders |
| `github.com/<owner>.png` | No | The owner or org avatar |

Repository files are found by name, in the top level of the repo root, `.github/`, `docs/`,
`doc/`, `assets/`, `public/`, `static/`, `src/assets/`, `images/`, `img/`, `media/` or
`resources/` — and the filename must contain `logo`, `icon`, `banner`, `hero`, `brand`,
`wordmark` or similar. Subdirectories of those are not scanned.

Prefer a hosted URL over a repository file. Uploading sets Drive permission
`{'role': 'reader', 'type': 'anyone'}`, which is a real disclosure — it has to be, because
Google's fetcher is anonymous — and every run uploads another copy. A public repo's own
`raw.githubusercontent.com` URL avoids both, and pinning it in `.docgen.yml` means the
picker never looks anywhere else.

If no usable logo is found the deck is generated without one and `{{LOGO}}` is swept blank.
A report of `{{LOGO}}=0` means the opposite problem — the template has no shape containing
the tag, so add one rather than retrying.

See [`IMAGES.md`](IMAGES.md) for the full walkthrough, including adding the placeholder
shape and wiring up more than one image.

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

### If the template changes

Whether you need to touch anything depends on *how* it changed:

**Edited in place** — moving shapes, restyling, adding a `{{LOGO}}` box, fixing a typo on
the master. The Drive file ID does not change, so there is nothing to update. Every run
copies the master fresh, so the next deck picks the edits up automatically.

**Replaced by a different file** — uploading a new `.pptx`, *File → Make a copy*, building a
new deck from scratch, or switching to someone else's. This creates a **new file with a new
ID**, and the configured ID must be updated or you will keep generating from the old one:

```bash
./venv/bin/python setup_template.py --drive "<new URL or ID>"   # writes templates.json
```

**If you also set `DOCGEN_PRESENTATION_TEMPLATE_ID` in `.env`, update it there too.** Config
resolves `process env > .env > templates.json`, so a stale ID in `.env` silently wins over
whatever `setup_template.py` just registered — the most confusing way to get this wrong.
The same applies to `DOCGEN_BROCHURE_TEMPLATE_ID`.

Nothing errors when the ID is stale. The deck generates normally, just from the wrong
master — so confirm which tags the configured template actually exposes:

```bash
./venv/bin/python setup_template.py --drive "<ID>" --dump | grep LOGO
```

or call `list_template_placeholders` from Claude Code. A tag reported as `x0` after a
template swap usually means the new master is missing a slot the old one had.

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

## Usage

From inside a repository:

```
/generate-slides            # extract, confirm, generate
/generate-slides --yes      # no questions; uses documented fallbacks
```

Claude gathers evidence, shows what it found with a source and confidence per field, and
generates the deck once you confirm:

```
  AGENT_NAME   Claude Doc Generator     <- README.md H1                [high]
  TAGLINE      Turns any repository into a client-ready deck
                                        <- README.md first paragraph   [medium]
  TEAM_NAMES   Cesar Hinojosa           <- git shortlog                [high]
  DATE         17 August 2026           <- system clock                [high]
  STATUS       Prototype                <- 2 commits, no CI            [high]
  INDUSTRY     Developer Tools          <- deps: mcp                   [high]
```

Low-confidence fields become multiple-choice questions with a recommendation, batched into
a single round. Afterwards you can save the confirmed values so later runs ask nothing.

### Pinning values per repo

Copy `.docgen.yml.example` to `.docgen.yml` in any repository and set what you want fixed.
These values are authoritative — extraction does not second-guess or re-ask them.

```yaml
agent_name: Atlas Router
tagline: Routes support tickets to the right team in under a second
status: Beta
industry: Enterprise SaaS
```

### Inspecting extraction on its own

`extract.py` is a plain function of the filesystem — no model involved — so you can see
exactly what the agent will be handed:

```bash
./venv/bin/python extract.py /path/to/repo
```

It reports *candidates with sources*, never a decision. Which of three names is the product
name needs reading comprehension; what `package.json` says does not.

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
| `extract.py`        | Deterministic repository evidence — manifests, git, dependency fingerprints       |
| `workspace.py`      | OAuth, template resolution, Slides/Docs rendering, error translation              |
| `server.py`         | MCP server — `generate_slides`, `generate_brochure`, `list_template_placeholders` |
| `auth.py`           | One-time interactive Google authorization                                         |
| `setup_template.py` | Register a template; normalize placeholders; inspect decks                        |
| `smoke_test.py`     | End-to-end test with hardcoded values                                             |
| `.mcp.json`         | Registers the server with Claude Code                                             |
| `.env.example`      | Documents every configuration variable                                            |
| `.docgen.yml.example` | Per-repo value overrides, meant to be committed in target repos                 |
| `.claude/commands/generate-slides.md` | The `/generate-slides` command and its extraction rulebook      |

Generated at runtime, all gitignored: `credentials.json`, `token.json`, `templates.json`, `.env`.

---

## MCP tools

| Tool                         | Arguments                                                           |
| ---------------------------- | ------------------------------------------------------------------- |
| `gather_repo_evidence`       | `repo_path` — returns candidate values and their sources as JSON    |
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

