# images.py
"""Find a logo for a repository, and get it to a URL Google can fetch.

Google's Slides and Docs APIs fetch images **server-side, by URL**. A local
file path can never work, and the format list is PNG/JPEG/GIF only -- notably
not SVG, which is what most repositories actually ship. Those two facts drive
everything here.

Three ways to end up with a usable URL, cheapest first:

  1. An explicit `logo_url:` in .docgen.yml -- authoritative, zero work.
  2. A GitHub URL derived from the remote. Both are already public, need no
     upload, and are stable, so this is the path that works with no setup.
  3. A file in the repository, uploaded to Drive and shared. Only this one
     needs an API call, and only this one can fail on format.

Like extract.py, this reports *candidates with sources* and decides nothing.
"""

import re
from pathlib import Path

# What the APIs will actually accept. SVG and WEBP are common in repos and are
# both rejected, so they are detected and reported rather than silently tried.
SUPPORTED_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif'}
REJECTED_SUFFIXES = {'.svg', '.webp', '.ico', '.avif', '.bmp', '.tiff'}

MAX_BYTES = 50 * 1024 * 1024
MAX_URL_LENGTH = 2048

# Ordered by how likely the file is to be an actual brand logo rather than a
# framework leftover. `vite.svg` and `react.svg` are scaffolding, not a logo.
LOGO_DIRS = ('', '.github', 'docs', 'doc', 'assets', 'public', 'static',
             'src/assets', 'images', 'img', 'media', 'resources')
LOGO_STEMS = ('logo', 'logotype', 'wordmark', 'brand', 'icon', 'banner',
              'header', 'hero', 'social-preview', 'opengraph')
SCAFFOLD_STEMS = {'vite', 'react', 'vue', 'svelte', 'angular', 'next',
                  'nuxt', 'webpack', 'node', 'npm', 'favicon'}

# ![alt](path) and <img src="path">, skipping badges, which are never logos.
MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(\s*([^)\s]+)')
HTML_IMAGE_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
BADGE_HOSTS = ('shields.io', 'badge.fury.io', 'travis-ci', 'circleci.com',
               'codecov.io', 'coveralls.io', 'badgen.net', 'app.netlify.com')


class ImageError(Exception):
    """Something the caller can fix."""


# ------------------------------------------------------------------ inspection


