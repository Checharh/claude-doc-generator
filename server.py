# server.py
"""MCP server exposing the documentation generators to Claude Code."""

from mcp.server import MCPServer

import workspace
from workspace import DocGenError

mcp = MCPServer('DocGenerator')

# The browser-based OAuth flow would hang a tool call, so the server never
# starts it; auth.py handles that once, in a real terminal.
NON_INTERACTIVE = {'interactive': False}


@mcp.tool()
def generate_slides(agent_name: str, tagline: str, team_names: str, date: str,
                    status: str, industry: str) -> str:
    """Generate a Google Slides deck from repository data. Returns the deck URL.

    Call list_template_placeholders first to confirm which tags the current
    template supports.
    """
    try:
        result = workspace.generate_google_slides(
            agent_name, tagline, team_names, date, status, industry, **NON_INTERACTIVE
        )
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error while generating slides: {type(exc).__name__}: {exc}'

    filled = ', '.join(f'{tag}={count}' for tag, count in result['occurrences'].items())
    lines = [f"Slides generated: {result['url']}", f'Replacements: {filled}']
    if result.get('warning'):
        lines.append(f"Warning: {result['warning']}")
    return '\n'.join(lines)


@mcp.tool()
def generate_brochure(project_name: str, use_case: str, capabilities: str) -> str:
    """Generate a commercial brochure in Google Docs. Returns the document URL."""
    try:
        result = workspace.generate_google_doc(
            project_name, use_case, capabilities, **NON_INTERACTIVE
        )
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error generating brochure: {type(exc).__name__}: {exc}'
    return f"Brochure generated: {result['url']}"


@mcp.tool()
def list_template_placeholders(kind: str = 'presentation') -> str:
    """List the {{TAGS}} the current template contains ('presentation' or 'brochure')."""
    try:
        tags = workspace.list_template_placeholders(kind)
    except DocGenError as exc:
        return f'Configuration error: {exc}'
    except Exception as exc:
        return f'Error reading template: {type(exc).__name__}: {exc}'
    return ', '.join(tags) if tags else f'No {{{{TAGS}}}} found in the {kind} template.'


if __name__ == '__main__':
    mcp.run()
