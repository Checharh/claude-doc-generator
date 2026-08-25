# test_images.py
"""Logo discovery and image insertion. No network, no credentials.

    ./venv/bin/python -m pytest test_images.py -q
"""

import types

import pytest

import images
import workspace


# ------------------------------------------------------------------ format


class TestClassify:
    @pytest.mark.parametrize('name', ['logo.png', 'logo.PNG', 'a.jpg',
                                      'a.jpeg', 'a.gif'])
    def test_supported_formats(self, name):
        assert images.classify(name)[0] is True

    @pytest.mark.parametrize('name', ['logo.svg', 'logo.webp', 'logo.ico',
                                      'logo.bmp'])
    def test_rejected_formats_explain_why(self, name):
        usable, reason = images.classify(name)
        assert usable is False
        assert 'PNG, JPEG and GIF only' in reason or 'not supported' in reason

    def test_svg_names_the_fix(self):
        """SVG is the common case, so the message has to be actionable."""
        _, reason = images.classify('logo.svg')
        assert 'Convert it' in reason and 'logo_url' in reason

    def test_oversize_file_is_rejected(self, tmp_path, monkeypatch):
        big = tmp_path / 'big.png'
        big.write_bytes(b'x')
        monkeypatch.setattr(images, 'MAX_BYTES', 0)
        usable, reason = images.classify(big)
        assert usable is False and '50MB' in reason


class TestCheckUrl:
    def test_accepts_https(self):
        assert images.check_url('https://example.com/a.png').endswith('a.png')

    def test_rejects_non_url(self):
        with pytest.raises(images.ImageError, match='http'):
            images.check_url('assets/logo.png')

    def test_rejects_overlong_url(self):
        with pytest.raises(images.ImageError, match='2 kB'):
            images.check_url('https://e.com/' + 'a' * 2100)


# ------------------------------------------------------------------ discovery


class TestGitHubRemote:
    @pytest.mark.parametrize('remote', [
        'https://github.com/acme/widget',
        'https://github.com/acme/widget.git',
        'git@github.com:acme/widget.git',
        'ssh://git@github.com/acme/widget/',
    ])
    def test_parses_every_remote_form(self, remote):
        assert images.parse_github_remote(remote) == ('acme', 'widget')

    def test_non_github_remote_yields_nothing(self):
        assert images.parse_github_remote('https://gitlab.com/a/b') is None
        assert images.github_candidates('https://gitlab.com/a/b') == []

    def test_candidates_are_public_urls_needing_no_upload(self):
        found = images.github_candidates('https://github.com/acme/widget')
        urls = [c['value'] for c in found]
        assert 'https://github.com/acme.png' in urls
        assert any('opengraph.githubassets.com/1/acme/widget' in u for u in urls)
        assert all(c['kind'] == 'url' for c in found)


class TestFindLogoFiles:
    def test_finds_a_root_logo(self, tmp_path):
        (tmp_path / 'logo.png').write_bytes(b'x')
        assert [p.name for p in images.find_logo_files(tmp_path)] == ['logo.png']

    def test_searches_known_subdirectories(self, tmp_path):
        (tmp_path / 'docs').mkdir()
        (tmp_path / 'docs' / 'logo.png').write_bytes(b'x')
        assert images.find_logo_files(tmp_path)

    def test_ignores_unrelated_images(self, tmp_path):
        (tmp_path / 'screenshot.png').write_bytes(b'x')
        assert images.find_logo_files(tmp_path) == []

    def test_shallower_path_ranks_first(self, tmp_path):
        (tmp_path / 'assets').mkdir()
        (tmp_path / 'assets' / 'logo.png').write_bytes(b'x')
        (tmp_path / 'logo.png').write_bytes(b'x')
        first = images.find_logo_files(tmp_path)[0]
        assert first.parent == tmp_path

    def test_supported_format_ranks_above_svg(self, tmp_path):
        (tmp_path / 'logo.svg').write_bytes(b'x')
        (tmp_path / 'logo.png').write_bytes(b'x')
        assert images.find_logo_files(tmp_path)[0].suffix == '.png'

    def test_framework_scaffolding_ranks_last(self, tmp_path):
        """vite.svg and favicon.png are not anybody's brand logo."""
        (tmp_path / 'favicon.png').write_bytes(b'x')
        (tmp_path / 'logo.png').write_bytes(b'x')
        assert images.find_logo_files(tmp_path)[0].name == 'logo.png'


