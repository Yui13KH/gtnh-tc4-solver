"""
Aspect research solver.

Goal: connect every starting aspect on the board into one network, placing as
few / as cheap aspects as possible, without using more of an aspect than the
player has in stock.

How it works
------------
1. A shortest-path search over (hex, aspect) states finds the cheapest chain
   from the current network to the nearest unconnected starting aspect.
   (Repeating this is the classic "shortest path heuristic" for Steiner trees.)
2. Because the result depends on which aspect you start from, we run the whole
   thing once per starting aspect and keep the cheapest result.
3. Every chain is checked: one aspect per hex, and never more of an aspect than
   is in stock. If a chain breaks a rule, the offending state is banned and the
   search retries, instead of failing the whole solve.
4. Finally, placed hexes that turn out to be unnecessary are pruned, using the
   real in-game rule (any two adjacent hexes link if one aspect is a recipe
   parent of the other).
"""

from dataclasses import dataclass, field
import heapq
import json
from pathlib import Path

from src.hex_grid import CUBE_DIRS

Coord = tuple[int, int, int]

# ---- tuning knobs -----------------------------------------------------------
REUSE_COST = 0.05        # tiny cost for walking over hexes already in the network
FREE_STOCK = 6           # below this many in stock, an aspect gets a scarcity penalty
SCARCITY_WEIGHT = 1.5    # penalty per missing unit below FREE_STOCK
MAX_SEEDS = 8            # how many different start aspects to try
MAX_RETRIES = 300        # re-search attempts when a chain breaks a rule


@dataclass
class SolveResult:
    ok: bool
    edges: list[tuple[Coord, Coord]] = field(default_factory=list)
    placed: dict[Coord, str] = field(default_factory=dict)
    remaining_counts: dict[str, int] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    cost: float = 0.0
    used: dict[str, int] = field(default_factory=dict)


# ---- data helpers -----------------------------------------------------------

def load_recipes_and_costs(
    aspects_path: Path, costs_path: Path | None = None
) -> tuple[dict[str, list[str]], dict[str, int]]:
    recipes: dict[str, list[str]] = json.loads(aspects_path.read_text(encoding="utf-8"))
    if costs_path and costs_path.exists():
        costs: dict[str, int] = json.loads(costs_path.read_text(encoding="utf-8"))
    else:
        costs = {k: 1 if v == ["Primal"] else 5 for k, v in recipes.items()}
    return recipes, costs


def get_parents(aspect: str, recipes: dict[str, list[str]]) -> list[str]:
    parents = recipes.get(aspect, [])
    return [] if parents == ["Primal"] else parents


def can_link_aspects(a: str, b: str, recipes: dict[str, list[str]]) -> str | None:
    """Adjacent aspects link only when one is a direct recipe parent of the other."""
    if a == b:
        return None
    if b in get_parents(a, recipes):
        return f"{a} uses {b}"
    if a in get_parents(b, recipes):
        return f"{b} uses {a}"
    return None


def build_links(recipes: dict[str, list[str]]) -> dict[str, set[str]]:
    """links[a] = every aspect that can sit next to `a` and connect to it."""
    links: dict[str, set[str]] = {a: set() for a in recipes}
    for a in recipes:
        for p in get_parents(a, recipes):
            if p != a:
                links.setdefault(a, set()).add(p)
                links.setdefault(p, set()).add(a)
    return links


# ---- cost model -------------------------------------------------------------

def _unit_cost(asp: str, avail: int, costs: dict[str, int]) -> float:
    return float(costs.get(asp, 5)) + max(0.0, (FREE_STOCK - avail) * SCARCITY_WEIGHT)


def solution_score(placed: dict[Coord, str], counts: dict[str, int], costs: dict[str, int]) -> float:
    """Total cost of a set of placements, with scarcity applied as stock runs down."""
    used: dict[str, int] = {}
    total = 0.0
    for asp in placed.values():
        k = used.get(asp, 0)
        total += _unit_cost(asp, int(counts.get(asp, 0)) - k, costs)
        used[asp] = k + 1
    return total


