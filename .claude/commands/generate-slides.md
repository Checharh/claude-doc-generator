---
description: Generate a Google Slides deck from this repository
argument-hint: "[--yes] [path]"
allowed-tools: Read, Grep, Glob, Bash(git shortlog:*), Bash(git log:*), Bash(date:*), Write, AskUserQuestion, mcp__doc-generator__gather_repo_evidence, mcp__doc-generator__list_template_placeholders, mcp__doc-generator__generate_slides
---

Generate a presentation for the repository at `$1` (default: the current directory).
If `--yes` was passed, run without asking anything.

## Step 1 — Gather evidence

Call `mcp__doc-generator__gather_repo_evidence` with the **absolute path** to the
repository root. The server runs in its own working directory, so a relative path
can resolve somewhere unexpected.

It returns candidate values with their sources, plus evidence for STATUS and
INDUSTRY. **It reports; you decide.** Read files yourself only to resolve a gap it
left — a monorepo with several `package.json` files, an empty README, a name that
looks like scaffolding. Do not re-derive what it already answered, and do not read
more than ~10 extra files.

## Step 2 — Build the extraction manifest

Apply the rulebook below and produce this, in your reply, before calling any other tool:

```json
{
  "AGENT_NAME": { "value": "...", "source": "README.md H1", "confidence": "high" }
}
```

One entry per field: `AGENT_NAME`, `TAGLINE`, `TEAM_NAMES`, `DATE`, `STATUS`, `INDUSTRY`.

Confidence:

- `high` — a `.docgen.yml` override, or an exact manifest/git field
- `medium` — README prose, or an inference from dependencies
- `low` — nothing usable; you are guessing

## Step 3 — Confirm

**If `--yes`:** skip to Step 4. Use the fallbacks in the rulebook for anything
missing, and afterwards state plainly which fields were defaulted.

**Otherwise:** print the manifest as a compact table — field, value, source,
confidence — and ask for one confirmation.

Batch every question into that single round. If any field is `low`, use
`AskUserQuestion` with concrete options and your recommendation first; never ask an
open-ended question like "what industry is this?" when you can offer four choices.

## Step 4 — Generate

Call `mcp__doc-generator__list_template_placeholders` to see which tags the template
supports, then `mcp__doc-generator__generate_slides` with the confirmed values.

Return the deck URL. The tool reports a replacement count per tag:

- `x0` means the template has no slot for that tag — say so; the value was dropped
- On an error, surface it verbatim. Do not retry with altered arguments.

## Step 5 — Persist

Offer to write `.docgen.yml` with the confirmed values, so the next run asks nothing.
Do not write it without asking, and never overwrite an existing one silently.

```yaml
agent_name: Atlas Router
tagline: Routes support tickets to the right team in under a second
team_names: Cesar Hinojosa, Ana Ruiz
status: Beta
industry: Enterprise SaaS
```

---

# Rulebook

## Source precedence

Walk the tiers in order; stop at the first usable hit.

1. **`.docgen.yml`** — authoritative. Use verbatim, confidence `high`. Never second-guess it.
2. **Structured metadata** — manifest fields, README H1, git shortlog.
3. **Inference** — dependency fingerprints, entrypoints, directory naming.

## Per field

**`AGENT_NAME`** — the product's name, as a human would say it.
Prefer the README H1 over a package name; prefer either over the directory name.
Title Case. Strip `-mcp`, `-api`, `-app`, `my-`, and any `@scope/`. Hyphens and
underscores become spaces.
If the only candidate is flagged `scaffold` (`app`, `test`, `untitled`, …), treat it
as `low` and ask.

**`TAGLINE`** — one line on what it does for whom.
≤ 12 words. No trailing period. Benefit-oriented, not architectural: "Turns any repo
into a client-ready deck" beats "Python MCP server using the Slides API".
Trim a long README sentence down rather than inventing a new claim.

**`TEAM_NAMES`** — who to credit.
`git shortlog` is the best source; the evidence already excludes bots. Take the top
5 by commits. Format `First Last, First Last` and **Title Case them** — git names are
often lowercase. Never include an email address.

**`DATE`** — use the value from the evidence. It comes from the system clock.
Never use your own belief about today's date; you will get it wrong.

**`STATUS`** — one of exactly: `Concept`, `Prototype`, `MVP`, `Beta`, `Production`.
The evidence includes a `suggested` value computed from version, CI, deploy config,
tests and commit count. Take it unless you have a specific reason to differ, and say
so if you do. **When torn, pick the lower one** — a deck that overstates maturity to
a client is worse than one that understates it.

**`INDUSTRY`** — one of exactly:
`Fintech`, `Healthtech`, `Developer Tools`, `E-Commerce`, `Logistics`,
`Marketing & Sales`, `Data & Analytics`, `Enterprise SaaS`, `Education`, `Media`,
`Gaming`, `Real Estate`, `Legal`, `HR`, `Security`, `General Technology`.
The evidence gives a `suggested` value and the dependency hits behind it. If nothing
matched, `General Technology` is correct — it is honest and never wrong. Do not
invent a category outside this list.

## Fallbacks for `--yes`

| Field | Fallback |
|---|---|
| `AGENT_NAME` | Title-cased directory name |
| `TAGLINE` | `A {INDUSTRY} project` |
| `TEAM_NAMES` | Top git authors, else `Internal Team` |
| `STATUS` | `Prototype` |
| `INDUSTRY` | `General Technology` |

## Hard constraints

These decks go to clients. Violating any of these is worse than producing nothing.

- **Never invent** metrics, percentages, customer names, funding, headcount, awards
  or dates. If the repo does not say it, it does not go on a slide.
- **Never put an email address** in `TEAM_NAMES`.
- **Never pass an empty string.** Use the documented fallback.
- **Never claim maturity above the evidence.**
- **Never leave a `{{TAG}}` visible.** The server blanks unfilled tags, but do not
  rely on it.
- **Always cite a source** for every field. "It seemed right" is not a source.