class TestReadmeImages:
    def test_finds_markdown_and_html_images(self, tmp_path):
        (tmp_path / 'README.md').write_text(
            '# T\n\n![logo](docs/logo.png)\n<img src="https://e.com/a.png">\n')
        found = images.readme_images(tmp_path)
        assert 'docs/logo.png' in found
        assert 'https://e.com/a.png' in found

    def test_badges_are_excluded(self, tmp_path):
        (tmp_path / 'README.md').write_text(
            '# T\n\n![build](https://img.shields.io/badge/x.svg)\n')
        assert images.readme_images(tmp_path) == []

    def test_missing_readme_is_not_an_error(self, tmp_path):
        assert images.readme_images(tmp_path) == []


class TestLogoCandidates:
    def test_docgen_yml_override_ranks_first(self, tmp_path):
        (tmp_path / 'logo.png').write_bytes(b'x')
        out = images.logo_candidates(
            tmp_path, {}, {'logo_url': 'https://e.com/brand.png'})
        assert out[0]['value'] == 'https://e.com/brand.png'
        assert out[0]['source'] == '.docgen.yml'

    def test_blocked_candidates_are_reported_not_dropped(self, tmp_path):
        """A visible 'this SVG cannot work' beats a silently missing logo."""
        (tmp_path / 'logo.svg').write_bytes(b'x')
        out = images.logo_candidates(tmp_path, {}, {})
        assert out and out[0]['usable'] is False
        assert 'blocked' in out[0]

    def test_every_candidate_carries_a_source(self, tmp_path):
        (tmp_path / 'logo.png').write_bytes(b'x')
        out = images.logo_candidates(
            tmp_path, {'remote': 'https://github.com/acme/widget'}, {})
        assert out and all(c['source'] for c in out)


# ------------------------------------------------------------------ resolve


class TestResolve:
    def test_url_passes_through_without_upload(self):
        out = images.resolve('https://e.com/a.png')
        assert out['url'] == 'https://e.com/a.png'
        assert out['uploaded'] is False

    def test_local_path_without_drive_service_explains_why(self, tmp_path):
        (tmp_path / 'logo.png').write_bytes(b'x')
        with pytest.raises(images.ImageError, match='fetches images by URL'):
            images.resolve('logo.png', root=tmp_path)

    def test_missing_file_is_an_error(self, tmp_path):
        with pytest.raises(images.ImageError, match='No such image'):
            images.resolve('nope.png', root=tmp_path)

    def test_empty_value_is_an_error(self):
        with pytest.raises(images.ImageError, match='No image given'):
            images.resolve('')


class FakeDrive:
    """Enough of the Drive API to record what an upload would do."""

    def __init__(self):
        self.created = None
        self.permission = None

    def files(self):
        return self

    def create(self, body=None, media_body=None, fields=None, **kw):
        self.created = body
        return types.SimpleNamespace(
            execute=lambda: {'id': 'IMGID', 'name': body['name']})

    def permissions(self):
        return self

    # permissions().create(...)
    def _perm_create(self, fileId=None, body=None, **kw):
        self.permission = (fileId, body)
        return types.SimpleNamespace(execute=lambda: {})


class TestUpload:
    @pytest.fixture
    def drive(self):
        d = FakeDrive()
        d.permissions = lambda: types.SimpleNamespace(create=d._perm_create)
        return d

    def test_upload_shares_with_anyone(self, tmp_path, drive):
        """Google fetches anonymously, so link-sharing is mandatory."""
        logo = tmp_path / 'logo.png'
        logo.write_bytes(b'x')
        out = images.upload_image(logo, drive)
        assert drive.permission[1] == {'role': 'reader', 'type': 'anyone'}
        assert out['url'] == 'https://drive.google.com/uc?export=view&id=IMGID'
        assert out['shared'] is True

    def test_upload_refuses_svg_before_calling_drive(self, tmp_path, drive):
        svg = tmp_path / 'logo.svg'
        svg.write_bytes(b'<svg/>')
        with pytest.raises(images.ImageError, match='not supported'):
            images.upload_image(svg, drive)
        assert drive.created is None


