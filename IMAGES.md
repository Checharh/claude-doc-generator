# Adding images to the templates

How to get a repository logo — or any image — into a generated deck.

---

## The one constraint everything follows from

**Google fetches images server-side, by URL.** The Slides and Docs APIs never receive
image bytes from us; they receive a link and go get it themselves.

Two consequences, both verified against the API discovery documents:

| | Slides | Docs |
| --- | --- | --- |
| Request | `replaceAllShapesWithImage` | `replaceImage` / `insertInlineImage` |
| Formats | **PNG, JPEG, GIF only** | same |
| URL | **publicly accessible**, ≤ 2 kB long | same |
| Size | < 50 MB, ≤ 25 megapixels | same |

1. **A local file path can never work.** It has to be hosted somewhere Google can reach
   anonymously.
2. **SVG is rejected.** This matters more than it sounds — most repositories ship their
   logo as SVG. `favicon.svg` and `logo.svg` are both dead ends without a conversion step.

---

## Step 1 — Add a placeholder shape to the template *(do this once, by hand)*

This is the part no code can do for you, because the template lives in Google Slides.

`replaceAllShapesWithImage` swaps **a shape** for an image, and the image inherits that
shape's position and size. So the template needs a real box, not just the text `{{LOGO}}`
typed into an existing body placeholder.

In Google Slides, on the master template:

1. **Insert → Shape → Rectangle**, and draw it where the logo belongs.
2. Size it to the aspect ratio you want. The image is fitted *inside* this box, so the box
   is the frame — a square box gives a square area, a wide box gives a banner.
3. With the shape selected, **type `{{LOGO}}`** directly into it.
4. Optional, and recommended: set the shape's fill to none and its border to none, so if
   the swap ever fails the leftover is invisible rather than an ugly grey rectangle.
5. Save. The template ID does not change, so nothing needs re-registering.

Verify the template sees it:

```bash
./venv/bin/python setup_template.py --drive "<TEMPLATE_ID>" --dump | grep LOGO
```

> **Why a shape and not text?** Text in an existing placeholder has no box of its own for
> the image to inherit, so the API has nothing to size or position the image against. Draw
> a shape.

---

## Step 2 — Pick a logo source

```bash
./venv/bin/python -c "
import extract, json
ev = extract.gather('.')
print(json.dumps(ev['candidates']['LOGO'], indent=2))"
```

Or from Claude Code, the `list_logo_candidates` tool. Real output for
`Checharh/ai-multi-assistants`:

```
[usable ] src/assets/hero.png
[BLOCKED] public/icons.svg     .svg is not supported by the Slides/Docs API
[BLOCKED] public/favicon.svg   .svg is not supported by the Slides/Docs API
[usable ] https://opengraph.githubassets.com/1/Checharh/ai-multi-assistants
[usable ] https://github.com/Checharh.png
```

Sources, in the order they are reported:

| Source | Needs an upload? | Notes |
| --- | --- | --- |
| `logo_url:` in `.docgen.yml` | No | Authoritative. Set it and nothing else is consulted |
| A file in the repo | **Yes** | `logo.*`, `icon.*`, `banner.*` etc. under the root, `.github/`, `docs/`, `assets/`, `public/`, … |
| A README image | Only if relative | Badges are excluded — a shields.io SVG is never a logo |
| `opengraph.githubassets.com/1/<owner>/<repo>` | No | The repo's social card. Always renders, even with no custom image set |
| `github.com/<owner>.png` | No | The owner or org avatar |

**The two GitHub URLs are the path of least resistance.** They are already public, already
PNG/JPEG, need no upload, no Drive permissions and no conversion. If you just want *a*
logo on the deck today, use one of those.

---

## Step 3 — Generate

Already-hosted URL — nothing else required:

```python
workspace.generate_google_slides(
    'Atlas Router', 'Routes tickets fast', 'Cesar Hinojosa',
    '24 August 2026', 'Beta', 'Developer Tools',
    logo_url='https://github.com/Checharh.png',
)
```

A file inside the repo — uploaded to Drive and shared automatically:

```python
workspace.generate_google_slides(
    ..., logo_path='src/assets/hero.png', repo_path='/path/to/repo',
)
```

From Claude Code:

```
/generate-slides
```

