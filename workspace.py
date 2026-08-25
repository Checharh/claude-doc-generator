# workspace.py
"""Google Workspace integration for the documentation generator.

Auth is per-user OAuth (installed-app flow), so generated files land in the
user's own Drive with normal ownership and sharing.

Paths are resolved absolutely: the MCP server is launched with the *target*
repository as its working directory, so anything relative would break.
"""

import json
import os
import re
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive',
]

_HERE = Path(__file__).resolve().parent


def _load_dotenv(path=None):
    """Read KEY=VALUE lines from .env into the environment.

    Real environment variables always win, so production can override the file
    without editing it. Kept dependency-free on purpose.
    """
    env_file = Path(path or os.environ.get('DOCGEN_ENV_FILE', _HERE / '.env'))
    if not env_file.exists():
        return {}
    loaded = {}
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


_load_dotenv()

# Secrets live next to the code so they stay visible and easy to swap. This is
# still safe from the cwd problem because _HERE is absolute; .gitignore keeps
# credentials.json, token.json and templates.json out of version control.
CONFIG_DIR = Path(os.environ.get('DOCGEN_CONFIG_DIR', _HERE))
CREDENTIALS_PATH = Path(os.environ.get('DOCGEN_CREDENTIALS', CONFIG_DIR / 'credentials.json'))
TOKEN_PATH = Path(os.environ.get('DOCGEN_TOKEN', CONFIG_DIR / 'token.json'))
TEMPLATES_PATH = Path(os.environ.get('DOCGEN_TEMPLATES', CONFIG_DIR / 'templates.json'))

# The agent-catalog brochure is a local .docx filled on disk, not a Drive master
# copied in place -- its fields carry no {{TAGS}} to replace remotely. They are
# marked by a gray "field key: <name>" tag under each label instead.
BROCHURE_DOCX_PATH = Path(os.environ.get(
    'DOCGEN_BROCHURE_DOCX', CONFIG_DIR / 'Agentic_AI_Brochure_Template.docx'))

# Brochure lives in Docs and predates this rewrite; keep the known-good default.
DEFAULT_BROCHURE_TEMPLATE_ID = '1spO_SmbQDeJlWfI_jZztgfvA5KAGOpWGU5QnaeSPSlg'

PLACEHOLDER_RE = re.compile(r'\{\{[A-Z_]+\}\}')

# A slide that reaches a client showing "{{TAGLINE}}" is the one unrecoverable
# failure mode of this tool, so every value passes through a fallback.
FALLBACKS = {
    '{{AGENT_NAME}}': 'Untitled Project',
    '{{TAGLINE}}': '',
    '{{TEAM_NAMES}}': 'Internal Team',
    '{{DATE}}': '',
    '{{STATUS}}': 'Prototype',
    '{{INDUSTRY}}': 'General Technology',
    '{{PROJECT_NAME}}': 'Untitled Project',
    '{{USE_CASE}}': '',
    '{{CAPABILITIES}}': '',
}


class DocGenError(Exception):
    """Actionable configuration or API failure."""


API_NAMES = {
    'drive': 'Google Drive API',
    'slides': 'Google Slides API',
    'docs': 'Google Docs API',
}


def explain(exc):
    """Turn a Google HttpError into something a human can act on.

    The raw tracebacks bury the one useful sentence under a wall of URL-encoded
    request detail, and the most common failure -- an API not enabled in the
    project -- is entirely self-inflicted and one click to fix.
    """
    if isinstance(exc, DocGenError):
        return str(exc)

    content = getattr(exc, 'content', b'')
    try:
        detail = json.loads(content.decode())['error']
    except Exception:
        return f'{type(exc).__name__}: {exc}'

    message = detail.get('message', '')
    reasons = {d.get('reason') for d in detail.get('errors', [])}

    if 'accessNotConfigured' in reasons:
        match = re.search(r'project (\d+)', message)
        project = match.group(1) if match else '<your-project>'
        api = next((n for k, n in API_NAMES.items() if k in message.lower()), 'the API')
        return (
            f'{api} is not enabled in project {project}.\n\n'
            f'  Enable it: https://console.cloud.google.com/apis/library/'
            f'{api.split()[1].lower()}.googleapis.com?project={project}\n\n'
            'Then wait ~2 minutes for it to propagate and retry. All three of Drive, '
            'Slides and Docs must be enabled in the same project.'
        )

    if detail.get('code') == 404:
        return (f'File not found, or your account cannot see it: {message}\n'
                'Check the ID, and that the file is owned by or shared with you.')

    if detail.get('code') == 403:
        return f'Permission denied: {message}'

    return f"{detail.get('status', detail.get('code', 'error'))}: {message}"