# ------------------------------------------------------------------ requests


class TestImageRequests:
    def test_builds_replace_all_shapes_with_image(self):
        req = workspace._image_requests({'{{LOGO}}': 'https://e.com/a.png'})
        assert len(req) == 1
        body = req[0]['replaceAllShapesWithImage']
        assert body['imageUrl'] == 'https://e.com/a.png'
        assert body['containsText'] == {'text': '{{LOGO}}', 'matchCase': True}

    def test_default_fit_preserves_aspect_ratio(self):
        """A cropped logo is a broken logo."""
        req = workspace._image_requests({'{{LOGO}}': 'https://e.com/a.png'})
        assert req[0]['replaceAllShapesWithImage']['imageReplaceMethod'] == 'CENTER_INSIDE'

    def test_fit_is_overridable(self):
        req = workspace._image_requests({'{{X}}': 'u'}, fit='CENTER_CROP')
        assert req[0]['replaceAllShapesWithImage']['imageReplaceMethod'] == 'CENTER_CROP'

    def test_no_images_means_no_requests(self):
        assert workspace._image_requests({}) == []

    def test_image_occurrences_read_replies_after_the_text_ones(self):
        """Replies are positional, so the offset must skip the text requests."""
        response = {'replies': [
            {'replaceAllText': {'occurrencesChanged': 1}},
            {'replaceAllText': {'occurrencesChanged': 1}},
            {'replaceAllShapesWithImage': {'occurrencesChanged': 1}},
        ]}
        counts = workspace._image_occurrences(response, ['{{LOGO}}'], offset=2)
        assert counts == {'{{LOGO}}': 1}

    def test_missing_placeholder_reports_zero_not_an_error(self):
        response = {'replies': [{}, {}, {}]}
        counts = workspace._image_occurrences(response, ['{{LOGO}}'], offset=2)
        assert counts == {'{{LOGO}}': 0}


class TestRenderOrdering:
    """Ordering inside the batch is load-bearing and easy to break silently."""

    @pytest.fixture
    def fake_slides(self, monkeypatch):
        state = {'batches': [], 'swept': []}

        class Svc:
            def presentations(self):
                return self

            def get(self, presentationId=None):
                # After the swap the shape is gone, so no {{LOGO}} text remains.
                return types.SimpleNamespace(execute=lambda: {'slides': []})

            def batchUpdate(self, presentationId=None, body=None):
                state['batches'].append(body['requests'])
                replies = []
                for r in body['requests']:
                    key = list(r)[0]
                    replies.append({key: {'occurrencesChanged': 1}})
                return types.SimpleNamespace(execute=lambda: {'replies': replies})

        drive = types.SimpleNamespace(files=lambda: types.SimpleNamespace(
            copy=lambda **k: types.SimpleNamespace(execute=lambda: {'id': 'NEW'})))
        monkeypatch.setattr(workspace, '_services',
                            lambda interactive=True: (drive, Svc(), None))
        monkeypatch.setattr(workspace, '_require_template', lambda kind: 'TPL')
        return state

    def test_text_requests_precede_image_requests(self, fake_slides):
        workspace.render_presentation(
            {'{{AGENT_NAME}}': 'Atlas'},
            images={'{{LOGO}}': 'https://e.com/a.png'})
        kinds = [list(r)[0] for r in fake_slides['batches'][0]]
        assert kinds == ['replaceAllText', 'replaceAllShapesWithImage']

    def test_occurrences_are_attributed_to_the_right_tags(self, fake_slides):
        out = workspace.render_presentation(
            {'{{AGENT_NAME}}': 'Atlas', '{{TAGLINE}}': 'x'},
            images={'{{LOGO}}': 'https://e.com/a.png'})
        assert set(out['occurrences']) == {'{{AGENT_NAME}}', '{{TAGLINE}}'}
        assert out['image_occurrences'] == {'{{LOGO}}': 1}

    def test_no_images_leaves_the_old_behaviour_untouched(self, fake_slides):
        out = workspace.render_presentation({'{{AGENT_NAME}}': 'Atlas'})
        assert out['image_occurrences'] == {}
        assert [list(r)[0] for r in fake_slides['batches'][0]] == ['replaceAllText']
