"""Summarize a debug run's capture_pipeline.csv.

Usage: python tools/analyze_capture_pipeline.py PATH_TO_CSV_OR_DEBUG_FOLDER
"""
import argparse
import csv
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median


def number(row, key):
    value = row.get(key, "")
    return float(value) if value not in (None, "") else None


def truth(value):
    return str(value).lower() in ("1", "true")


def describe(label, values):
    if not values:
        return f"{label}: no observations"
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return (f"{label}: n={len(values)}, avg={mean(values):.3f}, "
            f"median={median(values):.3f}, p95={p95:.3f}, max={max(values):.3f} ms")


def analyze(rows):
    lines = ["Software timestamps only; a byte change is not a measured VSync boundary."]
    metrics = defaultdict(list)
    counts = Counter()
    attempts = defaultdict(list)
    nav_by_target = {}
    accepted = defaultdict(list)
    all_grabs = []
    nav_rows = []
    for row in rows:
        event = row["event"]
        counts[event] += 1
        kind = row.get("item_type", "")
        if event == "dropped":
            lines.append(f"WARNING: truncated trace; dropped records={row.get('decision')}")
        elif event == "nav":
            nav_rows.append(row)
            start, end = number(row, "grab_start_s"), number(row, "grab_end_s")
            target = row.get("target_uid", "")
            if start is not None and end is not None:
                metrics["input call"].append((end - start) * 1000)
                nav_by_target[(kind, target)] = (start, end, row.get('source') or 'unlabelled input')
        elif event == "poll":
            attempt = (kind, row.get("uid"), row.get("capture_id", ""))
            attempts[attempt].append(row)
            if row.get("source") == "cache":
                counts["cached_poll"] += 1
                continue
            all_grabs.append(row)
            start = number(row, "grab_start_s")
            end = number(row, "grab_end_s")
            sig_end = number(row, "signature_end_s")
            if start is not None and end is not None:
                metrics["grab + conversion"].append((end - start) * 1000)
            if end is not None and sig_end is not None:
                metrics["signature + comparisons"].append((sig_end - end) * 1000)
        elif event == "accept":
            counts[f"accept_{row.get('decision', '')}"] += 1
            accepted[kind].append(row)

    all_grabs = sorted((row for row in all_grabs if number(row, "grab_start_s") is not None),
                       key=lambda row: number(row, "grab_start_s"))
    grab_starts = [number(row, "grab_start_s") for row in all_grabs]
    for nav in nav_rows:
        end = number(nav, "grab_end_s")
        if end is None:
            continue
        index = bisect_left(grab_starts, end)
        if index == len(all_grabs):
            continue
        first = all_grabs[index]
        metrics["input end to immediate next grab start"].append((grab_starts[index] - end) * 1000)
        if first.get("uid") == nav.get("uid") and first.get("item_type") == nav.get("item_type"):
            counts["post_input_confirmation_matched" if truth(first.get("equals_candidate"))
                   else "post_input_confirmation_changed"] += 1

    for attempt, polls in attempts.items():
        kind, uid, _ = attempt
        actual = [p for p in polls if p.get("source") != "cache"]
        for before, after in zip(actual, actual[1:]):
            start = number(after, "grab_start_s")
            last_start = number(before, "grab_start_s")
            last_sig = number(before, "signature_end_s")
            if start is not None and last_start is not None:
                metrics["capture start spacing"].append((start - last_start) * 1000)
            if start is not None and last_sig is not None:
                metrics["between comparison and next grab"].append((start - last_sig) * 1000)
        if (kind, uid) not in nav_by_target:
            continue
        _, nav_end, action = nav_by_target[(kind, uid)]
        # A cached poll keeps its original timestamps, which can predate this capture.
        after_nav = [p for p in polls if number(p, "grab_start_s") is not None
                     and number(p, "grab_start_s") >= nav_end]
        if after_nav:
            first = after_nav[0]
            counts["first_after_nav_changed" if not truth(first.get("equals_previous"))
                   else "first_after_nav_unchanged"] += 1
            metrics["input end to first target grab start"].append(
                (number(first, "grab_start_s") - nav_end) * 1000)
        changed = [p for p in after_nav if not truth(p.get("equals_previous"))]
        if changed:
            latency = (number(changed[0], "grab_start_s") - nav_end) * 1000
            metrics["input end to changed grab start"].append(latency)
            try:
                block = (int(uid) - 1) // 200 * 200 + 1
            except (ValueError, TypeError):
                continue
            metrics[f"{kind} UID {block}-{block + 199} {action} input to first change"].append(latency)
            accepts = [r for r in accepted[kind] if r.get('capture_id') == attempt[2]]
            if accepts:
                end = number(accepts[0], 'signature_end_s')
                first = number(changed[0], 'signature_end_s')
                if end is not None and first is not None:
                    metrics[f"{kind} UID {block}-{block + 199} first change to acceptance"].append(
                        (end - first) * 1000)

    lines.append("Events: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for label, values in metrics.items():
        lines.append(describe(label, values))
    for kind, items in accepted.items():
        times = [number(row, "signature_end_s") for row in items]
        times = [stamp for stamp in times if stamp is not None]
        lines.append(describe(f"{kind} acceptance spacing (not whole-scan ms/relic)",
                              [(b - a) * 1000 for a, b in zip(times, times[1:])]))
    lines.append("First-target-grab counts exclude confirmation grabs owned by the prior UID; "
                 "inspect nav/poll chronology for immediate post-input confirmation timing.")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    path = args.path / "capture_pipeline.csv" if args.path.is_dir() else args.path
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    print("\n".join(analyze(rows)))


if __name__ == "__main__":
    main()
