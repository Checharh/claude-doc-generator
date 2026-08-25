# test_brochure.py
"""The brochure .docx pipeline. No network, no credentials.

Filling the template is pure local work -- a zip of XML -- so the interesting
half is exercised for real here. Only the Drive upload is faked.

    ./venv/bin/python -m pytest test_brochure.py -q
"""

import types
import zipfile

import pytest

import fill_docx
import workspace


@pytest.fixture
def template():
    path = workspace.BROCHURE_DOCX_PATH
    if not path.exists():
        pytest.skip(f'brochure template not present at {path}')
    return path


@pytest.fixture
def out(tmp_path):
    return tmp_path / 'filled.docx'


class TestFieldKeys:
    def test_template_is_a_valid_docx(self, template):
        assert zipfile.is_zipfile(template)

    def test_finds_the_catalog_fields(self, template):
        keys = fill_docx.field_keys(template)
        for expected in ('agent_name', 'prepared_by', 'kpis_focus',
                         'security_notes', 'github_url'):
            assert expected in keys

    def test_every_field_has_placeholder_text(self, template):
        keys = fill_docx.field_keys(template)
        assert all(isinstance(v, str) for v in keys.values())


class TestFill:
    def test_writes_only_the_supplied_fields(self, template, out):
        report = fill_docx.fill(template, out, {'agent_name': 'Atlas Router'})
        assert report['filled']['agent_name'] >= 1
        assert 'kpis_focus' in report['untouched']

    def test_value_lands_in_the_document(self, template, out):
        fill_docx.fill(template, out, {'agent_name': 'Zzyzx Marker'})
        assert fill_docx.field_keys(out)['agent_name'] == 'Zzyzx Marker'

    def test_untouched_field_keeps_its_guidance_text(self, template, out):
        """An omitted field must read as an open item, never as blank."""
        before = fill_docx.field_keys(template)['kpis_focus']
        fill_docx.fill(template, out, {'agent_name': 'Atlas'})
        assert fill_docx.field_keys(out)['kpis_focus'] == before

    def test_multiline_value_becomes_multiple_paragraphs(self, template, out):
        fill_docx.fill(template, out, {'capabilities': 'One: a\nTwo: b'})
        text = fill_docx.field_keys(out)['capabilities']
        assert 'One: a' in text and 'Two: b' in text

    def test_empty_value_is_not_written(self, template, out):
        report = fill_docx.fill(template, out, {'agent_name': '   '})
        assert 'agent_name' not in report['filled']

    def test_none_value_is_not_written(self, template, out):
        report = fill_docx.fill(template, out, {'agent_name': None})
        assert 'agent_name' not in report['filled']

    def test_unknown_key_is_reported(self, template, out):
        report = fill_docx.fill(template, out, {'not_a_field': 'x'})
        assert report['unknown'] == ['not_a_field']

    def test_output_is_a_valid_docx(self, template, out):
        fill_docx.fill(template, out, {'agent_name': 'Atlas'})
        with zipfile.ZipFile(out) as archive:
            assert archive.testzip() is None
            assert 'word/document.xml' in archive.namelist()

    def test_header_image_survives_the_rewrite(self, template, out):
        """The template's header logo must not be dropped when repacking."""
        fill_docx.fill(template, out, {'agent_name': 'Atlas'})
        with zipfile.ZipFile(out) as archive:
            assert 'word/media/image1.png' in archive.namelist()


class TestRenderBrochureDocx:
    """The wrapper that fills then uploads. Upload is faked."""

    @pytest.fixture
    def fake_upload(self, monkeypatch):
        calls = {}

        def upload(path, name=None, folder_id=None, convert=True,
                   interactive=True):
            calls.update(path=str(path), name=name, convert=convert)
            return {'url': 'https://docs.google.com/document/d/NEW/edit',
                    'id': 'NEW', 'name': name or 'Brochure',
                    'folder': 'My Drive (root)'}

        monkeypatch.setattr(workspace, 'upload_docx', upload)
        return calls

    def test_reports_filled_and_skipped(self, template, fake_upload, tmp_path):
        result = workspace.render_brochure_docx(
            {'agent_name': 'Atlas'}, keep_local=tmp_path / 'x.docx')
        assert result['filled']['agent_name'] >= 1
        assert 'kpis_focus' in result['skipped']
        assert result['url'].endswith('/NEW/edit')

    def test_unknown_key_is_rejected_before_uploading(self, template, tmp_path,
                                                     fake_upload):
        """A typo'd key must fail loudly, not produce a half-filled document."""
        with pytest.raises(workspace.DocGenError, match='No such field key'):
            workspace.render_brochure_docx(
                {'nope': 'x'}, keep_local=tmp_path / 'x.docx')
        assert fake_upload == {}

    def test_converts_to_a_google_doc_on_upload(self, template, tmp_path,
                                               fake_upload):
        workspace.render_brochure_docx(
            {'agent_name': 'Atlas'}, keep_local=tmp_path / 'x.docx')
        assert fake_upload['convert'] is True

    def test_keeps_a_local_copy(self, template, tmp_path, fake_upload):
        local = tmp_path / 'kept.docx'
        result = workspace.render_brochure_docx(
            {'agent_name': 'Atlas'}, keep_local=local)
        assert local.exists()
        assert result['local'] == str(local)


class TestUploadDocx:
    def test_missing_file_is_an_actionable_error(self, tmp_path):
        with pytest.raises(workspace.DocGenError, match='Nothing to upload'):
            workspace.upload_docx(tmp_path / 'nope.docx', interactive=False)


class TestOutputFolder:
    def test_explicit_argument_wins(self):
        assert workspace.output_folder('1' + 'a' * 20) == '1' + 'a' * 20

    def test_accepts_a_pasted_folder_url(self):
        url = 'https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrSt'
        assert workspace.output_folder(url) == '1AbCdEfGhIjKlMnOpQrSt'

    def test_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv('DOCGEN_OUTPUT_FOLDER_ID', '1' + 'z' * 20)
        assert workspace.output_folder() == '1' + 'z' * 20

    def test_blank_means_drive_root(self, monkeypatch):
        monkeypatch.setenv('DOCGEN_OUTPUT_FOLDER_ID', '')
        assert workspace.output_folder() is None