then pass `logo_url` or `logo_path` to the `generate_slides` tool.

### ⚠️ What uploading actually does

Uploading a local file sets the Drive permission `{'role': 'reader', 'type': 'anyone'}` on
it. **That makes the image readable by anyone who has the link** — it has to be, because
Google's image fetcher is anonymous. That is a real disclosure. If the logo is
confidential, host it somewhere you control and pass `logo_url` instead.

The tool reports the upload explicitly rather than doing it quietly:

```
Logo uploaded to Drive as hero.png (1AbC...) and shared with anyone who has the link.
```

---

## The SVG problem

Most repo logos are SVG, and the API rejects them. Three ways out, best first:

1. **Use a GitHub URL instead.** Zero work, already PNG/JPEG.
2. **Convert once and host it.** Export a PNG at 2× the display size, put it anywhere
   public, and pin it:

   ```yaml
   # .docgen.yml
   logo_url: https://raw.githubusercontent.com/acme/widget/main/docs/logo.png
   ```

   A `raw.githubusercontent.com` URL from a public repo is publicly fetchable and stable —
   no Drive upload needed.
3. **Commit a PNG alongside the SVG.** `docs/logo.png` gets found automatically and ranks
   above the SVG.

Conversion is deliberately not automated. It needs a native rendering library
(`cairosvg` and friends), which is a heavy dependency for something that is a one-time
manual export.

---

## What happens when it does not work

Failures are reported, never silent:

| Situation | Result |
| --- | --- |
| Template has no `{{LOGO}}` shape | `image_occurrences: {'{{LOGO}}': 0}` plus a warning naming the tag |
| No logo passed at all | `{{LOGO}}` is swept blank like any other unfilled tag — never shipped visibly |
| SVG passed | Refused **before** any API call, with the reason and the fix |
| File over 50 MB | Refused before upload |
| URL over 2 kB | Refused before the request |

The orphan sweep runs *after* the image swap, deliberately. Reverse the order and the
sweep blanks the `{{LOGO}}` text inside the shape before `replaceAllShapesWithImage` can
match on it — the image then silently never lands. There is a test pinning this
(`TestRenderOrdering`).

---

## More than one image

Nothing is logo-specific in the plumbing. `render_presentation` takes a map:

```python
workspace.render_presentation(
    values,
    images={
        '{{LOGO}}': 'https://github.com/acme.png',
        '{{ARCHITECTURE_DIAGRAM}}': 'https://example.com/arch.png',
        '{{SCREENSHOT}}': 'https://example.com/demo.png',
    },
)
```

Each needs its own shape in the template, drawn at the size you want. `CENTER_INSIDE`
(the default) fits the whole image inside the shape and preserves aspect ratio;
`CENTER_CROP` fills the shape and crops the overflow. A logo must never be cropped, which
is why `CENTER_INSIDE` is the default — override per call with `image_fit`.

---

## The brochure

The Docs brochure supports `replaceImage` (swap an existing placeholder image) and
`insertInlineImage` (insert at a document index), with the same format and public-URL
rules. **Neither is wired up yet** — `generate_google_doc` is text-only today.

The `architecture_diagram` field currently says *"Insert an image of the architecture
diagram here, or add a link/filename if it's stored elsewhere"*, so a filename or link is
the expected answer for now.

Note also that the `.docx` brochure path lives on branch `DOC-02`, not here. When those
branches meet, image support will need integrating with the spec and confidence-gate
system — an image field should carry a source like every other field.

---

## Testing

```bash
./venv/bin/python -m pytest test_images.py -q      # 47 passed, no network
```

Covers format classification, GitHub remote parsing in all four forms, logo file ranking,
README badge exclusion, the upload permission body, and request ordering inside the batch.

The end-to-end check needs a real template with a `{{LOGO}}` shape, and Google credentials:

```bash
./venv/bin/python -c "
import workspace
r = workspace.generate_google_slides(
    'Test', 'tagline', 'You', '24 August 2026', 'Prototype', 'Developer Tools',
    logo_url='https://github.com/Checharh.png')
print(r['url']); print('image swaps:', r['image_occurrences'])"
```

`image swaps: {'{{LOGO}}': 1}` means it landed. `0` means the template has no matching
shape — go back to Step 1.
