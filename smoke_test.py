# smoke_test.py
"""Phase 0.5 -- prove the pipeline end to end with hardcoded data.

No repository analysis, no agent, no MCP. Just: clone the template, push six
known values in, verify each one actually landed, print the URL.

    ./venv/bin/python smoke_test.py
"""

import sys
from datetime import date

import workspace
from workspace import DocGenError

PAYLOAD = {
    '{{AGENT_NAME}}': 'Doc Generator',
    '{{TAGLINE}}': 'Turns any repository into a client-ready deck',
    '{{TEAM_NAMES}}': 'Cesar Hinojosa',
    '{{DATE}}': date.today().strftime('%d %B %Y'),
    '{{STATUS}}': 'Prototype',
    '{{INDUSTRY}}': 'Developer Tools',
}

# Tags the current template is known to contain. Anything outside this set is
# reported but does not fail the run -- see the note printed at the end.
REQUIRED = {'{{AGENT_NAME}}', '{{TAGLINE}}', '{{TEAM_NAMES}}', '{{DATE}}'}


def main():
    print('Template placeholders:')
    available = workspace.list_template_placeholders('presentation')
    print(f"  {', '.join(available) if available else '(none found)'}\n")

    print('Generating deck with hardcoded values...')
    result = workspace.render_presentation(PAYLOAD, title='SMOKE TEST - Doc Generator')

    print('\nReplacements:')
    failures = []
    for tag, value in PAYLOAD.items():
        count = result['occurrences'].get(tag, 0)
        if count:
            mark = 'ok'
        elif tag in REQUIRED:
            mark = 'FAIL'
            failures.append(tag)
        else:
            mark = 'n/a'
        print(f'  [{mark:4}] {tag:16} x{count:<3} {value[:44]}')

    if result['orphans']:
        print(f"\n  Swept unfilled tags: {', '.join(result['orphans'])}")

    print(f"\nDeck: {result['url']}")

    unsupported = sorted(set(PAYLOAD) - set(available))
    if unsupported:
        print(f"\nNote: the template has no slot for {', '.join(unsupported)}. "
              'Add a text box with those tags to the master template if the deck '
              'should display them.')

    if failures:
        print(f"\nFAILED: {', '.join(failures)} replaced nothing.", file=sys.stderr)
        return 1

    print('\nPASS: every required placeholder was replaced.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except DocGenError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(1)
