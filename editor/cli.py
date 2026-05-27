"""
editor/cli.py — orchestrator for the editing assistant.

    python -m editor run     <video> [--name N]   transcribe + build cut map (stops for review)
    python -m editor transcribe <video> [--name N] Layer 1 only
    python -m editor plan    <name>                build cut map from transcript
    python -m editor review  <name>                print the cut map summary
    python -m editor render  <name> [--no-captions] [--no-normalize]

The default `run` stops after building the cut map so a human can review (and
edit) cutmap.json before any pixels are rendered — assistant, not autopilot.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import captions as _captions
from . import config
from . import cutmap as _cutmap
from . import render as _render
from . import transcribe as _transcribe

load_dotenv()


def _project_dir(name: str) -> Path:
    return Path(config.PROJECTS_DIR) / name


def _do_transcribe(video: Path, name: str) -> dict:
    if not video.exists():
        sys.exit(f"Video not found: {video}")
    return _transcribe.transcribe(video, _project_dir(name))


def _do_plan(name: str) -> dict:
    pd = _project_dir(name)
    transcript = _transcribe.load_transcript(pd)
    cutmap = _cutmap.plan(transcript)
    _cutmap.save_cutmap(cutmap, pd)
    return cutmap


def _print_review(cutmap: dict, name: str) -> None:
    print("\n" + _cutmap.summarize(cutmap))
    print(
        f"\nReview/edit projects/{name}/cutmap.json "
        "(flip any action, or add \"protect\": true to a cut), then:\n"
        f"    python -m editor render {name}\n"
    )


def cmd_transcribe(args):
    name = args.name or Path(args.video).stem
    _do_transcribe(Path(args.video), name)
    print(f"Transcript → projects/{name}/transcript.json")


def cmd_plan(args):
    cutmap = _do_plan(args.name)
    _print_review(cutmap, args.name)


def cmd_review(args):
    cutmap = _cutmap.load_cutmap(_project_dir(args.name))
    print(_cutmap.summarize(cutmap))


def cmd_render(args):
    pd = _project_dir(args.name)
    cutmap = _cutmap.load_cutmap(pd)
    out_dir = pd / "out"
    edited = out_dir / "edited.mp4"
    _render.render(cutmap, edited, normalize=not args.no_normalize)

    if args.no_captions:
        print(f"\nDone → {edited}")
        return
    transcript = _transcribe.load_transcript(pd)
    captioned = out_dir / "edited_captioned.mp4"
    _captions.add_captions(transcript, cutmap, edited, captioned)
    print(f"\nDone → {captioned}")


def cmd_run(args):
    name = args.name or Path(args.video).stem
    _do_transcribe(Path(args.video), name)
    cutmap = _do_plan(name)
    _print_review(cutmap, name)
    if args.render:
        args.name = name
        args.no_captions = False
        args.no_normalize = False
        cmd_render(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="editor", description="AI-assisted video editing assistant")
    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("transcribe", help="Layer 1: video → transcript.json")
    t.add_argument("video")
    t.add_argument("--name", default=None, help="Project name (default: video filename)")
    t.set_defaults(func=cmd_transcribe)

    pl = sub.add_parser("plan", help="Build cut map from transcript")
    pl.add_argument("name")
    pl.set_defaults(func=cmd_plan)

    rv = sub.add_parser("review", help="Print the cut map summary")
    rv.add_argument("name")
    rv.set_defaults(func=cmd_review)

    rn = sub.add_parser("render", help="Render edited video (+ captions)")
    rn.add_argument("name")
    rn.add_argument("--no-captions", action="store_true")
    rn.add_argument("--no-normalize", action="store_true")
    rn.set_defaults(func=cmd_render)

    ru = sub.add_parser("run", help="Transcribe + plan, then stop for review")
    ru.add_argument("video")
    ru.add_argument("--name", default=None)
    ru.add_argument("--render", action="store_true", help="Also render now (skip manual review)")
    ru.set_defaults(func=cmd_run)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (RuntimeError, FileNotFoundError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
