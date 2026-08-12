# auth.py
"""One-time interactive Google authorization.

Opens a browser, then writes the refresh token to TOKEN_PATH. Run this in a
real terminal; the MCP server never does it.
"""

import sys

import workspace
from workspace import DocGenError


def main():
    try:
        creds = workspace.get_credentials(interactive=True)
    except DocGenError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1

    print(f'Authorized. Token saved to {workspace.TOKEN_PATH}')
    print(f'Scopes: {", ".join(creds.scopes or workspace.SCOPES)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
