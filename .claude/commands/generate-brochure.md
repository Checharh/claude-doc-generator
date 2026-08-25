---
description: Generate an Agentic AI catalog brochure (.docx to Google Doc)
argument-hint: "[--yes] [path]"
allowed-tools: Read, Grep, Glob, Bash(git shortlog:*), Bash(git log:*), Bash(date:*), Write, AskUserQuestion, mcp__doc-generator__gather_repo_evidence, mcp__doc-generator__list_brochure_fields, mcp__doc-generator__generate_brochure_docx
---

Generate an Agentic AI catalog brochure (.docx → Google Doc) for the repository
at `$1` (default: the current directory).
If `--yes` was passed, run without asking anything.

This is the brochure counterpart to `/generate-slides`. Same split: you decide
what each field says and show your sources; the MCP server fills the template
and uploads it deterministically.

## Step 1 — Gather evidence

Call `mcp__doc-generator__gather_repo_evidence` with the **absolute path** to the
repository root. The server runs in its own working directory, so a relative path
can resolve somewhere unexpected.

It answers six fields directly (`AGENT_NAME`, `TAGLINE`, `TEAM_NAMES`, `DATE`,
`STATUS`, `INDUSTRY`). The brochure needs far more than that, so read the
repository yourself to fill the rest — README, entrypoints, dependency manifests,
config, `.gitignore`. Budget roughly 15 files.

## Step 2 — Learn the template

Call `mcp__doc-generator__list_brochure_fields`. It returns every field key and
the placeholder text currently sitting in that field.

Do not assume the fields are marked with `[brackets]` — only a few are. The
`field key:` tag under each label is the real anchor, and those keys are the
catalog spreadsheet's column headers.

## Step 3 — Build the manifest

Produce this in your reply, before generating:

```json
{
  "agent_name": { "value": "...", "source": "README.md H1", "confidence": "high" }
}
```

One entry per field you intend to fill. Confidence:

- `high` — a `.docgen.yml` override, or an exact manifest/git field
- `medium` — README prose, or an inference from code you read
- `low` — nothing usable; you are guessing

**Omit any field you cannot source.** A field left out keeps the template's own
guidance text, which reads as visibly outstanding. That is the correct outcome —
it is strictly better than a plausible invention, because this document becomes a
row in a shared catalog and will outlive the conversation that produced it.

This applies hardest to `operational_impact`, `value_summary` and `kpis_focus`.
They ask for before/after metrics, percentage gains and KPI targets. Unless the
repository contains actual measurements — a benchmark suite, a results file,
numbers in the README — omit all three and say so.

## Step 4 — Confirm

**If `--yes`:** generate, then state plainly which fields you filled, which you
omitted, and why.

**Otherwise:** print the manifest as a compact table — field, value, source,
confidence — and ask for one confirmation. Batch every question into that single
round. For any `low` field use `AskUserQuestion` with concrete options and your
recommendation first.

## Step 5 — Generate

Call `mcp__doc-generator__generate_brochure_docx` with `values_json` as a JSON
object of `{field_key: value}`. Use `\n` inside a value for multi-line content;
each line becomes its own paragraph.

It reports which fields were filled and which were skipped. Return the URL, and
name the skipped fields explicitly so nobody assumes the document is complete.

On an error, surface it verbatim. Do not retry with altered arguments.

## Step 6 — Persist

Offer to write `.docgen.yml` with the confirmed shared values, so the next run
asks less. Do not write it without asking, and never overwrite an existing one
silently.

---

# Field rulebook

`category` must be exactly one of: `Analytics`, `Operations`, `Engineering`,
`QA`, `Compliance`, `Logistics`, `Finance`, `Customer Support`, `Other`.

`industry` uses the same taxonomy as the deck: `Fintech`, `Healthtech`,
`Developer Tools`, `E-Commerce`, `Logistics`, `Marketing & Sales`,
`Data & Analytics`, `Enterprise SaaS`, `Education`, `Media`, `Gaming`,
`Real Estate`, `Legal`, `HR`, `Security`, `General Technology`.

`date_prepared` is `DD/MM/YYYY`. Take the date from the evidence — it comes from
the system clock. Never use your own belief about today's date.

`capabilities` follows one grammar per line: `Title: one-line explanation`.
Three to six lines.

`assumptions_limitations` and `security_notes` should be honest about gaps you
actually found in the code — missing tests, unparsed manifest formats, scope
limits. A reviewer trusts a document that names its own edges.

## Hard constraints

These documents go into a shared catalog. Violating any of these is worse than
producing nothing.

- **Never invent** metrics, percentages, customer names, funding, headcount,
  awards or dates. If the repo does not say it, it does not go in the document.
- **Never put an email address** in `prepared_by`.
- **Never claim maturity above the evidence.** When torn, pick the lower.
- **Always cite a source** for every field. "It seemed right" is not a source.
