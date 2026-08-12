# setup_template.py
"""Upload a .pptx as a native Google Slides template and normalize its placeholders.

The shipped .pptx uses human-readable placeholders ("[ Agent / Project Name ]")
rather than {{TAGS}}, and replaceAllText matches literal strings only -- so
without this pass every replacement silently changes nothing. Run once per
template; it is idempotent.
"""

import argparse
import sys
from pathlib import Path

from googleapiclient.http import MediaFileUpload

import workspace
from workspace import DocGenError

SLIDES_MIME = 'application/vnd.google-apps.presentation'
PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

DEFAULT_PPTX = Path(__file__).resolve().parent / 'Hackathon_Final_Presentation_Template.pptx'

# Literal template text -> canonical tag.
PLACEHOLDER_MAP = {
    '[ Agent / Project Name ]': '{{AGENT_NAME}}',
    '[ One-line project tagline ]': '{{TAGLINE}}',
    '[ Presenter name(s), role(s) ]': '{{TEAM_NAMES}}',
    '[ Date ]': '{{DATE}}',
}


def upload(drive_service, pptx_path, name):
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pptx', type=Path, default=DEFAULT_PPTX,
                        help='local .pptx to upload')
    parser.add_argument('--reuse', metavar='ID',
                        help='normalize an existing native Slides file instead of uploading')
    parser.add_argument('--name', default='DocGen Master Template - Presentation')
    parser.add_argument('--dump', action='store_true',
                        help='print every text string in the template and exit')
    args = parser.parse_args()

    drive_service, slides_service, _ = workspace._services(interactive=True)

    if args.reuse:
        template_id = args.reuse
        print(f'Reusing presentation {template_id}')
    else:
        if not args.pptx.exists():
            print(f'Error: {args.pptx} not found', file=sys.stderr)
            return 1
        print(f'Uploading {args.pptx.name} as native Google Slides...')
        template_id = upload(drive_service, args.pptx, args.name)
        print(f'  created {template_id}')

    if args.dump:
        for line in workspace.presentation_text(template_id, slides_service):
            print(f'  {line}')
        return 0

    print('Normalizing placeholders...')
    counts = normalize(slides_service, template_id)
    for literal, count in counts.items():
        mark = 'ok  ' if count else 'MISS'
        print(f'  [{mark}] {literal:34} -> {PLACEHOLDER_MAP[literal]:16} x{count}')

    templates = workspace.load_templates()
    templates['presentation'] = template_id
    workspace.save_templates(templates)

    print(f'\nRegistered in {workspace.TEMPLATES_PATH}')
    print(f'Template: https://docs.google.com/presentation/d/{template_id}/edit')

    if not any(counts.values()):
        print('\nNothing was normalized. The template text may differ from '
              'PLACEHOLDER_MAP -- rerun with --dump to see the actual strings.',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except DocGenError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(1)
