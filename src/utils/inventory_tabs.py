"""Pure helpers for reading inventory screen state (quantity, filter tabs)."""
import re

from PIL import Image, ImageStat

# Mean luminance of the underline strip around each tab. Measured on 2560x1440
# screenshots: about 125 under the selected tab and 41-50 under the others.
TAB_UNDERLINE_SELECTED_MIN = 90
TAB_UNDERLINE_OTHER_MAX = 70
TAB_UNDERLINE_HALF_WIDTH = 0.025

_QUANTITY = re.compile(r"^(\d+)/(\d+)$")


def parse_quantity(text: str) -> tuple[int, int] | None:
    """Parse an inventory quantity such as "2962/3000"; None when unreadable."""
    match = _QUANTITY.match(text.strip())
    if match is None:
        return None
    count, capacity = int(match.group(1)), int(match.group(2))
    if capacity == 0 or count > capacity:
        return None
    return count, capacity


def selected_tab_index(
    strip: Image.Image, centres: list[float], strip_x: float, strip_width: float
) -> int | None:
    """Index of the single underlined tab in ``strip``, or None if not exactly one.

    ``centres`` are tab x centres and ``strip_x``/``strip_width`` the strip's
    horizontal extent, all as fractions of the window width.
    """
    gray = strip.convert("L")
    width, height = gray.size
    levels = []
    for centre in centres:
        left = int((centre - TAB_UNDERLINE_HALF_WIDTH - strip_x) / strip_width * width)
        right = int((centre + TAB_UNDERLINE_HALF_WIDTH - strip_x) / strip_width * width)
        left, right = max(0, left), min(width, right)
        if right <= left:
            return None
        levels.append(ImageStat.Stat(gray.crop((left, 0, right, height))).mean[0])
    selected = [i for i, level in enumerate(levels) if level >= TAB_UNDERLINE_SELECTED_MIN]
    if len(selected) != 1:
        return None
    if any(level > TAB_UNDERLINE_OTHER_MAX for i, level in enumerate(levels) if i != selected[0]):
        return None
    return selected[0]


def same_label(text: str, expected: str) -> bool:
    """Compare OCR'd labels ignoring case and spacing."""
    return re.sub(r"\s+", "", text).lower() == re.sub(r"\s+", "", expected).lower()
