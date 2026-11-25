import argparse
import ctypes
import json
from pathlib import Path
import time

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASPECTS_FILE = DATA_DIR / "aspects.json"
COORDS_FILE = DATA_DIR / "coords.json"

VK_F1 = 0x70      # Record current cursor (x, y)
VK_F2 = 0x71      # Skip single item
VK_F3 = 0x72      # Skip entire current section (remaining aspects or grid calibration)
VK_ESCAPE = 0x1B  # Save and exit

# How many hexes away from the center we click during grid calibration.
# Bigger = more accurate (a 1px click error gets divided by K). Max 4 (large board).
CALIB_K = 4

ASPECT_RECORD_ORDER = [
    "Ordo",
    "Terra",
    "Aqua",
    "Permutatio",
    "Sano",
    "Metallum",
    "Victus",
    "Vitreus",
    "Tempus",
    "Iter",
    "Mortuus",
    "Herba",
    "Spiritus",
    "Caelum",
    "Bestia",
    "Limus",
    "Magneto",
    "Aequalitas",
    "Corpus",
    "Humanus",
    "Tutamen",
    "Luxuria",
    "Perfodio",
    "Instrumentum",
    "Machina",
    "Gloria",
    "Messis",
    "Lucrum",
    "Fabrico",
    "Pannus",
    "Tabernus",
    "Electrum",
    "Meto",
    "Nebrisum",
    "Perditio",
    "Ignis",
    "Aer",
    "Potentia",
    "Vacuos",
    "Venenum",
    "Motus",
    "Gelum",
    "Lux",
    "Tempestas",
    "Vinculum",
    "Volatus",
    "Fames",
    "Radio",
    "Primordium",
    "Praecantatio",
    "Arbor",
    "Tenebrae",
    "Vitium",
    "Infernus",
    "Cognitio",
    "Superbia",
    "Auram",
    "Exanimis",
    "Gula",
    "Sensus",
    "Astrum",
    "Alienis",
    "Vesania",
    "Invidia",
    "Desidia",
    "Strontio",
    "Telum",
    "Ira",
    "Terminus",
]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def enable_dpi_awareness() -> None:
    """Make cursor coordinates real pixels, not scaled ones. Call before anything else."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_cursor_pos() -> tuple[int, int]:
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def get_screen_size() -> tuple[int, int]:
    return (
        int(ctypes.windll.user32.GetSystemMetrics(0)),
        int(ctypes.windll.user32.GetSystemMetrics(1)),
    )


def key_pressed(vk_code: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)


def generate_oscillating_coords(radius: int) -> list[tuple[int, int, int]]:
    """
    Left-to-right column sweep for flat-top hexes, alternating vertical
    direction each column so movement stays continuous.
    """
    max_d = radius - 1
    coords: list[tuple[int, int, int]] = []

    for col_idx, x in enumerate(range(-max_d, max_d + 1)):
        y_min = max(-max_d, -x - max_d)
        y_max = min(max_d, -x + max_d)
        ys = list(range(y_min, y_max + 1))
        if col_idx % 2 == 0:
            ys.reverse()
        for y in ys:
            coords.append((x, y, -x - y))

    return coords


def load_coords() -> dict:
    if COORDS_FILE.exists():
        data = json.loads(COORDS_FILE.read_text(encoding="utf-8"))
    else:
        data = {}

    data.setdefault("aspects", {})
    data.setdefault("boards", {})
    for r in ("3", "4", "5"):
        data["boards"].setdefault(r, {})
    return data


def save_coords(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COORDS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_aspect_order(recipe_keys: list[str]) -> list[str]:
    by_lower = {k.lower(): k for k in recipe_keys}
    ordered: list[str] = []
    seen: set[str] = set()

    for raw in ASPECT_RECORD_ORDER:
        canonical = by_lower.get(raw.strip().lower())
        if canonical and canonical not in seen:
            ordered.append(canonical)
            seen.add(canonical)

    for key in sorted(recipe_keys):
        if key not in seen:
            ordered.append(key)
            seen.add(key)

    return ordered


def wait_for_target(
    prompt: str,
    section_name: str,
    existing: dict | None = None,
) -> tuple[str, tuple[int, int] | None]:
    existing_str = f" [current: ({existing['x']}, {existing['y']})]" if existing else ""
    print(f"\n-> {prompt}{existing_str}")
    print(f"   [F1] Record   [F2] Skip item   [F3] Skip '{section_name}'   [Esc] Save & exit")

    while (
        key_pressed(VK_F1)
        or key_pressed(VK_F2)
        or key_pressed(VK_F3)
        or key_pressed(VK_ESCAPE)
    ):
        time.sleep(0.03)

    while True:
        if key_pressed(VK_ESCAPE):
            return "quit", None
        if key_pressed(VK_F3):
            return "skip_section", None
        if key_pressed(VK_F2):
            return "skip_item", None
        if key_pressed(VK_F1):
            x, y = get_cursor_pos()
            return "record", (x, y)
        time.sleep(0.03)


# ---------------------------------------------------------------- aspects
# (unchanged: aspects are still recorded one by one, by hand)

def record_aspects(data: dict, only_aspect: str | None = None) -> str:
    """Returns 'done', 'skip_section', or 'quit'."""
    recipes = json.loads(ASPECTS_FILE.read_text(encoding="utf-8"))
    ordered_names = _resolve_aspect_order(list(recipes.keys()))

    if only_aspect:
        match = next(
            (n for n in ordered_names if n.lower() == only_aspect.strip().lower()),
            None,
        )
        if not match:
            print(f"Aspect '{only_aspect}' not found in {ASPECTS_FILE}")
            return "done"
        targets = [match]
    else:
        targets = ordered_names

    for idx, asp in enumerate(targets, start=1):
        action, pos = wait_for_target(
            f"Aspect ({idx}/{len(targets)}): {asp}",
            section_name="aspects",
            existing=data["aspects"].get(asp),
        )
        if action == "quit":
            return "quit"
        if action == "skip_section":
            print("   Skipped remaining aspects.")
            return "skip_section"
        if action == "record" and pos is not None:
            data["aspects"][asp] = {"x": pos[0], "y": pos[1]}
            save_coords(data)
            print(f"   Saved {asp} -> {pos}")
        else:
            print(f"   Skipped {asp}")

    return "done"


# ------------------------------------------------------------------- grid
# Every hex is: origin + x*A + y*B   (A = pixel step for x+1, B = pixel step for y+1)
# Layout assumed: flat-top hexes, y grows DOWN the screen, x grows to the right.

def hex_to_screen(x: int, y: int, origin, A, B) -> tuple[int, int]:
    return (
        round(origin[0] + x * A[0] + y * B[0]),
        round(origin[1] + x * A[1] + y * B[1]),
    )


def calibrate_grid(data: dict) -> str:
    """3 clicks -> computes every hex on all 3 boards. Returns 'done', 'skip_section', or 'quit'."""
    k = CALIB_K
    print("\n=== GRID CALIBRATION (use the LARGE board, 3 clicks) ===")

    steps = [
        ("Click the CENTER hex of the board  (0,0,0)", "center"),
        (f"Click the hex {k} steps straight DOWN from the center (the bottom corner of the board)  (0,{k},{-k})", "down"),
        (f"Click the hex {k} steps toward the LOWER-RIGHT corner of the board  ({k},0,{-k})", "right"),
    ]

    points: dict[str, tuple[int, int]] = {}
    for prompt, name in steps:
        action, pos = wait_for_target(prompt, section_name="grid")
        if action == "quit":
            return "quit"
        if action != "record" or pos is None:
            print("   Grid calibration skipped, nothing changed.")
            return "skip_section"
        points[name] = pos

    cx, cy = points["center"]
    B = ((points["down"][0] - cx) / k, (points["down"][1] - cy) / k)
    A = ((points["right"][0] - cx) / k, (points["right"][1] - cy) / k)
    origin = (cx, cy)

    data["grid"] = {"origin": list(origin), "A": list(A), "B": list(B)}
    sw, sh = get_screen_size()
    data["meta"] = {"screen_width": sw, "screen_height": sh}

    # Fill the boards in the old format so solver/clicker keep working unchanged.
    for radius in (3, 4, 5):
        board = {}
        for x, y, z in generate_oscillating_coords(radius):
            sx, sy = hex_to_screen(x, y, origin, A, B)
            board[f"{x},{y},{z}"] = {"x": sx, "y": sy}
        data["boards"][str(radius)] = board

    save_coords(data)
    print(f"   Saved grid: origin={origin}, A=({A[0]:.2f}, {A[1]:.2f}), B=({B[0]:.2f}, {B[1]:.2f})")
    print("   Tip: run with --verify to watch the mouse visit every hex.")
    return "done"


def verify_grid(data: dict, radius: int) -> None:
    """Moves the mouse over every hex so you can see if the math lines up."""
    board = data["boards"].get(str(radius), {})
    if not board:
        print(f"No board data for radius {radius}. Run the grid calibration first.")
        return
    print(f"Visiting {len(board)} hexes (R={radius}). Hold Esc to stop.")
    for x, y, z in generate_oscillating_coords(radius):
        if key_pressed(VK_ESCAPE):
            print("Stopped.")
            return
        pos = board.get(f"{x},{y},{z}")
        if not pos:
            continue
        ctypes.windll.user32.SetCursorPos(int(pos["x"]), int(pos["y"]))
        time.sleep(0.12)
    print("Done.")


def main() -> None:
    enable_dpi_awareness()  # must run before any cursor reads

    parser = argparse.ArgumentParser(description="Record aspect coordinates and calibrate the hex grid.")
    parser.add_argument(
        "--mode",
        choices=["all", "aspects", "grid"],
        default="all",
        help="What to record (default: all)",
    )
    parser.add_argument("--skip-aspects", action="store_true", help="Skip recording aspects")
    parser.add_argument("--skip-grid", action="store_true", help="Skip grid calibration")
    parser.add_argument("--aspect", type=str, default=None, help="Re-record a single aspect by name")
    parser.add_argument(
        "--verify",
        type=int,
        nargs="?",
        const=5,
        default=None,
        metavar="RADIUS",
        help="Move the mouse over every hex of a board (3, 4 or 5; default 5) and exit",
    )
    args = parser.parse_args()

    data = load_coords()

    if args.verify is not None:
        verify_grid(data, args.verify)
        return

    if args.mode in ("all", "aspects") and not args.skip_aspects:
        if not ASPECTS_FILE.exists():
            raise FileNotFoundError(f"Missing {ASPECTS_FILE}")
        status = record_aspects(data, only_aspect=args.aspect)
        if status == "quit":
            save_coords(data)
            print("Saved and exited.")
            return

    if args.mode in ("all", "grid") and not args.skip_grid:
        status = calibrate_grid(data)
        if status == "quit":
            save_coords(data)
            print("Saved and exited.")
            return

    save_coords(data)
    print(f"\nDone. Coordinates written to {COORDS_FILE}")


if __name__ == "__main__":
    main()