# --------------------------------------------------------------------------- auth


def _find_client_secrets():
    """Configured location first, then the repo copy, so the move is non-breaking."""
    for candidate in (CREDENTIALS_PATH, _HERE / 'credentials.json'):
        if candidate.exists():
            return candidate
    raise DocGenError(
        f'No OAuth client secrets found. Expected {CREDENTIALS_PATH}.\n'
        'Download the "OAuth client ID -> Desktop app" JSON from Google Cloud Console '
        'and save it there.'
    )


def _save_token(creds):
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    TOKEN_PATH.chmod(0o600)


def get_credentials(interactive=True):
    """Return usable credentials, refreshing or running the OAuth flow as needed.

    Pass interactive=False from the MCP server: it cannot usefully open a browser
    mid-request, and run_local_server() would hang the tool call.
    """
    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except ValueError as exc:
            raise DocGenError(f'{TOKEN_PATH} is corrupt ({exc}). Delete it and re-authorize.')

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except RefreshError:
            creds = None  # revoked or scope change; fall through to a fresh grant

    if not interactive:
        raise DocGenError(
            'Not authorized with Google. Run this once in a terminal:\n'
            f'  {_HERE / "venv" / "bin" / "python"} {_HERE / "auth.py"}'
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(_find_client_secrets()), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def _services(interactive=True):
    creds = get_credentials(interactive=interactive)
    return (
        build('drive', 'v3', credentials=creds),
        build('slides', 'v1', credentials=creds),
        build('docs', 'v1', credentials=creds),
    )


# ---------------------------------------------------------------------- templates


# /d/<id> covers Docs, Slides and Sheets; /folders/<id> covers a Drive folder,
# which is the URL you get from the address bar when picking an output folder.
FILE_ID_RE = re.compile(r'/(?:d|folders)/([a-zA-Z0-9_-]{15,})')


def parse_file_id(value):
    """Accept a bare file ID or any Google Docs/Slides/Drive URL.

    Pasting the browser URL is the obvious thing to do, so every place that
    takes an ID accepts one -- .env, templates.json and the CLI alike.
    """
    value = (value or '').strip()
    match = FILE_ID_RE.search(value)
    if match:
        return match.group(1)
    cleaned = value.strip('/')
    if re.fullmatch(r'[a-zA-Z0-9_-]{15,}', cleaned):
        return cleaned
    raise DocGenError(
        f'Could not read a file ID from {value!r}. Use either the ID or the full '
        'URL, e.g. https://docs.google.com/presentation/d/<ID>/edit'
    )


def load_templates():
    if TEMPLATES_PATH.exists():
        data = json.loads(TEMPLATES_PATH.read_text())
    else:
        data = {}
    data.setdefault('brochure', DEFAULT_BROCHURE_TEMPLATE_ID)
    if os.environ.get('DOCGEN_PRESENTATION_TEMPLATE_ID'):
        data['presentation'] = os.environ['DOCGEN_PRESENTATION_TEMPLATE_ID']
    if os.environ.get('DOCGEN_BROCHURE_TEMPLATE_ID'):
        data['brochure'] = os.environ['DOCGEN_BROCHURE_TEMPLATE_ID']
    return {kind: parse_file_id(value) for kind, value in data.items() if value}


def save_templates(data):
    TEMPLATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATES_PATH.write_text(json.dumps(data, indent=2) + '\n')


def _require_template(kind):
    templates = load_templates()
    template_id = templates.get(kind)
    if not template_id:
        raise DocGenError(
            f'No {kind} template configured. Run setup_template.py to upload and '
            f'register one (writes {TEMPLATES_PATH}).'
        )
    return template_id


def presentation_text(presentation_id, slides_service=None):
    """Every text string in a presentation, in slide order. Used to discover tags."""
    if slides_service is None:
        _, slides_service, _ = _services()
    deck = slides_service.presentations().get(presentationId=presentation_id).execute()
    out = []
    for slide in deck.get('slides', []):
        for element in slide.get('pageElements', []):
            for para in element.get('shape', {}).get('text', {}).get('textElements', []):
                run = para.get('textRun')
                if run and run.get('content', '').strip():
                    out.append(run['content'].strip())
    return out


def list_template_placeholders(kind='presentation'):
    """The {{TAGS}} a template actually contains.

    Call this before generating so the agent maps to the template that exists
    rather than to a hardcoded list that has drifted.
    """
    template_id = _require_template(kind)
    if kind == 'presentation':
        _, slides_service, _ = _services()
        haystack = ' '.join(presentation_text(template_id, slides_service))
    else:
        _, _, docs_service = _services()
        doc = docs_service.documents().get(documentId=template_id).execute()
        haystack = json.dumps(doc)
    return sorted(set(PLACEHOLDER_RE.findall(haystack)))


# ------------------------------------------------------------------- rendering


def _sanitize(value, tag):
    text = (value or '').strip()
    if not text or text.startswith('{{'):
        return FALLBACKS.get(tag, '')
    return text


def _replace_requests(values):
    return [
        {
            'replaceAllText': {
                'containsText': {'text': tag, 'matchCase': True},
                'replaceText': _sanitize(value, tag),
            }
        }
        for tag, value in values.items()
    ]


def _occurrences(response, tags):
    """Map each tag to how many placeholders it actually replaced.

    batchUpdate replies are positional and mirror the request order, so a tag
    that matched nothing shows up as 0 rather than as an error.
    """
    replies = response.get('replies', [])
    counts = {}
    for tag, reply in zip(tags, replies):
        counts[tag] = (reply or {}).get('replaceAllText', {}).get('occurrencesChanged', 0)
    return counts


def output_folder(folder_id=None):
    """Drive folder for generated files: explicit argument, then .env, then root.

    Accepts a folder URL as well as a bare ID, because that is what you get
    from the address bar.
    """
    chosen = folder_id or os.environ.get('DOCGEN_OUTPUT_FOLDER_ID', '')
    return parse_file_id(chosen.strip()) if chosen.strip() else None


def _image_requests(images, fit='CENTER_INSIDE'):
    """Swap each placeholder shape for an image.

    replaceAllShapesWithImage takes over the shape the designer drew, so the
    image inherits its position and size. That is why the template needs a real
    shape holding {{LOGO}} rather than just the text typed into a slide -- text
    in a body placeholder has no box of its own to inherit.

    CENTER_INSIDE fits the whole image inside the shape and keeps its aspect
    ratio; CENTER_CROP fills the shape and crops the overflow. A logo must
    never be cropped, so CENTER_INSIDE is the default.
    """
    return [
        {
            'replaceAllShapesWithImage': {
                'imageUrl': url,
                'imageReplaceMethod': fit,
                'containsText': {'text': tag, 'matchCase': True},
            }
        }
        for tag, url in images.items()
    ]


def _image_occurrences(response, tags, offset):
    """Count image swaps, reading the replies that follow the text ones."""
    replies = response.get('replies', [])[offset:]
    counts = {}
    for tag, reply in zip(tags, replies):
        counts[tag] = (reply or {}).get(
            'replaceAllShapesWithImage', {}).get('occurrencesChanged', 0)
    return counts


def render_presentation(values, title=None, folder_id=None, interactive=True,
                        images=None, image_fit='CENTER_INSIDE'):
    """Clone the master template, fill it in, and report what actually changed.

    `images` maps a placeholder tag to a publicly fetchable image URL, e.g.
    {'{{LOGO}}': 'https://github.com/acme.png'}. Google fetches these
    server-side, so a local path will not work -- see images.resolve().

    Returns {url, id, occurrences, image_occurrences, orphans}. The caller
    decides how loudly to complain about zero-occurrence tags.
    """
    template_id = _require_template('presentation')
    drive_service, slides_service, _ = _services(interactive=interactive)

    body = {'name': title or f"Presentation - {values.get('{{AGENT_NAME}}', 'Untitled')}"}
    if folder_id or os.environ.get('DOCGEN_OUTPUT_FOLDER_ID'):
        body['parents'] = [folder_id or os.environ['DOCGEN_OUTPUT_FOLDER_ID']]

    copy = drive_service.files().copy(
        fileId=template_id, body=body, supportsAllDrives=True
    ).execute()
    new_id = copy['id']

    tags = list(values)
    image_tags = list(images or {})
    requests = _replace_requests(values) + _image_requests(images or {}, image_fit)

    response = slides_service.presentations().batchUpdate(
        presentationId=new_id, body={'requests': requests}
    ).execute()
    occurrences = _occurrences(response, tags)
    image_occurrences = _image_occurrences(response, image_tags, len(tags))

    # Sweep after the image swap, or the {{LOGO}} text inside the placeholder
    # shape gets blanked before replaceAllShapesWithImage can match on it.
    orphans = _sweep_orphans(new_id, slides_service)

    return {
        'url': f'https://docs.google.com/presentation/d/{new_id}/edit',
        'id': new_id,
        'occurrences': occurrences,
        'image_occurrences': image_occurrences,
        'orphans': orphans,
    }


def _sweep_orphans(presentation_id, slides_service):
    """Blank any {{TAG}} the caller did not supply, so none can ship visibly."""
    leftover = sorted(set(PLACEHOLDER_RE.findall(
        ' '.join(presentation_text(presentation_id, slides_service))
    )))
    if leftover:
        slides_service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={'requests': [
                {'replaceAllText': {
                    'containsText': {'text': tag, 'matchCase': True},
                    'replaceText': FALLBACKS.get(tag, ''),
                }}
                for tag in leftover
            ]},
        ).execute()
    return leftover


