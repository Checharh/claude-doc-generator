# setup_template.py
"""Register a master template and normalize its placeholders.

The template can come from three places:

    --drive <URL or ID>   a Slides deck already in your Google Drive  (recommended)
    --pptx <path>         a local .pptx, uploaded and converted
    --list                show the Slides decks in your Drive and exit

Only the *registration* is one-time. Generation always reads the template from
Drive, so nothing needs to stay on disk afterwards.

The shipped hackathon .pptx uses human-readable placeholders
("[ Agent / Project Name ]") rather than {{TAGS}}, and replaceAllText matches
literal strings only -- so without a normalization pass every replacement
silently changes nothing. This script is idempotent: re-running it on an
already-normalized template is a no-op.
"""

import argparse
import re
import sys
from pathlib import Path

from googleapiclient.http import MediaFileUpload

import workspace
from workspace import DocGenError

SLIDES_MIME = 'application/vnd.google-apps.presentation'
DOCS_MIME = 'application/vnd.google-apps.document'
PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

# Literal template text -> canonical tag. Templates in the wild mix three
# conventions, so all of them normalize to {{TAG}}.
PLACEHOLDER_MAP = {
    # prose placeholders (the original hackathon .pptx)
    '[ Agent / Project Name ]': '{{AGENT_NAME}}',
    '[ One-line project tagline ]': '{{TAGLINE}}',
    '[ Presenter name(s), role(s) ]': '{{TEAM_NAMES}}',
    '[ Date ]': '{{DATE}}',
    # single-bracket tags (hand-edited decks)
    '[AGENT_NAME]': '{{AGENT_NAME}}',
    '[TAGLINE]': '{{TAGLINE}}',
    '[TEAM_NAMES]': '{{TEAM_NAMES}}',
    '[DATE]': '{{DATE}}',
    '[STATUS]': '{{STATUS}}',
    '[INDUSTRY]': '{{INDUSTRY}}',
}

# ID/URL parsing lives in workspace so .env and templates.json get it too.
parse_file_id = workspace.parse_file_id