# ---- search -----------------------------------------------------------------

def _dijkstra(active, network, targets, fixed, remaining, costs, links, banned):
    """Cheapest chain from any network hex to any unconnected target. None if impossible."""
    pq: list[tuple[float, Coord, str]] = []
    best: dict[tuple[Coord, str], float] = {}
    parent: dict[tuple[Coord, str], tuple[Coord, str] | None] = {}

    for c, a in network.items():
        best[(c, a)] = 0.0
        parent[(c, a)] = None
        heapq.heappush(pq, (0.0, c, a))

    while pq:
        d, u, ua = heapq.heappop(pq)
        if d > best.get((u, ua), float("inf")):
            continue

        if u in targets and u not in network:
            chain: list[tuple[Coord, str]] = []
            cur: tuple[Coord, str] | None = (u, ua)
            while cur is not None:
                chain.append(cur)
                cur = parent[cur]
            chain.reverse()
            return chain

        ua_links = links.get(ua, ())
        ux, uy, uz = u
        for dx, dy, dz in CUBE_DIRS:
            v = (ux + dx, uy + dy, uz + dz)
            if v not in active:
                continue

            if v in fixed:
                va = fixed[v]
                if va not in ua_links:
                    continue
                options = ((va, REUSE_COST),)
            else:
                options = []
                for va in ua_links:
                    avail = remaining.get(va, 0)
                    if avail <= 0 or (v, va) in banned:
                        continue
                    options.append((va, _unit_cost(va, avail, costs)))

            for va, step in options:
                nd = d + step
                st = (v, va)
                if nd < best.get(st, float("inf")):
                    best[st] = nd
                    parent[st] = (u, ua)
                    heapq.heappush(pq, (nd, v, va))
    return None


def _check_chain(chain, fixed, remaining):
    """Returns a (hex, aspect) state to ban if the chain breaks a rule, else None."""
    seen: set[Coord] = set()
    need: dict[str, int] = {}
    for coord, asp in chain:
        if coord in seen:
            return (coord, asp)          # same hex used twice with different aspects
        seen.add(coord)
        if coord in fixed:
            continue
        need[asp] = need.get(asp, 0) + 1
        if need[asp] > remaining.get(asp, 0):
            return (coord, asp)          # would use more than we have
    return None


def _find_chain(active, network, targets, fixed, remaining, costs, links):
    banned: set[tuple[Coord, str]] = set()
    for _ in range(MAX_RETRIES):
        chain = _dijkstra(active, network, targets, fixed, remaining, costs, links, banned)
        if chain is None:
            return None
        bad = _check_chain(chain, fixed, remaining)
        if bad is None:
            return chain
        banned.add(bad)
    return None


def _build(seed, terminals, active, board_aspects, counts, costs, recipes, links):
    remaining = {k: max(0, int(v)) for k, v in counts.items()}
    fixed: dict[Coord, str] = {c: board_aspects[c] for c in terminals}
    network: dict[Coord, str] = {seed: board_aspects[seed]}
    targets = set(terminals) - {seed}
    placed: dict[Coord, str] = {}
    steps: list[tuple[Coord, str]] = []

    while targets:
        chain = _find_chain(active, network, targets, fixed, remaining, costs, links)
        if chain is None:
            left = sorted(targets)
            return None, [(seed, f"No valid link chain to remaining aspect(s): {left}")]

        for i, (coord, asp) in enumerate(chain):
            if coord in network:
                continue
            reason = can_link_aspects(chain[i - 1][1], asp, recipes) or ""
            if coord in board_aspects and coord in terminals:
                steps.append((coord, f"{coord}: join {asp} ({reason})"))
            else:
                remaining[asp] -= 1
                placed[coord] = asp
                fixed[coord] = asp
                steps.append((coord, f"{coord}: place [{asp}] ({reason})"))
            network[coord] = asp
            targets.discard(coord)

    return placed, steps