# ------------------------------------------------------------------ public API


def generate_google_slides(agent_name, tagline, team_names, date, status, industry,
                           interactive=True, logo_url=None, logo_path=None,
                           repo_path=None):
    """Clone the master presentation template and replace the key variables.

    A logo may be given either as a URL (used as-is) or as a local path, which
    is uploaded to Drive and shared first, because Google fetches images
    server-side. Both are optional; without one the {{LOGO}} placeholder is
    swept blank like any other unfilled tag.
    """
    import images as images_mod

    values = {
        '{{AGENT_NAME}}': agent_name,
        '{{TAGLINE}}': tagline,
        '{{TEAM_NAMES}}': team_names,
        '{{DATE}}': date,
        '{{STATUS}}': status,
        '{{INDUSTRY}}': industry,
    }

    image_map = {}
    upload = None
    if logo_url or logo_path:
        drive_service, _, _ = _services(interactive=interactive)
        resolved = images_mod.resolve(
            logo_url or logo_path, root=repo_path,
            drive_service=drive_service, folder_id=output_folder(),
        )
        image_map['{{LOGO}}'] = resolved['url']
        upload = resolved if resolved.get('uploaded') else None

    result = render_presentation(values, interactive=interactive, images=image_map)

    unmatched = [tag for tag, count in result['occurrences'].items() if count == 0]
    if image_map:
        unmatched += [t for t, n in result['image_occurrences'].items() if n == 0]
    if unmatched:
        result['warning'] = (
            'These tags are not present in the template, so their values were dropped: '
            + ', '.join(unmatched)
        )
    if upload:
        result['logo_upload'] = upload
    return result


