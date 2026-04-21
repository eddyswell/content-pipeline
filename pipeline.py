import sys
import time
import argparse

STEPS = {
    "voices": ("generate_voices", "Voice Generation"),
    "videos": ("animate_batch", "Video Animation"),
    "captions": ("add_captions", "Caption Burning"),
}
STEP_ORDER = ["voices", "videos", "captions"]


def parse_args():
    parser = argparse.ArgumentParser(description="Hook Video Pipeline")
    parser.add_argument(
        "--steps",
        type=str,
        default=None,
        help="Comma-separated steps to run: voices,videos,captions (default: all)",
    )
    return parser.parse_args()


def run_step(step_key: str) -> bool:
    module_name, label = STEPS[step_key]
    import importlib
    mod = importlib.import_module(module_name)
    try:
        mod.run()
        return True
    except SystemExit as e:
        if e.code == 0:
            return True
        print(f"Step '{label}' exited with code {e.code}.")
        return False
    except Exception as e:
        print(f"Step '{label}' raised an exception: {e}")
        return False


def main():
    args = parse_args()

    if args.steps:
        requested = [s.strip() for s in args.steps.split(",")]
        unknown = [s for s in requested if s not in STEPS]
        if unknown:
            print(f"Unknown steps: {unknown}. Valid options: {list(STEPS.keys())}")
            sys.exit(1)
        steps_to_run = [s for s in STEP_ORDER if s in requested]
    else:
        steps_to_run = STEP_ORDER

    print("=== Hook Video Pipeline ===")
    print(f"Steps: {', '.join(steps_to_run)}\n")

    pipeline_start = time.time()

    for step_key in steps_to_run:
        _, label = STEPS[step_key]
        print(f"\n--- Step: {label} ---")
        step_start = time.time()

        success = run_step(step_key)

        elapsed = time.time() - step_start
        print(f"--- {label} completed in {elapsed:.1f}s ---")

        if not success:
            answer = input(f"\nStep '{label}' failed. Continue anyway? (y/n): ").strip().lower()
            if answer != "y":
                print("Pipeline aborted.")
                sys.exit(1)

    total = time.time() - pipeline_start
    print(f"\n=== Pipeline complete in {total:.1f}s ===")


if __name__ == "__main__":
    main()
