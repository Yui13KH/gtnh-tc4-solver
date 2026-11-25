import argparse
import ctypes
import json
from pathlib import Path
import threading
import time
from typing import Callable

from src.hex_grid import format_coord_key

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
COORDS_FILE = DATA_DIR / "coords.json"
PLAN_FILE = DATA_DIR / "plan.json"

VK_ESCAPE = 0x1B
VK_F8 = 0x77
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def enable_dpi_awareness() -> None:
    """Real pixels instead of scaled ones. Must run before any window (Tk) is created."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# Runs when this module is imported, which happens before the GUI creates its window.
enable_dpi_awareness()


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor_pos() -> tuple[int, int]:
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def get_screen_size() -> tuple[int, int]:
    return (
        int(ctypes.windll.user32.GetSystemMetrics(0)),
        int(ctypes.windll.user32.GetSystemMetrics(1)),
    )


# ------------------------------------------------------------------ speed
# Speed 1..10. Each row: (hold_delay, drag_steps, drag_step_delay, step_delay)
# Speed 5 matches the old defaults. If drags get missed at high speeds,
# the game is probably running at a low FPS: lower the speed.
_SPEED_POINTS = {
    1: (0.12, 12, 0.020, 0.30),
    5: (0.05, 8, 0.010, 0.12),
    10: (0.02, 4, 0.004, 0.04),
}


def timing_for_speed(speed: float) -> dict:
    speed = max(1.0, min(10.0, float(speed)))
    lo, hi = (1, 5) if speed <= 5 else (5, 10)
    t = (speed - lo) / (hi - lo)
    a, b = _SPEED_POINTS[lo], _SPEED_POINTS[hi]
    mix = [a[i] + (b[i] - a[i]) * t for i in range(4)]
    return {
        "hold_delay": mix[0],
        "drag_steps": max(2, int(round(mix[1]))),
        "drag_step_delay": mix[2],
        "step_delay": mix[3],
    }


# ------------------------------------------------------------------ mouse
def drag_aspect_to_hex(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    hold_delay: float = 0.05,
    drag_steps: int = 8,
    drag_step_delay: float = 0.01,
) -> None:
    user32 = ctypes.windll.user32

    user32.SetCursorPos(int(from_x), int(from_y))
    time.sleep(hold_delay)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    try:
        time.sleep(hold_delay)

        start_x, start_y = get_cursor_pos()
        steps = max(1, int(drag_steps))
        for i in range(1, steps + 1):
            t = i / steps
            user32.SetCursorPos(
                int(round(start_x + (to_x - start_x) * t)),
                int(round(start_y + (to_y - start_y) * t)),
            )
            time.sleep(drag_step_delay)

        user32.SetCursorPos(int(to_x), int(to_y))
        time.sleep(hold_delay)
    finally:
        # Always let go of the mouse button, even if something went wrong.
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def abort_pressed() -> bool:
    return _key_down(VK_ESCAPE) or _key_down(VK_F8)


# ------------------------------------------------------------------ files
def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_plan(
    radius: int,
    placements: list[tuple[tuple[int, int, int], str]],
    plan_path: Path = PLAN_FILE,
) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "radius": radius,
        "placements": [
            {"coord": [c[0], c[1], c[2]], "aspect": asp}
            for c, asp in placements
        ],
    }
    plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return plan_path


# ------------------------------------------------------------------- run
def run_plan(
    plan: dict,
    coords: dict,
    countdown_sec: float = 3.0,
    speed: float = 5,
    dry_run: bool = False,
    log_fn: Callable[[str], None] = print,
    stop_event: threading.Event | None = None,
    progress_fn: Callable[[int, int], None] | None = None,
) -> bool:
    """
    Executes the placements in `plan` using the recorded screen coordinates.
    `speed` is 1 (slowest, safest) to 10 (fastest).
    Abort: Esc, F8, the GUI Stop button (stop_event), or mouse to the top-left corner.
    Returns True if completed, False if aborted or a coordinate is missing.
    """
    radius = int(plan.get("radius", 3))
    placements = plan.get("placements", [])

    if not placements:
        log_fn("Nothing to place, the plan is empty.")
        return True

    aspect_map = coords.get("aspects", {})
    board_map = coords.get("boards", {}).get(str(radius), {})

    resolved_steps = []
    for item in placements:
        raw = item["coord"]
        coord = (int(raw[0]), int(raw[1]), int(raw[2]))
        asp = str(item["aspect"])
        key = format_coord_key(coord)

        asp_xy = aspect_map.get(asp)
        hex_xy = board_map.get(key)

        if not asp_xy or "x" not in asp_xy or "y" not in asp_xy:
            log_fn(f"ERROR: no recorded position for aspect '{asp}'. Re-run the recorder for it.")
            return False
        if not hex_xy or "x" not in hex_xy or "y" not in hex_xy:
            log_fn(f"ERROR: no recorded position for board R={radius} hex ({key}). Run the grid calibration.")
            return False

        resolved_steps.append(
            (coord, asp, (int(asp_xy["x"]), int(asp_xy["y"])), (int(hex_xy["x"]), int(hex_xy["y"])))
        )

    if dry_run:
        log_fn(f"[DRY RUN] {len(resolved_steps)} step(s) for radius={radius}:")
        for coord, asp, a_pt, h_pt in resolved_steps:
            log_fn(f"  {asp} {a_pt} -> hex {coord} {h_pt}")
        return True

    meta = coords.get("meta", {})
    if meta.get("screen_width"):
        cur = get_screen_size()
        if cur != (meta.get("screen_width"), meta.get("screen_height")):
            log_fn(
                f"WARNING: screen is {cur[0]}x{cur[1]} but coordinates were recorded at "
                f"{meta.get('screen_width')}x{meta.get('screen_height')}. Re-record if clicks look off."
            )

    def should_stop() -> bool:
        return abort_pressed() or (stop_event is not None and stop_event.is_set())

    timing = timing_for_speed(speed)
    total = len(resolved_steps)

    if countdown_sec > 0:
        log_fn(f"Switch to Minecraft now. Starting in {countdown_sec:.1f}s... (Esc / F8 to abort)")
        deadline = time.monotonic() + countdown_sec
        while time.monotonic() < deadline:
            if should_stop():
                log_fn("Aborted during countdown.")
                return False
            time.sleep(0.05)

    for idx, (coord, asp, a_pt, h_pt) in enumerate(resolved_steps, start=1):
        if should_stop():
            log_fn(f"Aborted at step {idx}/{total}.")
            return False

        cur_x, cur_y = get_cursor_pos()
        if cur_x <= 2 and cur_y <= 2:
            log_fn("Aborted via top-left corner failsafe.")
            return False

        log_fn(f"[{idx}/{total}] {asp} -> {coord}")
        drag_aspect_to_hex(
            a_pt[0], a_pt[1], h_pt[0], h_pt[1],
            hold_delay=timing["hold_delay"],
            drag_steps=timing["drag_steps"],
            drag_step_delay=timing["drag_step_delay"],
        )
        if progress_fn:
            progress_fn(idx, total)
        time.sleep(timing["step_delay"])

    log_fn("Done, all placements finished.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute research placement plan using recorded coordinates.")
    parser.add_argument("--plan", type=Path, default=PLAN_FILE, help="Path to plan JSON")
    parser.add_argument("--coords", type=Path, default=COORDS_FILE, help="Path to coords JSON")
    parser.add_argument("--countdown", type=float, default=3.0, help="Seconds before clicking starts")
    parser.add_argument("--speed", type=float, default=5, help="Click speed 1 (slow) to 10 (fast)")
    parser.add_argument("--dry-run", action="store_true", help="Print steps and coordinates without clicking")
    args = parser.parse_args()

    plan = load_json(args.plan)
    coords = load_json(args.coords)
    ok = run_plan(
        plan=plan,
        coords=coords,
        countdown_sec=args.countdown,
        speed=args.speed,
        dry_run=args.dry_run,
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()