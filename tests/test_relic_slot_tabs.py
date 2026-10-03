"""Relic slot-tab scanning: tab order, tab ends, reopening and slot integrity."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import test_inventory_capture_integration as fixtures
from config.const import ASPECT_16_9, INVENTORY_FILTER_TABS, INV_TAB
from config.relic_scan import RELIC_NAV_DATA
from enums.scan_mode import ScanMode
from models.const import FILTERS, MIN_RARITY, RELIC_FILTERS, RELIC_SLOT, SORT_LV, SORT_RARITY
from pynput.keyboard import Key
from services.scanner.scanner import HSRScanner, InventoryTabEnd
from utils.duplicate_capture import UnresolvedDuplicateCaptureError
from utils.inventory_tabs import parse_quantity, same_label, selected_tab_index
from utils.scan_integrity import ScanIntegrityError, validate_relic_tab_slots

NAV = RELIC_NAV_DATA[ASPECT_16_9]
SLOTS = ["Head", "Hands", "Body", "Feet", "Planar Sphere", "Link Rope"]
ROW_STARTS = NAV["inventory_row_starts"]


class InventoryTabHelpersTest(unittest.TestCase):
    def strip(self, bright_centres):
        """Synthetic underline strip spanning x=0.10..0.55 of the window."""
        image = Image.new("L", (864, 8), 45)
        for centre in bright_centres:
            left = int((centre - 0.029 - 0.10) / 0.45 * 864)
            image.paste(125, (left, 0, left + int(0.058 / 0.45 * 864), 8))
        return image

    def test_quantity_parse(self):
        self.assertEqual(parse_quantity("2962/3000"), (2962, 3000))
        for text in ("", "2962", "29a2/3000", "3001/3000", "5/0"):
            self.assertIsNone(parse_quantity(text), text)

    def test_single_underlined_tab_is_selected(self):
        centres = [position[0] for _, position in NAV[INVENTORY_FILTER_TABS]]
        for index, centre in enumerate(centres):
            self.assertEqual(selected_tab_index(self.strip([centre]), centres, 0.10, 0.45), index)

    def test_no_or_several_underlines_are_not_a_selection(self):
        centres = [position[0] for _, position in NAV[INVENTORY_FILTER_TABS]]
        self.assertIsNone(selected_tab_index(self.strip([]), centres, 0.10, 0.45))
        self.assertIsNone(selected_tab_index(self.strip(centres[1:3]), centres, 0.10, 0.45))

    def test_labels_compare_without_case_or_spacing(self):
        self.assertTrue(same_label("PlanarSphere", "Planar Sphere"))
        self.assertTrue(same_label(" head", "Head"))
        self.assertFalse(same_label("Hands", "Head"))

    def test_tab_slots_match_exported_slot_names(self):
        self.assertEqual([name for name, _ in NAV[INVENTORY_FILTER_TABS]], ["All"] + SLOTS)


class SlotValidationTest(unittest.TestCase):
    def test_matching_slots_pass_and_mismatch_fails_closed(self):
        relics = [{"_uid": "relic_1", RELIC_SLOT: "Head"}, {"_uid": "relic_2", RELIC_SLOT: "Hands"}]
        self.assertIs(validate_relic_tab_slots(relics, {1: "Head", 2: "Hands"}), relics)
        with self.assertRaises(ScanIntegrityError):
            validate_relic_tab_slots(relics, {1: "Head", 2: "Head"})
        with self.assertRaises(ScanIntegrityError):
            validate_relic_tab_slots(relics, {1: "Head"})

    def test_single_pass_scans_skip_the_check(self):
        relics = [{"_uid": "relic_1", RELIC_SLOT: "Head"}]
        self.assertIs(validate_relic_tab_slots(relics, {}), relics)


class SlotTabLoopTest(unittest.TestCase):
    def scanner(self, quantity, tab_sizes, rarities=None):
        scanner, strategy = fixtures.InventoryLoopTest().scanner()
        del scanner._relic_slot_tab_plan  # use the real plan
        scanner._begin_relic_slot_tab = Mock()
        scanner._reopen_relic_inventory = Mock()
        scanner._config[FILTERS] = {RELIC_FILTERS: {MIN_RARITY: 5}}
        strategy.check_filters.side_effect = lambda stats, filters, uid: (
            {MIN_RARITY: stats["rarity"] >= 5}, stats)
        self.captured = []

        def capture(_strategy, uid, previous):
            tab = scanner._inventory_tab
            position = uid - scanner._inventory_tab_offset
            if position > tab_sizes[SLOTS.index(tab)]:
                raise InventoryTabEnd()
            self.captured.append((uid, tab, position))
            rarity = (rarities or {}).get((tab, position), 5)
            return {"uid": uid, "rarity": rarity}, str(uid).encode()

        scanner._capture_inventory_stats = Mock(side_effect=capture)
        self.quantity = quantity
        return scanner, strategy

    def run_scan(self, scanner, strategy):
        with patch("services.scanner.scanner.image_to_string",
                   side_effect=[f"{self.quantity}/3000", SORT_RARITY]), \
                patch("services.scanner.scanner.time.sleep"):
            return scanner.scan_inventory(strategy)

    def test_tabs_scan_in_order_with_continuous_uids(self):
        sizes = [3, 9, 2, 1, 4, 2]
        scanner, strategy = self.scanner(sum(sizes), sizes)
        shards = self.run_scan(scanner, strategy)
        self.assertEqual([uid for shard in shards for uid, _ in shard], list(range(1, 22)))
        self.assertEqual([call.args[1] for call in scanner._begin_relic_slot_tab.call_args_list], SLOTS)
        self.assertEqual(scanner._reopen_relic_inventory.call_count, 5)
        expected = [slot for slot, size in zip(SLOTS, sizes) for _ in range(size)]
        self.assertEqual([scanner._relic_tab_slots[uid] for uid in range(1, 22)], expected)
        self.assertEqual([tab for _, tab, _ in self.captured], expected)

    def test_row_starts_follow_the_position_within_each_tab(self):
        sizes = [3, 9, 1, 1, 1, 1]
        scanner, strategy = self.scanner(sum(sizes), sizes)
        self.run_scan(scanner, strategy)
        # UID 11 is the Hands tab's 8th relic: its successor is a row-2 click.
        # UID 8 is the Hands tab's 5th relic and must not trigger a click.
        moves = [call.args for call in scanner._nav.move_cursor_to.call_args_list]
        self.assertEqual(moves.count(ROW_STARTS[1]), 1)
        self.assertNotIn(ROW_STARTS[2], moves)
        self.assertEqual(scanner._inventory_tab_offset, 0)

    def test_tab_counts_must_add_up_to_the_quantity(self):
        sizes = [3, 9, 2, 1, 4, 2]
        scanner, strategy = self.scanner(sum(sizes) + 4, sizes)
        with self.assertRaises(ScanIntegrityError):
            self.run_scan(scanner, strategy)

    def test_quantity_reached_skips_remaining_tabs(self):
        sizes = [3, 9, 2, 1, 4, 2]
        scanner, strategy = self.scanner(5, sizes)
        shards = self.run_scan(scanner, strategy)
        self.assertEqual([uid for shard in shards for uid, _ in shard], [1, 2, 3, 4, 5])
        self.assertEqual(scanner._reopen_relic_inventory.call_count, 1)

    def test_rarity_stop_ends_only_its_tab(self):
        sizes = [5, 2, 1, 1, 1, 1]
        scanner, strategy = self.scanner(sum(sizes), sizes, rarities={("Head", 3): 4})
        shards = self.run_scan(scanner, strategy)
        # Head stops at its 3rd relic; Hands starts at UID 4.
        self.assertEqual([uid for shard in shards for uid, _ in shard], [1, 2, 4, 5, 6, 7, 8, 9])
        self.assertEqual(scanner._relic_tab_slots[4], "Hands")

    def test_plan_only_for_normal_relic_scans(self):
        scanner, strategy = fixtures.InventoryLoopTest().scanner()
        del scanner._relic_slot_tab_plan
        self.assertEqual(scanner._relic_slot_tab_plan(strategy, NAV), SLOTS)
        scanner._scan_mode = ScanMode.RECENT_RELICS.value
        self.assertEqual(scanner._relic_slot_tab_plan(strategy, NAV), [])
        scanner._scan_mode = 0
        self.assertEqual(scanner._relic_slot_tab_plan(Mock(), NAV), [])


class TabEndProbeTest(unittest.TestCase):
    def scanner(self, last_position, offset=0, tab="Head"):
        scanner, strategy = fixtures.InventoryLoopTest().scanner()
        scanner._hwnd = 7
        scanner._config["debug"] = False
        scanner._inventory_tab = tab
        scanner._inventory_tab_offset = offset
        self.item_id = offset + last_position + 1
        scanner._inventory_nav_target = self.item_id
        self.responses = []

        def capture(*_args):
            changes, panel = self.responses.pop(0)
            scanner._screenshot.last_capture_signature_changes = changes
            return {"panel": panel}, panel

        scanner._screenshot.screenshot_stats_on_panel_change.side_effect = capture
        return scanner, strategy

    def capture(self, scanner, strategy, foreground=7):
        with patch("services.scanner.scanner.win32gui.GetForegroundWindow", return_value=foreground), \
                patch("services.scanner.scanner.win32gui.GetClassName", return_value="x"):
            return scanner._capture_inventory_stats(strategy, self.item_id, b"last")

    def test_unchanged_panel_and_probe_end_the_tab(self):
        for last_position, probe in ((5, "d"), (8, "s"), (16, "s"), (7, "d")):
            scanner, strategy = self.scanner(last_position, offset=40)
            self.responses = [(0, b"last"), (0, b"last")]
            with self.assertRaises(InventoryTabEnd):
                self.capture(scanner, strategy)
            self.assertEqual([call.args for call in scanner._nav.key_tap.call_args_list], [(probe,)])
            scanner._interruptible_sleep.assert_not_called()

    def test_probe_that_changes_the_panel_aborts(self):
        for probe_changes, probe_panel in ((1, b"next"), (1, b"last")):
            scanner, strategy = self.scanner(5)
            self.responses = [(0, b"last"), (probe_changes, probe_panel)]
            with self.assertRaises(ScanIntegrityError):
                self.capture(scanner, strategy)

    def test_game_not_in_foreground_aborts_without_probe(self):
        scanner, strategy = self.scanner(5)
        self.responses = [(0, b"last")]
        with self.assertRaises(ScanIntegrityError):
            self.capture(scanner, strategy, foreground=8)
        scanner._nav.key_tap.assert_not_called()

    def test_flicker_or_single_pass_uses_normal_recovery(self):
        for tab, first_changes in (("Head", 1), (None, 0)):
            scanner, strategy = self.scanner(5, tab=tab)
            self.responses = [(first_changes, b"last")] + [(0, b"last")] * 3
            with self.assertRaises(UnresolvedDuplicateCaptureError):
                self.capture(scanner, strategy)
            scanner._nav.key_tap.assert_not_called()

    def test_first_relic_of_a_tab_is_never_an_end(self):
        scanner, strategy = self.scanner(5)
        self.responses = [(0, b"first")]
        with patch("services.scanner.scanner.win32gui.GetForegroundWindow", return_value=7):
            stats, panel = scanner._capture_inventory_stats(strategy, self.item_id, None)
        self.assertEqual(panel, b"first")
        scanner._nav.key_tap.assert_not_called()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def perf_counter(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class InventoryReopenTest(unittest.TestCase):
    def scanner(self, readings):
        scanner, _ = fixtures.InventoryLoopTest().scanner()
        del scanner._close_inventory
        scanner._interrupt_event = Mock()
        scanner._interrupt_event.is_set.return_value = False
        self.readings = list(readings)
        last = [None]

        def read():
            if self.readings:
                last[0] = self.readings.pop(0)
            return last[0]

        scanner._read_inventory_quantity = Mock(side_effect=read)
        return scanner

    def run_with_clock(self, function, *args):
        clock = FakeClock()
        with patch("services.scanner.scanner.time.perf_counter", side_effect=clock.perf_counter), \
                patch("services.scanner.scanner.time.sleep", side_effect=clock.sleep):
            function(*args)
        return clock

    def test_close_waits_through_a_stutter(self):
        q = (2962, 3000)
        scanner = self.scanner([q] * 40 + [None, q, None, None, None])
        clock = self.run_with_clock(scanner._close_inventory)
        self.assertEqual(scanner._nav.key_tap.call_args_list[0].args, (Key.esc,))
        self.assertEqual(scanner._nav.key_tap.call_count, 1)
        self.assertGreater(clock.now, 1.9)  # waited through the stuck frames

    def test_close_times_out_closed(self):
        scanner = self.scanner([(2962, 3000)])
        with self.assertRaises(ScanIntegrityError):
            self.run_with_clock(scanner._close_inventory)

    def test_reopen_returns_to_relics(self):
        scanner = self.scanner([None, None, None, None, (40, 1500), (40, 1500), (2962, 3000)])
        self.run_with_clock(scanner._reopen_relic_inventory, NAV, 2962)
        self.assertEqual([call.args for call in scanner._nav.key_tap.call_args_list], [(Key.esc,), ("b",)])
        scanner._nav.move_cursor_to.assert_called_once_with(*NAV[INV_TAB])

    def test_reopen_retries_the_key_only_while_closed(self):
        scanner = self.scanner([None] * 3 + [None] * 205 + [(2962, 3000)])
        self.run_with_clock(scanner._reopen_relic_inventory, NAV, 2962)
        self.assertEqual([call.args for call in scanner._nav.key_tap.call_args_list],
                         [(Key.esc,), ("b",), ("b",)])
        scanner._nav.move_cursor_to.assert_not_called()

    def test_reopen_gives_up_closed(self):
        scanner = self.scanner([None])
        with self.assertRaises(ScanIntegrityError):
            self.run_with_clock(scanner._reopen_relic_inventory, NAV, 2962)


class BeginSlotTabTest(unittest.TestCase):
    def scanner(self, tabs, labels):
        scanner, _ = fixtures.InventoryLoopTest().scanner()
        scanner._interrupt_event = Mock()
        scanner._interrupt_event.is_set.return_value = False
        tabs, labels = list(tabs), list(labels)
        scanner._selected_relic_tab = Mock(side_effect=lambda nav: tabs.pop(0) if len(tabs) > 1 else tabs[0])
        scanner._read_relic_slot_label = Mock(side_effect=lambda: labels.pop(0) if len(labels) > 1 else labels[0])
        scanner._select_first_inventory_item = Mock()
        return scanner

    def begin(self, scanner, sort=SORT_RARITY):
        clock = FakeClock()
        with patch("services.scanner.scanner.time.perf_counter", side_effect=clock.perf_counter), \
                patch("services.scanner.scanner.time.sleep", side_effect=clock.sleep), \
                patch("services.scanner.scanner.image_to_string", return_value=sort):
            scanner._begin_relic_slot_tab(NAV, "Planar Sphere", SORT_RARITY)

    def test_tab_click_is_confirmed_before_first_relic(self):
        scanner = self.scanner(["All", "All", "Planar Sphere"], ["Body", "Planar Sphere"])
        self.begin(scanner)
        scanner._nav.move_cursor_to.assert_called_once_with(*dict(NAV[INVENTORY_FILTER_TABS])["Planar Sphere"])
        scanner._select_first_inventory_item.assert_called_once_with(NAV)

    def test_missed_tab_click_is_retried_once(self):
        scanner = self.scanner(["All"] * 120 + ["Planar Sphere"], ["Planar Sphere"])
        self.begin(scanner)
        self.assertEqual(scanner._nav.click.call_count, 2)

    def test_unconfirmed_tab_sort_or_panel_aborts(self):
        cases = [
            (["All"], ["Planar Sphere"], SORT_RARITY),
            (["Planar Sphere"], ["Planar Sphere"], SORT_LV),
            (["Planar Sphere"], ["Link Rope"], SORT_RARITY),
        ]
        for tabs, labels, sort in cases:
            scanner = self.scanner(tabs, labels)
            with self.assertRaises(ScanIntegrityError):
                self.begin(scanner, sort)


if __name__ == "__main__":
    unittest.main()


class RealCaptureSlotTabTest(unittest.TestCase):
    """Real poll loop, pipelined navigation and tab-end probes against a game model."""

    def run_game(self, sizes, drop_after_uid=None):
        scanner, strategy = fixtures.InventoryLoopTest().scanner()
        del scanner._relic_slot_tab_plan
        scanner._hwnd = 7
        helper = fixtures.CapturePollingTest()
        helper.poll([1], settle=0, debug=False)
        screenshot = helper.last_screenshot

        def stats_on_change(*args):
            result = screenshot.screenshot_stats_on_panel_change(*args)
            scanner._screenshot.last_capture_signature_changes = screenshot.last_capture_signature_changes
            return result

        scanner._screenshot.configure_inventory_capture.side_effect = screenshot.configure_inventory_capture
        scanner._screenshot.screenshot_stats_on_panel_change.side_effect = stats_on_change
        scanner._screenshot.reset_inventory_pipeline.side_effect = screenshot.reset_inventory_pipeline
        scanner._screenshot.remember_inventory_before_navigation.side_effect = screenshot.remember_inventory_before_navigation
        game = {"tab": None, "position": 0, "pending": None, "polls": 0}
        offsets = [sum(sizes[:i]) for i in range(len(sizes))]
        clock = [0.0]
        probes = []

        def size():
            return sizes[SLOTS.index(game["tab"])]

        def request(target):
            self.assertIsNone(game["pending"], "second input before the first arrived")
            game["pending"], game["polls"] = target, 0

        def tap(key):
            if game["tab"] is None or key not in ("d", "s"):
                return  # opening the inventory
            p = game["position"]
            # Normal navigation targets the current UID's successor only after
            # the tap; a tab-end probe is sent once that target is already set.
            if scanner._inventory_nav_target == offsets[SLOTS.index(game["tab"])] + p + 1:
                probes.append((game["tab"], key))
            if drop_after_uid == offsets[SLOTS.index(game["tab"])] + p and not probes:
                return  # the game misses this navigation input
            if key == "d" and p % 8 and p < size():
                request(p + 1)
            if key == "s" and p % 8 == 0 and p < size():
                request(min(p + 8, size()))

        def click():
            if game["tab"] is None:
                return  # relic category tab
            p = game["position"]
            if p % 8 == 0 and p < size():
                request(p + 1)

        def begin(_nav, slot, _sort):
            game.update(tab=slot, position=1, pending=None)

        def grab(_bbox):
            clock[0] += .009
            if game["pending"] is not None:
                game["polls"] += 1
                if game["polls"] == 2:
                    game["position"], game["pending"] = game["pending"], None
            uid = offsets[SLOTS.index(game["tab"])] + game["position"]
            return Image.new("RGB", (480, 842), (uid, 0, 0)), "fake"

        scanner._nav.key_tap.side_effect = tap
        scanner._nav.click.side_effect = click
        scanner._begin_relic_slot_tab = Mock(side_effect=begin)
        scanner._reopen_relic_inventory = Mock()
        screenshot._grab_screenshot = grab
        with patch("services.scanner.scanner.image_to_string", side_effect=[f"{sum(sizes)}/3000", SORT_RARITY]), \
                patch("services.scanner.scanner.time.sleep"), \
                patch("services.scanner.scanner.win32gui.GetForegroundWindow", return_value=7), \
                patch("utils.screenshot.time.perf_counter", side_effect=lambda: clock[0]):
            shards = scanner.scan_inventory(strategy)
        return scanner, shards, probes

    def test_six_tabs_end_by_probe_with_every_relic_once(self):
        sizes = [11, 8, 3, 1, 2, 9]
        scanner, shards, probes = self.run_game(sizes)
        items = [(uid, stats["name"].getpixel((0, 0))[0]) for shard in shards for uid, stats in shard]
        self.assertEqual(items, [(uid, uid) for uid in range(1, sum(sizes) + 1)])
        # The last tab ends on the quantity, so only five tabs need a probe.
        self.assertEqual(probes, [("Head", "d"), ("Hands", "s"), ("Body", "d"),
                                  ("Feet", "d"), ("Planar Sphere", "d")])
        expected = [slot for slot, n in zip(SLOTS, sizes) for _ in range(n)]
        self.assertEqual([scanner._relic_tab_slots[uid] for uid in range(1, sum(sizes) + 1)], expected)

    def test_missed_input_inside_a_tab_aborts_instead_of_ending_it(self):
        with self.assertRaises(ScanIntegrityError):
            self.run_game([11, 8, 3, 1, 2, 9], drop_after_uid=5)