def generate_google_doc(project_name, use_case, capabilities, interactive=True):
    """Clone the brochure template in Google Docs and replace its variables."""
    template_id = _require_template('brochure')
    drive_service, _, docs_service = _services(interactive=interactive)

    copy = drive_service.files().copy(
        fileId=template_id, body={'name': f'Brochure - {project_name}'},
        supportsAllDrives=True,
    ).execute()
    new_id = copy['id']

    values = {
        '{{PROJECT_NAME}}': project_name,
        '{{USE_CASE}}': use_case,
        '{{CAPABILITIES}}': capabilities,
    }
    tags = list(values)
    response = docs_service.documents().batchUpdate(
        documentId=new_id, body={'requests': _replace_requests(values)}
    ).execute()

    return {
        'url': f'https://docs.google.com/document/d/{new_id}/edit',
        'id': new_id,
        'occurrences': _occurrences(response, tags),
    }


DOCX_MIME = ('application/vnd.openxmlformats-officedocument'
             '.wordprocessingml.document')
GOOGLE_DOC_MIME = 'application/vnd.google-apps.document'


def upload_docx(path, name=None, folder_id=None, convert=True, interactive=True):
    """Upload a filled .docx to Drive. Returns {url, id, name, folder}.

    Unlike the template generators, this uploads a file built locally rather
    than copying a Drive master -- the .docx template is filled on disk, then
    pushed. With convert=True Drive turns it into an editable Google Doc; with
    convert=False it stays a .docx you download.
    """
    source = Path(path)
    if not source.exists():
        raise DocGenError(f'Nothing to upload: {source} does not exist.')

    drive_service, _, _ = _services(interactive=interactive)

    body = {'name': name or source.stem}
    folder = output_folder(folder_id)
    if folder:
        body['parents'] = [folder]
    if convert:
        body['mimeType'] = GOOGLE_DOC_MIME

    media = MediaFileUpload(str(source), mimetype=DOCX_MIME, resumable=False)
    created = drive_service.files().create(
        body=body, media_body=media, fields='id,name,webViewLink',
        supportsAllDrives=True,
    ).execute()

    return {
        'url': created.get('webViewLink')
               or f"https://docs.google.com/document/d/{created['id']}/edit",
        'id': created['id'],
        'name': created.get('name', body['name']),
        'folder': folder or 'My Drive (root)',
    }


