from datetime import UTC, datetime

from immich_album_exporter.template import TemplateRenderer, sanitize_segment


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
