from __future__ import annotations

from pathlib import Path

import pytest

from pdfengine.rendering.cache import RenderCache


class CountingRenderer:
    version = "test-1"

    def __init__(self, image: bytes) -> None:
        self.image = image
        self.calls = 0

    def render(self, source, page_index, width, password, output_dir) -> bytes:
        self.calls += 1
        return self.image


@pytest.fixture
def renderer(png_bytes) -> CountingRenderer:
    return CountingRenderer(png_bytes(12, 8))


def test_cache_reuses_matching_thumbnail_without_calling_renderer(
    tmp_path, renderer
) -> None:
    cache = RenderCache(tmp_path)

    first = cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))
    second = cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))

    assert first.image_bytes == second.image_bytes == renderer.image
    assert renderer.calls == 1
    assert (first.cache_hit, second.cache_hit) == (False, True)


def test_cache_reports_the_real_png_pixel_size(tmp_path, renderer) -> None:
    result = RenderCache(tmp_path).get_or_render(
        "abc", "page_a", 180, renderer, source=Path("d.pdf")
    )

    assert (result.width, result.height) == (12, 8)
    assert result.page_id == "page_a"


@pytest.mark.parametrize(
    ("fingerprint", "page_id", "width"),
    [("changed", "page_a", 180), ("abc", "page_b", 180), ("abc", "page_a", 240)],
)
def test_any_identity_change_misses_the_cache(
    tmp_path, renderer, fingerprint: str, page_id: str, width: int
) -> None:
    cache = RenderCache(tmp_path)
    cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))

    cache.get_or_render(fingerprint, page_id, width, renderer, source=Path("d.pdf"))

    assert renderer.calls == 2


def test_a_renderer_upgrade_invalidates_existing_entries(tmp_path, renderer) -> None:
    cache = RenderCache(tmp_path)
    cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))

    renderer.version = "test-2"
    result = cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))

    assert result.cache_hit is False
    assert renderer.calls == 2


def test_a_corrupt_cache_entry_is_repaired_by_re_rendering(tmp_path, renderer) -> None:
    cache = RenderCache(tmp_path)
    cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))
    cache.path_for("abc", "page_a", 180, renderer).write_bytes(b"truncated")

    result = cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))

    assert result.image_bytes == renderer.image
    assert renderer.calls == 2


def test_cache_writes_nothing_outside_its_root(tmp_path, renderer) -> None:
    root = tmp_path / "cache"
    cache = RenderCache(root)

    cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))

    written = [entry for entry in tmp_path.rglob("*") if entry.is_file()]
    assert written and all(root in entry.parents for entry in written)
    assert not list(root.glob("*.tmp"))


def test_a_miss_without_a_source_path_is_a_programming_error(tmp_path, renderer) -> None:
    with pytest.raises(ValueError, match="source path"):
        RenderCache(tmp_path).get_or_render("abc", "page_a", 180, renderer)


def test_non_png_render_output_is_never_cached(tmp_path) -> None:
    cache = RenderCache(tmp_path)

    with pytest.raises(ValueError, match="non-PNG"):
        cache.get_or_render("abc", "page_a", 180, CountingRenderer(b"nope"), source=Path("d"))

    assert not list(Path(tmp_path).glob("*.png"))


def test_clear_removes_every_cached_image(tmp_path, renderer) -> None:
    cache = RenderCache(tmp_path)
    cache.get_or_render("abc", "page_a", 180, renderer, source=Path("d.pdf"))

    cache.clear()

    assert not list(Path(tmp_path).glob("*.png"))