def list_drive_templates(drive_service, mime=SLIDES_MIME, limit=25):
    response = drive_service.files().list(
        q=f"mimeType='{mime}' and trashed=false",
        orderBy='modifiedTime desc',
        pageSize=limit,
        fields='files(id,name,modifiedTime)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return response.get('files', [])


def ensure_native(drive_service, file_id, want_mime=SLIDES_MIME):
    """Return an ID that the Slides/Docs API can actually edit.

    A .pptx sitting in Drive is a binary blob: batchUpdate returns 400 on it.
    Converting is a server-side copy, so the original is left untouched.
    """
    meta = drive_service.files().get(
        fileId=file_id, fields='id,name,mimeType', supportsAllDrives=True
    ).execute()

    if meta['mimeType'] == want_mime:
        print(f"  using '{meta['name']}' ({file_id})")
        return file_id

    if meta['mimeType'].startswith('application/vnd.google-apps'):
        raise DocGenError(
            f"'{meta['name']}' is a {meta['mimeType']}, not a presentation."
        )

    print(f"  '{meta['name']}' is not a native Google file ({meta['mimeType']})")
    print('  converting a copy...')
    converted = drive_service.files().copy(
        fileId=file_id,
        body={'name': f"{meta['name']} (Slides)", 'mimeType': want_mime},
        fields='id',
        supportsAllDrives=True,
    ).execute()
    print(f"  converted -> {converted['id']}")
    return converted['id']


def upload_pptx(drive_service, pptx_path, name):
    media = MediaFileUpload(str(pptx_path), mimetype=PPTX_MIME, resumable=True)
    created = drive_service.files().create(
        body={'name': name, 'mimeType': SLIDES_MIME},
        media_body=media,
        fields='id',
        supportsAllDrives=True,
    ).execute()
    return created['id']


def normalize(slides_service, presentation_id):
    """Rewrite bracket placeholders as {{TAGS}}. Returns {literal: occurrences}."""
    literals = list(PLACEHOLDER_MAP)
    response = slides_service.presentations().batchUpdate(
        presentationId=presentation_id,
        body={'requests': [
            {'replaceAllText': {
                'containsText': {'text': literal, 'matchCase': True},
                'replaceText': PLACEHOLDER_MAP[literal],
            }}
            for literal in literals
        ]},
    ).execute()
    return workspace._occurrences(response, literals)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--drive', metavar='URL_OR_ID',
                        help='a Slides deck already in your Drive')
    source.add_argument('--pptx', type=Path, help='a local .pptx to upload and convert')
    source.add_argument('--list', action='store_true',
                        help='list the Slides decks in your Drive and exit')
    parser.add_argument('--kind', choices=['presentation', 'brochure'],
                        default='presentation')
    parser.add_argument('--name', default='DocGen Master Template')
    parser.add_argument('--dump', action='store_true',
                        help='print every text string in the template and exit')
    parser.add_argument('--no-normalize', action='store_true',
                        help='register as-is; the template already uses {{TAGS}}')
    args = parser.parse_args()

    drive_service, slides_service, _ = workspace._services(interactive=True)

    if args.list:
        mime = DOCS_MIME if args.kind == 'brochure' else SLIDES_MIME
        files = list_drive_templates(drive_service, mime)
        if not files:
            print(f'No {args.kind} files found in your Drive.')
            return 0
        print(f'{args.kind.title()} files in your Drive (newest first):\n')
        for f in files:
            print(f"  {f['id']}  {f['name'][:56]}")
        print(f"\nRegister one with:\n  setup_template.py --drive <ID> --kind {args.kind}")
        return 0

    if args.drive:
        template_id = ensure_native(
            drive_service, parse_file_id(args.drive),
            DOCS_MIME if args.kind == 'brochure' else SLIDES_MIME,
        )
    elif args.pptx:
        if not args.pptx.exists():
            print(f'Error: {args.pptx} not found', file=sys.stderr)
            return 1
        print(f'Uploading {args.pptx.name} as native Google Slides...')
        template_id = upload_pptx(drive_service, args.pptx, args.name)
        print(f'  created {template_id}')
    else:
        parser.error('give one of --drive, --pptx or --list')

    if args.dump:
        for line in workspace.presentation_text(template_id, slides_service):
            print(f'  {line}')
        return 0

    if args.kind == 'presentation' and not args.no_normalize:
        existing = set(workspace.PLACEHOLDER_RE.findall(
            ' '.join(workspace.presentation_text(template_id, slides_service))
        ))
        if existing:
            print(f"Already tagged: {', '.join(sorted(existing))}")

        # Always normalize, even on a partially tagged deck -- that is exactly
        # the case that needs it. replaceAllText on an absent literal is a no-op,
        # so this stays idempotent.
        print('Normalizing remaining placeholders...')
        counts = normalize(slides_service, template_id)
        for literal, count in counts.items():
            if count:
                print(f'  [ok  ] {literal:30} -> {PLACEHOLDER_MAP[literal]:16} x{count}')

        after = set(workspace.PLACEHOLDER_RE.findall(
            ' '.join(workspace.presentation_text(template_id, slides_service))
        ))
        if not after:
            print('\nNothing matched PLACEHOLDER_MAP. Re-run with --dump to see the '
                  "template's actual text, then either fix the map or add {{TAGS}} "
                  'to the deck by hand.', file=sys.stderr)
            return 1

        print(f"\nTemplate tags: {', '.join(sorted(after))}")
        missing = set(workspace.FALLBACKS) - after - {
            '{{PROJECT_NAME}}', '{{USE_CASE}}', '{{CAPABILITIES}}'}
        if missing:
            print(f"  no slot for: {', '.join(sorted(missing))} "
                  '(values passed for these will be dropped)')

    templates = workspace.load_templates()
    templates[args.kind] = template_id
    workspace.save_templates(templates)

    print(f'\nRegistered {args.kind} -> {template_id}')
    print(f'  in {workspace.TEMPLATES_PATH}')
    kind_path = 'document' if args.kind == 'brochure' else 'presentation'
    print(f'  https://docs.google.com/{kind_path}/d/{template_id}/edit')
    print('\nFor .env (overrides templates.json -- use this in production):')
    print(f'  DOCGEN_{args.kind.upper()}_TEMPLATE_ID={template_id}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'\nError: {workspace.explain(exc)}', file=sys.stderr)
        raise SystemExit(1)
