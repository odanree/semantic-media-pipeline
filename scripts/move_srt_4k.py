"""
Move .srt subtitles from a source dir into the 4K dir, renamed to match their
4K .mp4 counterpart (so players auto-load them next to the video).

Matching reuses the product-code logic from repoint_paths_4k.py:
    ABC-123 Some Title.srt  ->  abc123.4K Some Title.srt   (matches the 4K mp4 stem)

Paths are supplied via args or env vars (no paths are hardcoded):
    SRT_SRC_DIR  source dir holding the subtitles (required)
    FOURK_DIR    4K dir to move them into (required)

Categories:
  MOVE        clean code match, target name free in I:           -> moved
  REVIEW      srt name has a variant suffix (-ub / -B / hash)    -> only with --include-variants
  CONFLICT    >1 srt maps to the same target name                -> never auto-moved
  EXISTS      target .srt already present in I:                  -> skipped (no overwrite)
  NO-MATCH    no 4K mp4 with that code                           -> left in place

Default is DRY-RUN. Add --apply to move files.

Usage:
    python scripts/move_srt_4k.py
    python scripts/move_srt_4k.py --apply
    python scripts/move_srt_4k.py --apply --include-variants
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

# Reuse parse_name / build_fourk_index from the repoint script
_spec = importlib.util.spec_from_file_location(
    "repoint_paths_4k", str(Path(__file__).with_name("repoint_paths_4k.py")))
_rp = importlib.util.module_from_spec(_spec)
sys.modules["repoint_paths_4k"] = _rp  # needed so @dataclass can resolve the module
_spec.loader.exec_module(_rp)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-variants", action="store_true")
    ap.add_argument("--src-dir", default=os.getenv("SRT_SRC_DIR"),
                    help="Source dir holding the subtitles (or set SRT_SRC_DIR)")
    ap.add_argument("--fourk-dir", default=os.getenv("FOURK_DIR"),
                    help="4K dir to move subtitles into (or set FOURK_DIR)")
    args = ap.parse_args()
    if not args.src_dir or not args.fourk_dir:
        raise SystemExit("Set --src-dir/--fourk-dir (or SRT_SRC_DIR/FOURK_DIR env vars).")

    fourk, _dupes = _rp.build_fourk_index(args.fourk_dir)           # code -> 4K mp4 filename
    fourk_stem = {code: Path(name).stem for code, name in fourk.items()}
    existing_srt = {p.name.lower() for p in Path(args.fourk_dir).glob("*.srt")}

    srcs = sorted(Path(args.src_dir).glob("*.srt"))

    # First pass: resolve each srt to a target name + flags
    resolved = []  # (src_path, target_name, parsed)
    for s in srcs:
        parsed = _rp.parse_name(s.stem)
        if not parsed or parsed.code not in fourk_stem:
            resolved.append((s, None, parsed))
            continue
        target = fourk_stem[parsed.code] + ".srt"
        resolved.append((s, target, parsed))

    # Detect conflicts: >1 source mapping to the same target
    target_counts: dict[str, int] = {}
    for _s, t, _p in resolved:
        if t:
            target_counts[t] = target_counts.get(t, 0) + 1

    move, review, conflict, exists, nomatch = [], [], [], [], []
    for s, target, parsed in resolved:
        if not target:
            nomatch.append(s.name)
        elif target.lower() in existing_srt:
            exists.append((s.name, target))
        elif target_counts[target] > 1:
            conflict.append((s.name, target))
        elif parsed.has_variant:
            review.append((s.name, target))
        else:
            move.append((s.name, target))

    def show(title, rows):
        print(f"\n=== {title} ({len(rows)}) ===")
        for r in rows:
            print(f"  {r[0]}" + (f"\n      -> {r[1]}" if isinstance(r, tuple) else ""))

    show("MOVE (clean -> rename to 4K name)", move)
    show("REVIEW (variant srt — needs --include-variants)", review)
    show("CONFLICT (>1 srt -> same target — skipped)", conflict)
    show("EXISTS in I: already (skipped)", exists)
    show("NO 4K match (left in place)", nomatch)

    to_move = list(move) + (review if args.include_variants else [])
    print(f"\nWould move {len(to_move)} srt "
          f"({len(move)} clean{' + %d review' % len(review) if args.include_variants else ''}).")

    if not args.apply:
        print("DRY-RUN — nothing moved. Re-run with --apply.")
        return

    import shutil
    src_dir, dst_dir = Path(args.src_dir), Path(args.fourk_dir)
    for name, target in to_move:
        dst = dst_dir / target
        if dst.exists():
            print(f"  SKIP (appeared): {target}")
            continue
        shutil.move(str(src_dir / name), str(dst))
        print(f"  moved: {name} -> {target}")
    print(f"\nMoved {len(to_move)} srt.")


if __name__ == "__main__":
    main()
