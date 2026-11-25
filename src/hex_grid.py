import math

CUBE_DIRS: list[tuple[int, int, int]] = [
    (1, -1, 0),
    (1, 0, -1),
    (0, 1, -1),
    (-1, 1, 0),
    (-1, 0, 1),
    (0, -1, 1),
]

BOARD_RADII: dict[str, int] = {
    "S (19)": 3,
    "M (37)": 4,
    "L (61)": 5,
}


def generate_cube_coords(radius: int) -> list[tuple[int, int, int]]:
    max_d = radius - 1
    coords: list[tuple[int, int, int]] = []
    for x in range(-max_d, max_d + 1):
        y_min = max(-max_d, -x - max_d)
        y_max = min(max_d, -x + max_d)
        for y in range(y_min, y_max + 1):
            coords.append((x, y, -x - y))
    return coords


def generate_oscillating_coords(radius: int) -> list[tuple[int, int, int]]:
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


def cube_to_pixel(
    x: int, y: int, size: float, cx: float, cy: float
) -> tuple[float, float]:
    px = cx + size * 1.5 * x
    py = cy + size * math.sqrt(3) * (y + x / 2.0)
    return px, py


def hex_vertices(px: float, py: float, radius: float) -> list[float]:
    pts: list[float] = []
    for i in range(6):
        angle = math.radians(60 * i)
        pts.append(px + radius * math.cos(angle))
        pts.append(py + radius * math.sin(angle))
    return pts


def parse_coord_key(key: str) -> tuple[int, int, int]:
    parts = [int(p.strip()) for p in key.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Invalid cube coordinate key: '{key}'")
    return parts[0], parts[1], parts[2]


def format_coord_key(coord: tuple[int, int, int]) -> str:
    return f"{coord[0]},{coord[1]},{coord[2]}"