def brochure_fields(template=None):
    """Field keys in the brochure .docx, mapped to their placeholder text."""
    import fill_docx

    source = Path(template or BROCHURE_DOCX_PATH)
    if not source.exists():
        raise DocGenError(
            f'Brochure template not found at {source}. Set DOCGEN_BROCHURE_DOCX '
            'to point at the .docx.'
        )
    return fill_docx.field_keys(source)


def render_brochure_docx(values, name=None, folder_id=None, template=None,
                         keep_local=None, interactive=True):
    """Fill the brochure .docx and upload it. Returns {url, id, filled, skipped}.

    Mirrors render_presentation's contract: the caller supplies decided values,
    this reports exactly which fields were written and which were left showing
    the template's own guidance text.
    """
    import fill_docx

    source = Path(template or BROCHURE_DOCX_PATH)
    if not source.exists():
        raise DocGenError(
            f'Brochure template not found at {source}. Set DOCGEN_BROCHURE_DOCX '
            'to point at the .docx.'
        )

    known = set(fill_docx.field_keys(source))
    unknown = sorted(set(values) - known)
    if unknown:
        raise DocGenError(
            'No such field key in the template: ' + ', '.join(unknown)
            + '. Call list_brochure_fields to see the valid keys.'
        )

    local = Path(keep_local or source.with_name(source.stem.replace('_Template', '')
                                                + '_Filled.docx'))
    report = fill_docx.fill(source, local, values)
    uploaded = upload_docx(local, name=name, folder_id=folder_id,
                           convert=True, interactive=interactive)

    return {
        'url': uploaded['url'],
        'id': uploaded['id'],
        'name': uploaded['name'],
        'folder': uploaded['folder'],
        'local': str(local),
        'filled': report['filled'],
        'skipped': report['untouched'],
    }