def classify(path):
    """Whether this file can be sent to Google, and why not if it cannot."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_SUFFIXES:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            return False, f'{path.name} exceeds the 50MB API limit'
        return True, ''
    if suffix in REJECTED_SUFFIXES:
        return False, (
            f'{suffix} is not supported by the Slides/Docs API '
            '(PNG, JPEG and GIF only). Convert it first, or set logo_url '
            'in .docgen.yml to an already-hosted PNG.'
        )
    return False, f'{suffix or "no extension"} is not a recognised image format'


def is_url(value):
    return str(value or '').strip().lower().startswith(('http://', 'https://'))


def check_url(url):
    """Validate a URL against the documented API limits. Raises ImageError."""
    url = str(url or '').strip()
    if not is_url(url):
        raise ImageError(f'Not an http(s) URL: {url!r}')
    if len(url) > MAX_URL_LENGTH:
        raise ImageError(f'URL is {len(url)} bytes; the API limit is 2 kB.')
    return url


# ------------------------------------------------------------------- discovery


def parse_github_remote(remote):
    """(owner, repo) from any GitHub remote form, or None."""
    if not remote:
        return None
    match = re.search(
        r'github\.com[:/]+([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$',
        str(remote).strip(),
    )
    return (match.group(1), match.group(2)) if match else None


def github_candidates(remote):
    """Public GitHub image URLs derived from the remote.

    Both are served publicly and need no upload and no auth, which makes this
    the only source that works with zero setup.
    """
    parsed = parse_github_remote(remote)
    if not parsed:
        return []
    owner, repo = parsed
    return [
        {
            'value': f'https://opengraph.githubassets.com/1/{owner}/{repo}',
            'source': 'GitHub social preview (from git remote)',
            'kind': 'url',
            'note': "The repo's Open Graph card. Always renders, even when no "
                    'custom social image is set.',
        },
        {
            'value': f'https://github.com/{owner}.png',
            'source': f'GitHub avatar for {owner} (from git remote)',
            'kind': 'url',
            'note': 'The owner or organization avatar.',
        },
    ]


def _score(path, root):
    """Lower sorts first. Prefers a real brand file over framework scaffolding."""
    stem = path.stem.lower()
    rel = path.relative_to(root)
    depth = len(rel.parts) - 1
    exact = 0 if stem in LOGO_STEMS else 1
    scaffold = 10 if stem in SCAFFOLD_STEMS else 0
    unsupported = 5 if path.suffix.lower() not in SUPPORTED_SUFFIXES else 0
    return (scaffold, unsupported, exact, depth, len(stem), str(rel))


def find_logo_files(root):
    """Image files in the repo that look like a logo, best first."""
    root = Path(root)
    found = []
    for directory in LOGO_DIRS:
        base = root / directory if directory else root
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_file():
                continue
            suffix = entry.suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES | REJECTED_SUFFIXES:
                continue
            if any(stem in entry.stem.lower() for stem in LOGO_STEMS):
                found.append(entry)
    return sorted(set(found), key=lambda p: _score(p, root))


def readme_images(root, readme_name='README.md'):
    """Images referenced by the README, badges excluded."""
    path = Path(root) / readme_name
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding='utf-8', errors='replace')[:64_000]
    except OSError:
        return []
    out = []
    for match in (*MD_IMAGE_RE.findall(text), *HTML_IMAGE_RE.findall(text)):
        ref = match.strip()
        if any(host in ref for host in BADGE_HOSTS):
            continue
        if ref not in out:
            out.append(ref)
    return out


def logo_candidates(root, git=None, overrides=None):
    """Every usable logo source for a repository, best first.

    Mirrors extract.py's contract: candidates with sources, never a decision.
    """
    root = Path(root)
    out = []

    explicit = (overrides or {}).get('logo_url') or (overrides or {}).get('logo')
    if explicit:
        out.append({
            'value': explicit,
            'source': '.docgen.yml',
            'kind': 'url' if is_url(explicit) else 'file',
            'usable': True,
        })

    for entry in find_logo_files(root):
        usable, reason = classify(entry)
        out.append({
            'value': str(entry.relative_to(root)),
            'source': f'repository file {entry.relative_to(root)}',
            'kind': 'file',
            'usable': usable,
            **({'blocked': reason} if reason else {}),
        })

    for ref in readme_images(root):
        if is_url(ref):
            usable, reason = classify(ref.split('?')[0])
            out.append({
                'value': ref, 'source': 'README image', 'kind': 'url',
                'usable': usable, **({'blocked': reason} if reason else {}),
            })

    for candidate in github_candidates((git or {}).get('remote')):
        out.append({**candidate, 'usable': True})

    return out


# --------------------------------------------------------------------- hosting


DRIVE_MIME = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
}


def upload_image(path, drive_service, folder_id=None, share=True):
    """Upload a local image to Drive and return a publicly fetchable URL.

    Google fetches the image server-side and anonymously, so the file has to be
    readable by anyone with the link. That is a real disclosure -- the image
    becomes public to anyone holding the URL -- so it is done explicitly here
    rather than as a side effect somewhere deeper.
    """
    from googleapiclient.http import MediaFileUpload

    source = Path(path)
    if not source.exists():
        raise ImageError(f'No such image: {source}')

    usable, reason = classify(source)
    if not usable:
        raise ImageError(reason)

    body = {'name': source.name}
    if folder_id:
        body['parents'] = [folder_id]

    media = MediaFileUpload(
        str(source), mimetype=DRIVE_MIME[source.suffix.lower()], resumable=False
    )
    created = drive_service.files().create(
        body=body, media_body=media, fields='id,name', supportsAllDrives=True,
    ).execute()
    file_id = created['id']

    if share:
        drive_service.permissions().create(
            fileId=file_id, body={'role': 'reader', 'type': 'anyone'},
            supportsAllDrives=True,
        ).execute()

    return {
        'id': file_id,
        'name': created.get('name', source.name),
        # The form Google's own image fetcher follows for a shared Drive file.
        'url': f'https://drive.google.com/uc?export=view&id={file_id}',
        'shared': share,
    }


def resolve(value, root=None, drive_service=None, folder_id=None):
    """Turn a candidate into a URL Google can fetch.

    A URL passes through. A path is uploaded, which needs a Drive service --
    demanded explicitly so no caller uploads a file by accident.
    """
    value = str(value or '').strip()
    if not value:
        raise ImageError('No image given.')

    if is_url(value):
        return {'url': check_url(value), 'uploaded': False}

    path = Path(root or '.') / value
    if not path.exists():
        raise ImageError(f'No such image: {path}')
    if drive_service is None:
        raise ImageError(
            f'{value} is a local file. Google fetches images by URL, so it has '
            'to be uploaded to Drive first -- pass a drive_service, or set '
            'logo_url in .docgen.yml to an already-hosted PNG.'
        )
    uploaded = upload_image(path, drive_service, folder_id=folder_id)
    return {'url': uploaded['url'], 'uploaded': True, **uploaded}
