#!/usr/bin/env python3
# preflight.py
"""Check everything /generate-slides needs, before running it.

Each check says what is wrong and what to do about it, so a failure is
actionable rather than a traceback thirty seconds into a generation.

    ./venv/bin/python preflight.py
"""

import sys

import workspace

SLIDES_MIME = 'application/vnd.google-apps.presentation'


def line(ok, label, detail=''):
    mark = 'ok  ' if ok else 'FAIL'
    print(f'[{mark}] {label}')
    if detail:
        for part in str(detail).split('\n'):
            print(f'        {part}')
    return ok


def main():
    results = []

    # 1 -- modules
    try:
        import images  # noqa: F401
        results.append(line(True, 'images.py imports'))
    except Exception as exc:
        results.append(line(False, 'images.py imports', exc))
        return 1

    # 2 -- credentials
    try:
        drive, slides, _ = workspace._services(interactive=False)
        results.append(line(True, 'Google authorization', f'token: {workspace.TOKEN_PATH}'))
    except Exception as exc:
        results.append(line(False, 'Google authorization', workspace.explain(exc)))
        print('\nStop here and authorize; nothing below can run without it.')
        return 1

    # 3 -- template registered
    try:
        template_id = workspace._require_template('presentation')
        results.append(line(True, 'presentation template registered', template_id))
    except Exception as exc:
        results.append(line(False, 'presentation template registered', exc))
        return 1

    # 4 -- template is really a Slides deck
    try:
        meta = drive.files().get(fileId=template_id,
                                 fields='id,name,mimeType').execute()
        is_deck = meta['mimeType'] == SLIDES_MIME
        results.append(line(
            is_deck, 'template is a Google Slides deck',
            f"{meta['name']} -- {meta['mimeType']}" + ('' if is_deck else
                '\nThis is not a deck. The Slides API cannot batchUpdate it.'
                '\nFix: setup_template.py --drive "<URL of a real Slides file>"'
                '\nor set DOCGEN_PRESENTATION_TEMPLATE_ID in .env.')))
        if not is_deck:
            return 1
    except Exception as exc:
        results.append(line(False, 'template is a Google Slides deck',
                            workspace.explain(exc)))
        return 1

    # 5 -- text placeholders
    try:
        tags = workspace.list_template_placeholders('presentation')
        results.append(line(bool(tags), 'template has {{TAGS}}',
                            ', '.join(tags) or 'none found'))
    except Exception as exc:
        results.append(line(False, 'template has {{TAGS}}', workspace.explain(exc)))
        tags = []

    # 6 -- the logo shape
    has_logo = '{{LOGO}}' in tags
    results.append(line(
        has_logo, 'template has a {{LOGO}} shape',
        '' if has_logo else
        'No {{LOGO}} found. Images need a *shape* containing that text --\n'
        'Insert > Shape > Rectangle, then type {{LOGO}} into it.\n'
        'See IMAGES.md Step 1. Everything else still works without it.'))

    print()
    if all(results):
        print('All checks passed. /generate-slides can run with a logo.')
        return 0
    if has_logo is False and all(results[:-1]):
        print('Ready except for the logo shape -- slides will generate, '
              'the logo will not land.')
        return 2
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
