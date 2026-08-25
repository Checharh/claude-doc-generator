#!/usr/bin/env python3
# extract.py
"""Gather deterministic evidence about a repository.

This does the mechanical half of extraction -- parsing manifests, reading git
history, fingerprinting dependencies -- and emits JSON. It deliberately makes
no judgment calls: it reports *candidates* with their sources and lets the
agent choose, because "which of these three names is the product name" needs
reading comprehension while "what does package.json say" does not.

Being a plain function of the filesystem makes this snapshot-testable, which is
the whole point of separating it from the agent.

    ./venv/bin/python extract.py [repo_path]
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

MAX_README_BYTES = 64_000

BOT_PATTERNS = re.compile(
    r'\[bot\]|dependabot|renovate|github-actions|semantic-release|greenkeeper|'
    r'snyk-bot|imgbot|allcontributors|noreply@',
    re.I,
)

SCAFFOLD_NAMES = {
    'app', 'test', 'tests', 'demo', 'example', 'untitled', 'new-project',
    'my-app', 'my-project', 'project', 'temp', 'tmp', 'foo', 'bar', 'src',
}

CI_PATHS = [
    '.github/workflows', '.gitlab-ci.yml', '.circleci/config.yml',
    'azure-pipelines.yml', 'Jenkinsfile', '.travis.yml', '.drone.yml',
]

DEPLOY_PATHS = [
    'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'vercel.json',
    'fly.toml', 'Procfile', 'netlify.toml', 'k8s', 'kubernetes', 'helm',
    'serverless.yml', 'app.yaml', 'render.yaml',
]

TEST_PATHS = [
    'tests', 'test', '__tests__', 'spec', 'pytest.ini', 'tox.ini',
    'jest.config.js', 'jest.config.ts', 'vitest.config.ts', 'conftest.py',
]

# Dependency / keyword fingerprints -> INDUSTRY taxonomy value. First match by
# score wins; ties break toward the earlier entry.
INDUSTRY_FINGERPRINTS = {
    'Fintech': ['stripe', 'plaid', 'braintree', 'paypal', 'adyen', 'wise',
                'ledger', 'iso20022', 'quickbooks', 'xero', 'coinbase', 'web3'],
    'Healthtech': ['fhir', 'hl7', 'dicom', 'pydicom', 'hipaa', 'epic-fhir',
                   'medplum', 'openmrs'],
    'Developer Tools': ['mcp', 'fastmcp', 'click', 'typer', 'commander',
                        'yargs', 'tree-sitter', 'language-server', 'lsp',
                        'codemod', 'jscodeshift', 'semver', 'nodegit'],
    'E-Commerce': ['shopify', 'commercetools', 'woocommerce', 'magento',
                   'medusa', 'saleor', 'bigcommerce'],
    'Logistics': ['osrm', 'mapbox', 'here-api', 'shipengine', 'easypost',
                  'freight', 'warehouse', 'fleet'],
    'Marketing & Sales': ['hubspot', 'salesforce', 'mailchimp', 'sendgrid',
                          'klaviyo', 'segment', 'intercom', 'braze'],
    'Data & Analytics': ['dbt', 'airflow', 'dagster', 'prefect', 'spark',
                         'pyspark', 'snowflake', 'bigquery', 'duckdb',
                         'pandas', 'polars', 'clickhouse', 'kafka'],
    'Enterprise SaaS': ['auth0', 'okta', 'keycloak', 'casbin', 'workos',
                        'multi-tenant', 'rbac', 'saml', 'scim'],
    'Education': ['moodle', 'canvas-lms', 'scorm', 'lti', 'edx'],
    'Media': ['ffmpeg', 'mux', 'cloudinary', 'imgix', 'livekit', 'hls'],
    'Gaming': ['phaser', 'godot', 'unity', 'pygame', 'three', 'babylonjs'],
    'Real Estate': ['mls', 'zillow', 'idx-broker', 'rets'],
    'Legal': ['docusign', 'contract', 'clause', 'ediscovery'],
    'HR': ['workday', 'greenhouse', 'lever', 'bamboohr', 'ats', 'payroll'],
    'Security': ['semgrep', 'trivy', 'snyk', 'bandit', 'vault', 'sops',
                 'osquery', 'yara', 'sigma'],
}

INDUSTRY_FALLBACK = 'General Technology'

# Ubiquitous build/runtime tooling. Present in most repos regardless of domain,
# so it must never drive classification -- eslint + vite in a support-agent app
# was classifying it as Developer Tools.
UBIQUITOUS_DEPS = {
    'eslint', 'prettier', 'vite', 'webpack', 'babel', 'rollup', 'esbuild',
    'typescript', 'ts-node', 'nodemon', 'pytest', 'ruff', 'black', 'flake8',
    'mypy', 'tox', 'react', 'vue', 'svelte', 'angular', 'next', 'nuxt',
    'express', 'fastapi', 'flask', 'django', 'axios', 'requests', 'lodash',
    'dotenv', 'chalk', 'zod',
}


# ------------------------------------------------------------------ utilities


def _run(args, cwd):
    try:
        out = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=15, check=False
        )
        return out.stdout.strip() if out.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def _read(path, limit=MAX_README_BYTES):
    try:
        return path.read_text(encoding='utf-8', errors='replace')[:limit]
    except OSError:
        return ''


def _exists_any(root, names):
    return [n for n in names if (root / n).exists()]


# ------------------------------------------------------------------ .docgen.yml


def parse_docgen_yml(root):
    """Tier-1 overrides. A flat key: value subset of YAML, so no dependency.

    Anything here is authoritative and must not be second-guessed.
    """
    for name in ('.docgen.yml', '.docgen.yaml'):
        path = root / name
        if not path.exists():
            continue
        data = {}
        for raw in _read(path, 8_000).splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or ':' not in line:
                continue
            key, _, value = line.partition(':')
            value = value.strip().strip('"').strip("'")
            if value:
                data[key.strip().lower()] = value
        return {'file': name, 'values': data}
    return {'file': None, 'values': {}}


# ------------------------------------------------------------------- manifests


def read_manifests(root):
    """Structured project metadata, with the file each value came from."""
    found = []

    pkg = root / 'package.json'
    if pkg.exists():
        try:
            data = json.loads(_read(pkg))
            found.append({
                'file': 'package.json',
                'name': data.get('name'),
                'description': data.get('description'),
                'version': data.get('version'),
                'author': data.get('author') if isinstance(data.get('author'), str)
                else (data.get('author') or {}).get('name'),
                'contributors': [
                    c if isinstance(c, str) else c.get('name')
                    for c in data.get('contributors', [])
                ],
                'deps': sorted({
                    *data.get('dependencies', {}),
                    *data.get('devDependencies', {}),
                }),
            })
        except (json.JSONDecodeError, AttributeError):
            pass

    pyproject = root / 'pyproject.toml'
    if pyproject.exists():
        try:
            data = tomllib.loads(_read(pyproject))
            project = data.get('project', {})
            poetry = data.get('tool', {}).get('poetry', {})
            authors = project.get('authors') or poetry.get('authors') or []
            found.append({
                'file': 'pyproject.toml',
                'name': project.get('name') or poetry.get('name'),
                'description': project.get('description') or poetry.get('description'),
                'version': project.get('version') or poetry.get('version'),
                'author': ', '.join(
                    a.get('name', '') if isinstance(a, dict) else str(a)
                    for a in authors
                ) or None,
                'contributors': [],
                'deps': sorted({
                    re.split(r'[<>=!~\[ ]', d)[0]
                    for d in project.get('dependencies', []) if isinstance(d, str)
                } | set(poetry.get('dependencies', {}))),
            })
        except (tomllib.TOMLDecodeError, AttributeError):
            pass

    cargo = root / 'Cargo.toml'
    if cargo.exists():
        try:
            data = tomllib.loads(_read(cargo))
            package = data.get('package', {})
            found.append({
                'file': 'Cargo.toml',
                'name': package.get('name'),
                'description': package.get('description'),
                'version': package.get('version'),
                'author': ', '.join(package.get('authors', [])) or None,
                'contributors': [],
                'deps': sorted(data.get('dependencies', {})),
            })
        except (tomllib.TOMLDecodeError, AttributeError):
            pass

    gomod = root / 'go.mod'
    if gomod.exists():
        text = _read(gomod)
        match = re.search(r'^module\s+(\S+)', text, re.M)
        found.append({
            'file': 'go.mod',
            'name': match.group(1).rsplit('/', 1)[-1] if match else None,
            'description': None, 'version': None, 'author': None,
            'contributors': [],
            'deps': sorted(set(re.findall(r'^\s+([\w.\-/]+)\s+v', text, re.M))),
        })

    # requirements.txt is not a manifest but is the only dep list in many repos.
    reqs = root / 'requirements.txt'
    if reqs.exists():
        found.append({
            'file': 'requirements.txt',
            'name': None, 'description': None, 'version': None, 'author': None,
            'contributors': [],
            'deps': sorted({
                re.split(r'[<>=!~\[ ]', line.strip())[0]
                for line in _read(reqs, 20_000).splitlines()
                if line.strip() and not line.startswith(('#', '-'))
            }),
        })

    return found


# ---------------------------------------------------------------------- README


def read_readme(root):
    for name in ('README.md', 'README.rst', 'README.txt', 'readme.md', 'README'):
        path = root / name
        if not path.exists():
            continue
        text = _read(path)
        heading = None
        tagline = None
        lines = text.splitlines()
        for index, raw in enumerate(lines):
            line = raw.strip()
            if heading is None and line.startswith('# '):
                heading = line[2:].strip()
                # First substantial prose line after the H1, skipping badges,
                # blockquotes and images -- that is where taglines live.
                for follow in lines[index + 1:index + 12]:
                    candidate = follow.strip()
                    if (not candidate or candidate.startswith(('#', '>', '!', '[', '|', '-', '*', '='))
                            or candidate.startswith('```')):
                        continue
                    tagline = candidate
                    break
                break
        return {'file': name, 'heading': heading, 'first_paragraph': tagline}
    return {'file': None, 'heading': None, 'first_paragraph': None}


# ------------------------------------------------------------------------- git


def read_git(root):
    if not (root / '.git').exists():
        return {'is_git': False, 'authors': [], 'commit_count': 0,
                'last_commit': None, 'remote': None}

    shortlog = _run(['git', 'shortlog', '-sne', '--no-merges', 'HEAD'], root)
    authors = []
    for line in shortlog.splitlines():
        match = re.match(r'\s*(\d+)\s+(.*?)\s+<(.+?)>', line)
        if not match:
            continue
        commits, name, email = match.groups()
        if BOT_PATTERNS.search(name) or BOT_PATTERNS.search(email):
            continue
        authors.append({'name': name, 'commits': int(commits)})

    count = _run(['git', 'rev-list', '--count', 'HEAD'], root)
    return {
        'is_git': True,
        # Emails are deliberately dropped here so they cannot reach a slide.
        'authors': authors[:8],
        'commit_count': int(count) if count.isdigit() else 0,
        'last_commit': _run(['git', 'log', '-1', '--format=%cs'], root) or None,
        'remote': _run(['git', 'config', '--get', 'remote.origin.url'], root) or None,
    }


# ---------------------------------------------------------------- status rules


def status_evidence(root, manifests, git):
    versions = [m['version'] for m in manifests if m.get('version')]
    version = versions[0] if versions else None

    ci = _exists_any(root, CI_PATHS)
    deploy = _exists_any(root, DEPLOY_PATHS)
    tests = _exists_any(root, TEST_PATHS)
    readme_has_usage = bool(re.search(
        r'^#+\s*(usage|getting started|quick ?start|installation)',
        _read(root / 'README.md'), re.I | re.M,
    ))

    def major(v):
        try:
            return int(re.match(r'v?(\d+)', v).group(1))
        except (AttributeError, TypeError, ValueError):
            return None

    def minor(v):
        try:
            return int(re.match(r'v?\d+\.(\d+)', v).group(1))
        except (AttributeError, TypeError, ValueError):
            return None

    wip = bool(re.search(r'\b(wip|work in progress|experimental|alpha|early)\b',
                         _read(root / 'README.md', 4_000), re.I))

    # First matching rule wins; when torn, prefer the lower claim.
    if version and (major(version) or 0) >= 1 and ci and deploy:
        suggested = 'Production'
    elif version and ((major(version) or 0) >= 1 or (minor(version) or 0) >= 5) and ci:
        suggested = 'Beta'
    elif tests and ci:
        suggested = 'Beta'
    elif git['commit_count'] and git['commit_count'] < 20 or wip:
        suggested = 'Prototype'
    elif readme_has_usage:
        suggested = 'MVP'
    elif not manifests and not git['is_git']:
        suggested = 'Concept'
    else:
        suggested = 'Prototype'

    return {
        'version': version,
        'ci': ci,
        'deploy': deploy,
        'tests': tests,
        'readme_has_usage': readme_has_usage,
        'readme_says_wip': wip,
        'commit_count': git['commit_count'],
        'suggested': suggested,
    }


# -------------------------------------------------------------- industry rules


def industry_evidence(manifests, readme):
    deps = {d.lower() for m in manifests
            for d in (m.get('deps') or [])} - UBIQUITOUS_DEPS
    haystack = ' '.join(filter(None, [
        readme.get('heading'), readme.get('first_paragraph'),
        *(m.get('description') or '' for m in manifests),
    ])).lower()

    scores = {}
    matched = {}
    for industry, needles in INDUSTRY_FINGERPRINTS.items():
        hits = [n for n in needles
                if any(n in dep for dep in deps) or f' {n}' in f' {haystack}']
        if hits:
            scores[industry] = len(hits)
            matched[industry] = hits

    suggested = max(scores, key=scores.get) if scores else INDUSTRY_FALLBACK
    return {'matched': matched, 'suggested': suggested}


# ------------------------------------------------------------------ candidates


def name_candidates(root, readme, manifests, git):
    out = []
    if readme.get('heading'):
        out.append({'value': readme['heading'], 'source': f"{readme['file']} H1"})
    for m in manifests:
        if m.get('name'):
            out.append({'value': m['name'], 'source': f"{m['file']}:name"})
    if git.get('remote'):
        slug = re.sub(r'\.git$', '', git['remote'].rstrip('/')).rsplit('/', 1)[-1]
        if slug:
            out.append({'value': slug, 'source': 'git remote'})
    out.append({
        'value': root.name,
        'source': 'directory name',
        'scaffold': root.name.lower() in SCAFFOLD_NAMES,
    })
    return out


def tagline_candidates(readme, manifests):
    out = []
    if readme.get('first_paragraph'):
        out.append({'value': readme['first_paragraph'],
                    'source': f"{readme['file']} first paragraph"})
    for m in manifests:
        if m.get('description'):
            out.append({'value': m['description'], 'source': f"{m['file']}:description"})
    return out


def logo_candidates(root, git, overrides):
    """Logo sources for the {{LOGO}} placeholder, best first.

    Delegated to images.py, which owns the format rules -- the Slides API takes
    PNG, JPEG and GIF only, so an SVG logo is reported as blocked rather than
    handed over to fail at insertion time.
    """
    try:
        import images
    except ImportError:
        return []
    return images.logo_candidates(root, git, overrides)


def team_candidates(git, manifests):
    out = []
    if git['authors']:
        out.append({
            'value': ', '.join(a['name'] for a in git['authors'][:5]),
            'source': 'git shortlog (bots excluded)',
        })
    for m in manifests:
        people = [p for p in [m.get('author'), *m.get('contributors', [])] if p]
        if people:
            out.append({'value': ', '.join(people), 'source': f"{m['file']}:author"})
    codeowners = None
    return [c for c in out if c['value']] or ([codeowners] if codeowners else [])


# ------------------------------------------------------------------------ main


URL_RE = re.compile(r'^(https?://|git@|ssh://|git://)')


def is_remote(value):
    return bool(URL_RE.match(str(value).strip()))


def clone_temp(url, into):
    """Blobless clone: full history for git shortlog, without the file blobs."""
    target = Path(into) / re.sub(r'\.git$', '', url.rstrip('/')).rsplit('/', 1)[-1]
    result = subprocess.run(
        ['git', 'clone', '--filter=blob:none', '--quiet', url, str(target)],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if result.returncode != 0:
        raise DocGenExtractError(
            f'Could not clone {url}\n{result.stderr.strip()[:300]}'
        )
    return target


class DocGenExtractError(Exception):
    """Something the caller can fix."""


def gather(repo_path):
    """Evidence for a local path or a remote git URL.

    A URL is cloned to a temporary directory and removed afterwards -- pasting a
    GitHub link is the obvious thing to try, so it works.
    """
    if is_remote(repo_path):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix='docgen-')
        try:
            evidence = gather(clone_temp(str(repo_path).strip(), tmp))
            evidence['repo']['source_url'] = str(repo_path).strip()
            evidence['repo']['path'] = '(temporary clone)'
            return evidence
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(
            f'{root} is not a directory. Pass a local path, or a git URL '
            '(https://github.com/owner/repo) to clone it automatically.'
        )

    overrides = parse_docgen_yml(root)
    manifests = read_manifests(root)
    readme = read_readme(root)
    git = read_git(root)
    today = date.today()

    return {
        'repo': {'path': str(root), 'dir_name': root.name},
        'overrides': overrides,
        'readme': readme,
        'manifests': [{k: v for k, v in m.items() if k != 'deps'} for m in manifests],
        'dependency_count': sum(len(m.get('deps') or []) for m in manifests),
        'git': git,
        'candidates': {
            'AGENT_NAME': name_candidates(root, readme, manifests, git),
            'TAGLINE': tagline_candidates(readme, manifests),
            'TEAM_NAMES': team_candidates(git, manifests),
            'DATE': [{'value': today.strftime('%d %B %Y'), 'source': 'system clock'}],
            'LOGO': logo_candidates(root, git, overrides['values']),
        },
        'STATUS': status_evidence(root, manifests, git),
        'INDUSTRY': industry_evidence(manifests, readme),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', nargs='?', default='.',
                        help='local path, or a git URL to clone')
    parser.add_argument('--compact', action='store_true', help='single-line JSON')
    args = parser.parse_args()

    try:
        evidence = gather(args.repo)
    except (OSError, DocGenExtractError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1

    print(json.dumps(evidence, indent=None if args.compact else 2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
