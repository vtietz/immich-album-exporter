from datetime import UTC, datetime

import pytest

from immich_album_exporter.template import TemplateRenderError, TemplateRenderer, sanitize_segment


def test_sanitize_segment_removes_windows_unsafe_characters() -> None:
    assert sanitize_segment('2026-01-24 Vincent: Taiwan/Indien?') == '2026-01-24 Vincent Taiwan Indien'


def test_renderer_keeps_nested_folder_template() -> None:
    renderer = TemplateRenderer(
        "{{ album_date.strftime('%Y') }}/{{ album_date.strftime('%Y-%m-%d') }} {{ album_display_title }}",
        "{{ asset_date.strftime('%Y%m%d_%H%M%S') }}{{ collision_suffix }}{{ ext }}",
    )

    folder = renderer.render_folder(
        album={"id": "album-1"},
        album_date=datetime(2026, 1, 24, tzinfo=UTC),
        album_title="Winter Trip",
    )
    filename = renderer.render_filename(
        asset={"id": "asset-1"},
        asset_date=datetime(2026, 1, 24, 9, 15, 4, tzinfo=UTC),
        original_filename="IMG_1234.JPG",
        ext=".jpg",
        collision_suffix="_01",
    )

    assert folder == "2026/2026-01-24 Winter Trip"
    assert filename == "20260124_091504_01.jpg"


ASSET_FOLDER_CONTEXT = {
    "asset": {"id": "asset-1", "exifInfo": {"city": "Taipei"}},
    "asset_date": datetime(2024, 6, 1, 12, tzinfo=UTC),
    "album": {"id": "album-1"},
    "album_date": datetime(2026, 1, 24, tzinfo=UTC),
    "album_title": "2026-01-24 Winter Trip",
}


def _asset_folder_renderer(template: str) -> TemplateRenderer:
    return TemplateRenderer("unused", "{{ ext }}", asset_folder_template=template)


def test_asset_folder_supports_nested_paths_and_album_context() -> None:
    renderer = _asset_folder_renderer(
        "{{ asset_date.strftime('%Y/%m') }}/{{ album_display_title }} {{ asset.exifInfo.city }}"
    )

    assert renderer.render_asset_folder(**ASSET_FOLDER_CONTEXT) == "2024/06/Winter Trip Taipei"


@pytest.mark.parametrize(
    "template",
    ["../{{ asset_date.year }}", "{{ asset.exifInfo.country }}", "{{ '' }}"],
    ids=["traversal", "missing-field", "empty"],
)
def test_asset_folder_render_failures_raise_template_render_error(template: str) -> None:
    with pytest.raises(TemplateRenderError, match="templates.asset_folder"):
        _asset_folder_renderer(template).render_asset_folder(**ASSET_FOLDER_CONTEXT)


def test_filename_render_failure_raises_template_render_error() -> None:
    renderer = TemplateRenderer("unused", "{{ asset.exifInfo.model }}{{ ext }}")

    with pytest.raises(TemplateRenderError, match="templates.filename"):
        renderer.render_filename(
            asset={"id": "asset-1"},
            asset_date=datetime(2024, 6, 1, tzinfo=UTC),
            original_filename="IMG.jpg",
            ext=".jpg",
            collision_suffix="",
        )