# ---- cleanup ----------------------------------------------------------------

def _connected(terminals, occ, links) -> bool:
    start = terminals[0]
    seen = {start}
    stack = [start]
    while stack:
        u = stack.pop()
        ua_links = links.get(occ[u], ())
        ux, uy, uz = u
        for dx, dy, dz in CUBE_DIRS:
            v = (ux + dx, uy + dy, uz + dz)
            if v in occ and v not in seen and occ[v] in ua_links:
                seen.add(v)
                stack.append(v)
    return all(t in seen for t in terminals)


def _prune(placed, terminals, board_aspects, links, costs):
    """Drop placed hexes that aren't needed to keep all starting aspects connected."""
    placed = dict(placed)
    occ = {c: board_aspects[c] for c in terminals}
    occ.update(placed)

    changed = True
    while changed:
        changed = False
        for c in sorted(placed, key=lambda k: -costs.get(placed[k], 5)):
            trial = dict(occ)
            del trial[c]
            if _connected(terminals, trial, links):
                del occ[c]
                del placed[c]
                changed = True
    return placed


def _link_edges(occ, links):
    edges = []
    for c, a in occ.items():
        cx, cy, cz = c
        for dx, dy, dz in CUBE_DIRS:
            v = (cx + dx, cy + dy, cz + dz)
            if v in occ and c < v and occ[v] in links.get(a, ()):
                edges.append((c, v))
    return edges


# ---- public entry point -----------------------------------------------------

def solve(
    coords: list[Coord],
    dead: set[Coord],
    board_aspects: dict[Coord, str],
    counts: dict[str, int],
    costs: dict[str, int],
    recipes: dict[str, list[str]],
) -> SolveResult:
    active = set(coords) - set(dead)
    terminals = [c for c in board_aspects if c in active]
    if len(terminals) < 2:
        return SolveResult(
            ok=False,
            remaining_counts=dict(counts),
            log=["Place at least 2 starting aspects on non-dead hexes."],
        )

    links = build_links(recipes)

    if len(terminals) <= MAX_SEEDS:
        seeds = list(terminals)
    else:
        seeds = [terminals[int(i * len(terminals) / MAX_SEEDS)] for i in range(MAX_SEEDS)]

    best = None
    first_fail: list[str] | None = None
    for seed in seeds:
        placed, steps = _build(seed, terminals, active, board_aspects, counts, costs, recipes, links)
        if placed is None:
            if first_fail is None:
                first_fail = [text for _, text in steps]
            continue
        before = set(placed)
        placed = _prune(placed, terminals, board_aspects, links, costs)
        removed = before - set(placed)
        score = solution_score(placed, counts, costs)
        if best is None or score < best[0]:
            best = (score, seed, placed, [t for c, t in steps if c not in removed], len(removed))

    if best is None:
        return SolveResult(
            ok=False,
            remaining_counts={k: max(0, int(v)) for k, v in counts.items()},
            log=first_fail or ["No valid link chain found."],
        )

    score, seed, placed, step_lines, pruned = best
    occ = {c: board_aspects[c] for c in terminals}
    occ.update(placed)

    used: dict[str, int] = {}
    for asp in placed.values():
        used[asp] = used.get(asp, 0) + 1
    remaining = {k: max(0, int(v)) for k, v in counts.items()}
    for asp, n in used.items():
        remaining[asp] = max(0, remaining.get(asp, 0) - n)

    log = [f"Tried {len(seeds)} start point(s), best starts at {seed} = {board_aspects[seed]}"]
    log += step_lines
    if pruned:
        log.append(f"Removed {pruned} unnecessary placement(s).")

    return SolveResult(
        ok=True,
        edges=_link_edges(occ, links),
        placed=placed,
        remaining_counts=remaining,
        log=log,
        cost=score,
        used=used,
    )