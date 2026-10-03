from config.const import (
    ASPECT_16_9,
    FIRST_ITEM,
    INVENTORY_FILTER_TABS,
    INVENTORY_ROW_STARTS,
    INV_TAB,
    SORT_BUTTON,
)
from models.const import SORT_DATE, SORT_LV, SORT_RARITY


RELIC_NAV_DATA = {
    ASPECT_16_9: {
        INV_TAB: (0.43, 0.06),
        FIRST_ITEM: (0.107, 0.26),
        # Row 5 is partly clipped; y=1230 is the middle of its visible part at 1440p.
        INVENTORY_ROW_STARTS: (
            (0.107, 0.260),
            (0.107, 0.425),
            (0.107, 0.590),
            (0.107, 0.755),
            (0.107, 1230 / 1440),
        ),
        # Names must match the parsed RELIC_SLOT values.
        INVENTORY_FILTER_TABS: (
            ("All", (0.1348, 0.1333)),
            ("Head", (0.1973, 0.1333)),
            ("Hands", (0.2598, 0.1333)),
            ("Body", (0.3223, 0.1333)),
            ("Feet", (0.385, 0.1333)),
            ("Planar Sphere", (0.4475, 0.1333)),
            ("Link Rope", (0.51, 0.1333)),
        ),
        SORT_BUTTON: (0.12, 0.91),
        SORT_RARITY: (0.12, 0.7),
        SORT_LV: (0.12, 0.77),
        SORT_DATE: (0.12, 0.84),
    }
}
