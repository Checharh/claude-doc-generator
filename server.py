# server.py
"""MCP server exposing the documentation generators to Claude Code."""

import json

from mcp.server import MCPServer

import extract
import workspace
from workspace import DocGenError

mcp = MCPServer('DocGenerator')

# The browser-based OAuth flow would hang a tool call, so the server never
# starts it; auth.py handles that once, in a real terminal.
NON_INTERACTIVE = {'interactive': False}


@mcp.tool()
def gather_repo_evidence(repo_path: str = '.') -> str:
    """Collect deterministic evidence about a repository, as JSON.

    Returns candidate values with their sources for AGENT_NAME, TAGLINE,
    TEAM_NAMES and DATE, plus the evidence behind a suggested STATUS and
    INDUSTRY. Call this before generate_slides and interpret the result -- it
    reports what the repository says, it does not decide what belongs on a
    slide.
    """
    try:
        return json.dumps(extract.gather(repo_path), indent=2)
    except OSError as exc:
        return f'Error reading repository: {exc}'


@mcp.tool()
def generate_slides(agent_name: str, tagline: str, team_names: str, date: str,
                    status: str, industry: str, logo_url: str = '',
                    logo_path: str = '', repo_path: str = '') -> str:
    """Generate a Google Slides deck from repository data. Returns the deck URL.

    Call list_template_placeholders first to confirm which tags the current
    template supports.

    For a logo, pass either logo_url (already hosted, used as-is) or logo_path
    (a path inside repo_path, uploaded to Drive and shared first). Google
    fetches images server-side, so a bare local path cannot work. PNG, JPEG and
    GIF only -- SVG is rejected by the API.

    Call list_logo_candidates to see what a repository offers. The template
    needs a shape containing {{LOGO}} for this to land anywhere.
    """
    try:
        result = workspace.generate_google_slides(
            agent_name, tagline, team_names, date, status, industry,
            logo_url=logo_url or None, logo_path=logo_path or None,
            repo_path=repo_path or None, **NON_INTERACTIVE
        )
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error while generating slides: {workspace.explain(exc)}'

    filled = ', '.join(f'{tag}={count}' for tag, count in result['occurrences'].items())
    lines = [f"Slides generated: {result['url']}", f'Replacements: {filled}']
    if result.get('image_occurrences'):
        lines.append('Images: ' + ', '.join(
            f'{tag}={n}' for tag, n in result['image_occurrences'].items()))
    if result.get('logo_upload'):
        up = result['logo_upload']
        lines.append(f"Logo uploaded to Drive as {up['name']} ({up['id']}) and "
                     'shared with anyone who has the link.')
    if result.get('warning'):
        lines.append(f"Warning: {result['warning']}")
    return '\n'.join(lines)


@mcp.tool()
def list_logo_candidates(repo_path: str = '.') -> str:
    """List logo sources a repository offers, best first.

    Reports repository files, README images and public GitHub URLs, each marked
    usable or blocked with the reason. Decides nothing -- pick one and pass it
    to generate_slides.
    """
    import images

    try:
        evidence = extract.gather(repo_path)
    except Exception as exc:
        return f'Error reading repository: {exc}'

    candidates = evidence['candidates'].get('LOGO', [])
    if not candidates:
        return ('No logo candidates found. Set logo_url in .docgen.yml, or pass '
                'an already-hosted PNG/JPEG/GIF URL directly.')

    lines = []
    for c in candidates:
        mark = 'usable ' if c.get('usable') else 'BLOCKED'
        lines.append(f"[{mark}] {c['value']}\n           {c['source']}"
                     + (f"\n           {c['blocked']}" if c.get('blocked') else '')
                     + (f"\n           {c['note']}" if c.get('note') else ''))
    lines.append(f'\nAPI accepts {", ".join(sorted(images.SUPPORTED_SUFFIXES))} '
                 'only, under 50MB and 25 megapixels.')
    return '\n'.join(lines)


@mcp.tool()
def list_brochure_fields() -> str:
    """List the brochure .docx field keys and the placeholder text in each.

    The template marks only a few placeholders with [ brackets ]; most are plain
    guidance prose. The 'field key: <name>' tag under each label is the real
    anchor, and those keys are the catalog spreadsheet's column headers.
    """
    try:
        fields = workspace.brochure_fields()
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error reading brochure template: {workspace.explain(exc)}'
    return '\n'.join(f'{key}: {text[:80]}' for key, text in fields.items())


@mcp.tool()
def generate_brochure_docx(values_json: str, name: str = '',
                           folder_id: str = '') -> str:
    """Fill the brochure .docx from a {field_key: value} JSON map and upload it.

    Call list_brochure_fields first to confirm the keys. Omit a field entirely
    to leave the template's guidance text visible rather than writing a guess --
    a visibly outstanding field is better than a plausible invention, because
    this document becomes a row in a shared catalog.

    Use \\n inside a value for multi-line content; each line becomes its own
    paragraph.
    """
    try:
        values = json.loads(values_json)
    except json.JSONDecodeError as exc:
        return f'values_json is not valid JSON: {exc}'
    if not isinstance(values, dict):
        return 'values_json must be a JSON object of {field_key: value}.'

    try:
        result = workspace.render_brochure_docx(
            values, name=name or None, folder_id=folder_id or None,
            **NON_INTERACTIVE
        )
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error generating brochure: {workspace.explain(exc)}'

    filled = ', '.join(f'{key}={n}' for key, n in result['filled'].items())
    lines = [
        f"Brochure generated: {result['url']}",
        f"Folder: {result['folder']}",
        f"Local copy: {result['local']}",
        f'Filled: {filled}',
    ]
    if result['skipped']:
        lines.append('Left as template guidance text: '
                     + ', '.join(result['skipped']))
    return '\n'.join(lines)


@mcp.tool()
def upload_brochure(docx_path: str, name: str = '', folder_id: str = '') -> str:
    """Upload a filled .docx brochure to Drive. Returns the document URL.

    Goes to DOCGEN_OUTPUT_FOLDER_ID unless folder_id overrides it, and is
    converted to an editable Google Doc on the way in.
    """
    try:
        result = workspace.upload_docx(
            docx_path, name=name or None, folder_id=folder_id or None,
            **NON_INTERACTIVE
        )
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error uploading brochure: {workspace.explain(exc)}'
    return (f"Brochure uploaded: {result['url']}\n"
            f"Name: {result['name']}\nFolder: {result['folder']}")


@mcp.tool()
def generate_brochure(project_name: str, use_case: str, capabilities: str) -> str:
    """Generate a commercial brochure in Google Docs. Returns the document URL.

    The older three-field Docs-template path. Prefer generate_brochure_docx,
    which fills the 29-field catalog .docx.
    """
    try:
        result = workspace.generate_google_doc(
            project_name, use_case, capabilities, **NON_INTERACTIVE
        )
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error generating brochure: {workspace.explain(exc)}'
    return f"Brochure generated: {result['url']}"


@mcp.tool()
def list_template_placeholders(kind: str = 'presentation') -> str:
    """List the {{TAGS}} the current template contains ('presentation' or 'brochure')."""
    try:
        tags = workspace.list_template_placeholders(kind)
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error reading template: {workspace.explain(exc)}'
    return ', '.join(tags) if tags else f'No {{{{TAGS}}}} found in the {kind} template.'


if __name__ == '__main__':
    mcp.run()
