import json
from pathlib import Path
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from src.clicker import COORDS_FILE, PLAN_FILE, load_json, run_plan, save_plan
from src.hex_grid import (
    BOARD_RADII,
    cube_to_pixel,
    generate_cube_coords,
    hex_vertices,
)
from src.solver import SolveResult, load_recipes_and_costs, solve

try:
    from PIL import Image as PILImage, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASPECTS_FILE = DATA_DIR / "aspects.json"
COSTS_FILE = DATA_DIR / "aspectsCost.json"
INVENTORY_FILE = DATA_DIR / "inventory.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
ICONS_DIR = BASE_DIR / "assets" / "aspectIcons"

# ---- look -------------------------------------------------------------------
BG = "#0b1020"
PANEL = "#131a2e"
PANEL2 = "#1c2542"
BORDER = "#2a3558"
TEXT = "#e8ecf8"
MUTED = "#8b96b8"
ACCENT = "#4f8cff"
GOOD = "#22c55e"
WARN = "#f59e0b"
BAD = "#ef4444"
DISABLED_BG = "#1a2038"

HEX_EMPTY = ("#161e36", "#2c3960")
HEX_DEAD = ("#080c18", "#1c2440")
HEX_START = ("#262d63", "#8ea2ff")
HEX_PLACED = ("#0e3a2c", GOOD)

FONT = ("Segoe UI", 9)
FONT_B = ("Segoe UI", 9, "bold")
FONT_H = ("Segoe UI", 11, "bold")
FONT_T = ("Segoe UI", 15, "bold")


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _blend(rgb: tuple[int, int, int], base_hex: str, t: float) -> str:
    b = _hex_to_rgb(base_hex)
    mixed = [int(b[i] * (1 - t) + rgb[i] * t) for i in range(3)]
    return "#%02x%02x%02x" % tuple(mixed)


def speed_label(speed: int) -> str:
    if speed <= 3:
        name = "Slow"
    elif speed <= 6:
        name = "Normal"
    elif speed <= 8:
        name = "Fast"
    else:
        name = "Turbo"
    return f"{speed}/10  {name}"


class ResearchBoardGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Aspect Solver")
        self.root.configure(bg=BG)
        self.root.minsize(1100, 720)

        self.recipes, self.costs = load_recipes_and_costs(ASPECTS_FILE, COSTS_FILE)
        self.all_aspects: list[str] = sorted(self.recipes.keys())

        settings = self._load_settings()
        size_default = settings.get("size")
        if size_default not in BOARD_RADII:
            size_default = list(BOARD_RADII.keys())[0]

        self.size_var = tk.StringVar(value=size_default)
        self.selected_aspect = tk.StringVar(
            value="Aer" if "Aer" in self.all_aspects else (self.all_aspects[0] if self.all_aspects else "")
        )
        self.mode_var = tk.StringVar(value="place_aspect")  # place_aspect | erase | dead
        self.search_var = tk.StringVar(value="")
        self.countdown_var = tk.DoubleVar(value=float(settings.get("countdown", 3.0)))
        self.speed_var = tk.IntVar(value=int(settings.get("speed", 5)))

        self.counts: dict[str, int] = self._load_inventory()
        self.count_vars: dict[str, tk.IntVar] = {}
        self.card_buttons: dict[str, tk.Button] = {}

        self.coords: list[tuple[int, int, int]] = []
        self.dead: set[tuple[int, int, int]] = set()
        self.board_aspects: dict[tuple[int, int, int], str] = {}
        self.last_result: SolveResult | None = None

        self.hex_polys: dict[int, tuple[int, int, int]] = {}
        self._icon_src: dict[str, object] = {}
        self._icon_small: dict[str, object] = {}
        self._icon_cache: dict[tuple[str, int], object] = {}
        self._tint: dict[str, str] = {}
        self._clicker_running = False

        self._style()
        self._load_icons()
        self._build_ui()
        self._refresh_aspect_grid()
        self._update_selected_info()
        self._rebuild_board()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- data
    def _load_settings(self) -> dict:
        if SETTINGS_FILE.exists():
            try:
                raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except (ValueError, json.JSONDecodeError):
                pass
        return {}

    def _save_settings(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "speed": int(self.speed_var.get()),
                "countdown": float(self.countdown_var.get()),
                "size": self.size_var.get(),
            }
            SETTINGS_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except (OSError, tk.TclError, ValueError):
            pass

    def _load_inventory(self) -> dict[str, int]:
        loaded: dict[str, int] = {}
        if INVENTORY_FILE.exists():
            try:
                raw = json.loads(INVENTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    loaded = {str(k): max(0, int(v)) for k, v in raw.items()}
            except (ValueError, json.JSONDecodeError):
                loaded = {}
        return {asp: loaded.get(asp, 5) for asp in self.all_aspects}

    def _save_inventory(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {asp: max(0, int(self.counts.get(asp, 0))) for asp in self.all_aspects}
        INVENTORY_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _on_close(self) -> None:
        self._save_settings()
        try:
            self._save_inventory()
        except OSError:
            pass
        self.root.destroy()

    # --------------------------------------------------------------- style
    def _style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=PANEL2, background=PANEL2, foreground=TEXT,
            arrowcolor=TEXT, bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL2)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", PANEL2)],
            selectforeground=[("readonly", TEXT)],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=PANEL2, background=ACCENT, bordercolor=PANEL,
            lightcolor=ACCENT, darkcolor=ACCENT,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=PANEL2, troughcolor=BG, bordercolor=BG, arrowcolor=MUTED,
        )
        self.root.option_add("*TCombobox*Listbox.background", PANEL2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    def _btn(self, parent, text: str, command, kind: str = "ghost", **kw) -> tk.Button:
        palette = {
            "primary": (ACCENT, "#ffffff", "#6ea0ff"),
            "good": (GOOD, "#04210f", "#4ade80"),
            "ghost": (PANEL2, TEXT, "#26325a"),
            "danger": ("#4c1d1d", "#fecaca", "#7f1d1d"),
        }
        bg, fg, hov = palette[kind]
        b = tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=hov, activeforeground=fg,
            relief=tk.FLAT, bd=0, padx=14, pady=7, cursor="hand2", font=FONT_B, **kw,
        )
        b._colors = (bg, fg, hov)  # type: ignore[attr-defined]
        b.bind("<Enter>", lambda _e: b["state"] != "disabled" and b.config(bg=hov))
        b.bind("<Leave>", lambda _e: b["state"] != "disabled" and b.config(bg=bg))
        return b

    def _set_enabled(self, btn: tk.Button, enabled: bool) -> None:
        bg, fg, _hov = btn._colors  # type: ignore[attr-defined]
        if enabled:
            btn.config(state=tk.NORMAL, bg=bg, fg=fg, cursor="hand2")
        else:
            btn.config(state=tk.DISABLED, bg=DISABLED_BG, disabledforeground=MUTED, cursor="arrow")

    # --------------------------------------------------------------- icons
    def _load_icons(self) -> None:
        if not HAS_PIL or not ICONS_DIR.exists():
            return
        for asp in self.all_aspects:
            icon_path = ICONS_DIR / f"{asp.lower()}.png"
            if not icon_path.exists():
                continue
            try:
                with PILImage.open(icon_path) as im:
                    rgba = im.convert("RGBA")
                self._icon_src[asp] = rgba
                self._icon_small[asp] = ImageTk.PhotoImage(rgba.resize((20, 20), PILImage.Resampling.NEAREST))
                px = [p for p in rgba.getdata() if p[3] > 128]
                if px:
                    n = len(px)
                    avg = (sum(p[0] for p in px) // n, sum(p[1] for p in px) // n, sum(p[2] for p in px) // n)
                    self._tint[asp] = _blend(avg, PANEL, 0.35)
            except Exception:
                continue

    def _icon(self, asp: str, size: int):
        key = (asp, size)
        if key not in self._icon_cache:
            src = self._icon_src.get(asp)
            if src is None:
                return None
            self._icon_cache[key] = ImageTk.PhotoImage(src.resize((size, size), PILImage.Resampling.NEAREST))
        return self._icon_cache[key]

    # ------------------------------------------------------------------ ui
    def _filtered_aspects(self) -> list[str]:
        query = self.search_var.get().strip().lower()
        if not query:
            return list(self.all_aspects)
        starts = [a for a in self.all_aspects if a.lower().startswith(query)]
        contains = [a for a in self.all_aspects if query in a.lower() and not a.lower().startswith(query)]
        return starts + contains

    def _build_ui(self) -> None:
        # header
        header = tk.Frame(self.root, bg=PANEL, padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(header, text="Aspect Solver", bg=PANEL, fg=TEXT, font=FONT_T).pack(side=tk.LEFT)
        tk.Label(header, text="  research board auto-solver", bg=PANEL, fg=MUTED, font=FONT).pack(side=tk.LEFT, pady=(6, 0))

        size_box = ttk.Combobox(
            header, textvariable=self.size_var, values=list(BOARD_RADII.keys()), width=9, state="readonly",
        )
        size_box.pack(side=tk.RIGHT)
        tk.Label(header, text="Board size", bg=PANEL, fg=MUTED, font=FONT).pack(side=tk.RIGHT, padx=(0, 8))
        size_box.bind("<<ComboboxSelected>>", lambda _e: self._rebuild_board())

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        # ---------------- left column
        left = tk.Frame(main, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tools = tk.Frame(left, bg=BG)
        tools.pack(fill=tk.X, pady=(0, 8))
        for label, val in [("Paint", "place_aspect"), ("Erase", "erase"), ("Block hex", "dead")]:
            tk.Radiobutton(
                tools, text=label, variable=self.mode_var, value=val, indicatoron=False,
                bg=PANEL2, fg=TEXT, selectcolor=ACCENT, activebackground="#26325a", activeforeground=TEXT,
                relief=tk.FLAT, bd=0, padx=14, pady=6, font=FONT_B, cursor="hand2",
            ).pack(side=tk.LEFT, padx=(0, 6))
        self._btn(tools, "Reset board", lambda: self._rebuild_board(), "danger").pack(side=tk.RIGHT)
        self._btn(tools, "Clear solution", self._clear_solution, "ghost").pack(side=tk.RIGHT, padx=(0, 6))

        self.canvas = tk.Canvas(
            left, width=640, height=440, bg="#0a0f1e", highlightthickness=1, highlightbackground=BORDER,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.draw_board())
        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<Button-3>", self._on_right_click)

        controls = tk.Frame(left, bg=PANEL, padx=12, pady=10)
        controls.pack(fill=tk.X, pady=(8, 0))

        row1 = tk.Frame(controls, bg=PANEL)
        row1.pack(fill=tk.X)
        self.solve_btn = self._btn(row1, "Solve", self._on_solve, "good")
        self.solve_btn.pack(side=tk.LEFT)
        self.run_btn = self._btn(row1, "Run clicker", self._on_run_clicker, "primary")
        self.run_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._set_enabled(self.run_btn, False)
        tk.Label(row1, text="Abort: Esc or F8", bg=PANEL, fg=MUTED, font=FONT).pack(side=tk.RIGHT)

        row2 = tk.Frame(controls, bg=PANEL)
        row2.pack(fill=tk.X, pady=(10, 0))
        tk.Label(row2, text="Click speed", bg=PANEL, fg=TEXT, font=FONT_B).pack(side=tk.LEFT)
        self.speed_lbl = tk.Label(row2, text=speed_label(self.speed_var.get()), bg=PANEL, fg=ACCENT, font=FONT_B, width=12, anchor=tk.W)
        tk.Scale(
            row2, from_=1, to=10, orient=tk.HORIZONTAL, variable=self.speed_var, showvalue=False,
            length=200, bg=PANEL, fg=TEXT, troughcolor=PANEL2, highlightthickness=0, bd=0,
            activebackground=ACCENT, command=lambda _v: self.speed_lbl.config(text=speed_label(int(self.speed_var.get()))),
        ).pack(side=tk.LEFT, padx=(10, 6))
        self.speed_lbl.pack(side=tk.LEFT)

        tk.Label(row2, text="Start delay (s)", bg=PANEL, fg=TEXT, font=FONT_B).pack(side=tk.LEFT, padx=(18, 6))
        tk.Spinbox(
            row2, from_=0.0, to=15.0, increment=0.5, width=5, textvariable=self.countdown_var,
            bg=PANEL2, fg=TEXT, insertbackground=TEXT, buttonbackground=PANEL2, relief=tk.FLAT,
        ).pack(side=tk.LEFT)

        row3 = tk.Frame(controls, bg=PANEL)
        row3.pack(fill=tk.X, pady=(10, 0))
        self.status_lbl = tk.Label(row3, text="Paint the starting aspects, then press Solve.", bg=PANEL, fg=MUTED, font=FONT_B, anchor=tk.W)
        self.status_lbl.pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(row3, orient=tk.HORIZONTAL, mode="determinate", length=180)
        self.progress.pack(side=tk.RIGHT)

        self.log_box = tk.Text(
            left, height=6, bg="#0a0f1e", fg=TEXT, insertbackground=TEXT, font=("Consolas", 9),
            wrap=tk.WORD, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER,
        )
        self.log_box.pack(fill=tk.X, pady=(8, 0))

        # ---------------- right column
        right = tk.Frame(main, width=400, bg=PANEL, padx=12, pady=12)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(12, 0))
        right.pack_propagate(False)

        tk.Label(right, text="Aspects & stock", bg=PANEL, fg=TEXT, font=FONT_H).pack(anchor=tk.W)

        self.info_lbl = tk.Label(right, text="", bg=PANEL2, fg=TEXT, font=FONT_B, anchor=tk.W, justify=tk.LEFT, padx=10, pady=8, compound=tk.LEFT)
        self.info_lbl.pack(fill=tk.X, pady=(8, 8))

        search_row = tk.Frame(right, bg=PANEL)
        search_row.pack(fill=tk.X, pady=(0, 6))
        tk.Entry(
            search_row, textvariable=self.search_var, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
            relief=tk.FLAT, font=FONT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0, 6))
        self._btn(search_row, "Clear", lambda: self.search_var.set(""), "ghost").pack(side=tk.LEFT)
        self.search_var.trace_add("write", lambda *_a: self._refresh_aspect_grid())

        bulk_row = tk.Frame(right, bg=PANEL)
        bulk_row.pack(fill=tk.X, pady=(0, 8))
        self._btn(bulk_row, "All = 5", lambda: self._set_all_counts(5), "ghost").pack(side=tk.LEFT, padx=(0, 4))
        self._btn(bulk_row, "All = 10", lambda: self._set_all_counts(10), "ghost").pack(side=tk.LEFT)
        self._btn(bulk_row, "Save stock", self._on_save_inventory, "primary").pack(side=tk.RIGHT)

        grid_container = tk.Frame(right, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        grid_container.pack(fill=tk.BOTH, expand=True)

        self.grid_canvas = tk.Canvas(grid_container, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(grid_container, orient=tk.VERTICAL, command=self.grid_canvas.yview)
        self.grid_inner = tk.Frame(self.grid_canvas, bg=BG)
        self.grid_inner.bind(
            "<Configure>",
            lambda _e: self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all")),
        )
        self.grid_canvas.create_window((0, 0), window=self.grid_inner, anchor=tk.NW)
        self.grid_canvas.configure(yscrollcommand=scrollbar.set)
        self.grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # mouse wheel only scrolls the palette while the pointer is over it
        def _wheel(e):
            self.grid_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        grid_container.bind("<Enter>", lambda _e: self.grid_canvas.bind_all("<MouseWheel>", _wheel))
        grid_container.bind("<Leave>", lambda _e: self.grid_canvas.unbind_all("<MouseWheel>"))

    # ------------------------------------------------------------- palette
    def _select_aspect(self, asp: str) -> None:
        self.selected_aspect.set(asp)
        self.mode_var.set("place_aspect")
        self._update_selected_info()
        self._highlight_selected_card()

    def _update_selected_info(self) -> None:
        asp = self.selected_aspect.get()
        if not asp:
            return
        parents = [p for p in self.recipes.get(asp, []) if p != "Primal"]
        made = " + ".join(parents) if parents else "primal aspect"
        stock = self.counts.get(asp, 0)
        icon = self._icon_small.get(asp)
        self.info_lbl.config(
            text=f"  {asp}   (stock {stock})\n  {made}",
            image=icon if icon else "",
            compound=tk.LEFT if icon else tk.NONE,
        )

    def _highlight_selected_card(self) -> None:
        current = self.selected_aspect.get()
        for asp, btn in self.card_buttons.items():
            if asp == current:
                btn.config(bg=ACCENT, fg="#ffffff")
            else:
                btn.config(bg=PANEL2, fg=TEXT)

    def _on_single_count_change(self, asp: str) -> None:
        var = self.count_vars.get(asp)
        if var is None:
            return
        try:
            val = max(0, int(var.get()))
        except (ValueError, tk.TclError):
            val = 0
        self.counts[asp] = val
        if asp == self.selected_aspect.get():
            self._update_selected_info()

    def _set_all_counts(self, val: int) -> None:
        for asp in self.all_aspects:
            self.counts[asp] = val
            if asp in self.count_vars:
                self.count_vars[asp].set(val)
        self._save_inventory()
        self._update_selected_info()
        self._append_log(f"Set every aspect's stock to {val}.")

    def _on_save_inventory(self) -> None:
        self._save_inventory()
        self._append_log(f"Saved stock to {INVENTORY_FILE.name}.")

    def _refresh_aspect_grid(self) -> None:
        for child in self.grid_inner.winfo_children():
            child.destroy()
        self.count_vars.clear()
        self.card_buttons.clear()

        cols = 3
        for idx, asp in enumerate(self._filtered_aspects()):
            r, c = divmod(idx, cols)
            card = tk.Frame(self.grid_inner, bg=PANEL, padx=3, pady=3)
            card.grid(row=r, column=c, padx=3, pady=3, sticky="nsew")

            icon = self._icon_small.get(asp)
            pick_btn = tk.Button(
                card, text=f" {asp}", image=icon if icon else "", compound=tk.LEFT if icon else tk.NONE,
                command=lambda a=asp: self._select_aspect(a), bg=PANEL2, fg=TEXT,
                activebackground="#26325a", activeforeground=TEXT,
                relief=tk.FLAT, bd=0, anchor=tk.W, font=("Segoe UI", 8, "bold"), cursor="hand2", pady=4,
            )
            pick_btn.pack(fill=tk.X)
            self.card_buttons[asp] = pick_btn

            ivar = tk.IntVar(value=self.counts.get(asp, 5))
            self.count_vars[asp] = ivar
            tk.Spinbox(
                card, from_=0, to=9999, width=5, textvariable=ivar, bg=BG, fg=TEXT,
                insertbackground=TEXT, buttonbackground=PANEL2, relief=tk.FLAT,
                command=lambda a=asp: self._on_single_count_change(a),
            ).pack(fill=tk.X, pady=(3, 0))
            ivar.trace_add("write", lambda *_a, a=asp: self._on_single_count_change(a))

        self._highlight_selected_card()

    # --------------------------------------------------------------- board
    def _rebuild_board(self) -> None:
        radius = BOARD_RADII[self.size_var.get()]
        self.coords = generate_cube_coords(radius)
        self.dead = set()
        self.board_aspects = {}
        self.last_result = None
        self._set_enabled(self.run_btn, False)
        self._set_status("Paint the starting aspects, then press Solve.", MUTED)
        self.progress["value"] = 0
        self.draw_board()

    def _invalidate_plan(self) -> None:
        """The board changed, so any earlier solution no longer matches it."""
        if self.last_result is not None:
            self.last_result = None
            self._set_enabled(self.run_btn, False)
            self._set_status("Board changed. Press Solve again.", WARN)

    def _clear_solution(self) -> None:
        self.last_result = None
        self._set_enabled(self.run_btn, False)
        self._set_status("Solution cleared.", MUTED)
        self.draw_board()

    def _hit_test(self, event) -> tuple[int, int, int] | None:
        items = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in reversed(items):
            if item in self.hex_polys:
                return self.hex_polys[item]
        return None

    def _apply_left(self, coord: tuple[int, int, int], toggle: bool) -> None:
        mode = self.mode_var.get()
        changed = False

        if mode == "dead":
            if not toggle:
                return
            if coord in self.dead:
                self.dead.remove(coord)
            else:
                self.dead.add(coord)
                self.board_aspects.pop(coord, None)
            changed = True
        elif coord in self.dead:
            return
        elif mode == "place_aspect":
            asp = self.selected_aspect.get()
            if asp and self.board_aspects.get(coord) != asp:
                self.board_aspects[coord] = asp
                changed = True
        elif mode == "erase":
            if coord in self.board_aspects:
                del self.board_aspects[coord]
                changed = True

        if changed:
            self._invalidate_plan()
            self.draw_board()

    def _on_left_click(self, event) -> None:
        coord = self._hit_test(event)
        if coord:
            self._apply_left(coord, toggle=True)

    def _on_left_drag(self, event) -> None:
        coord = self._hit_test(event)
        if coord:
            self._apply_left(coord, toggle=False)

    def _on_right_click(self, event) -> None:
        coord = self._hit_test(event)
        if not coord:
            return
        if coord in self.dead:
            self.dead.remove(coord)
        else:
            self.dead.add(coord)
            self.board_aspects.pop(coord, None)
        self._invalidate_plan()
        self.draw_board()

    # ----------------------------------------------------------- status/log
    def _append_log(self, msg: str) -> None:
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)

    def _set_status(self, text: str, color: str = TEXT) -> None:
        self.status_lbl.config(text=text, fg=color)

    def _set_progress(self, done: int, total: int) -> None:
        self.progress.config(maximum=max(1, total))
        self.progress["value"] = done
        self._set_status(f"Placing {done}/{total}", ACCENT)

    # --------------------------------------------------------------- solve
    def _on_solve(self) -> None:
        self._save_inventory()
        result = solve(
            coords=self.coords,
            dead=self.dead,
            board_aspects=self.board_aspects,
            counts=self.counts,
            costs=self.costs,
            recipes=self.recipes,
        )
        self.last_result = result

        self.log_box.delete("1.0", tk.END)
        for line in result.log:
            self._append_log(line)

        if not result.ok:
            self.last_result = None
            self._set_enabled(self.run_btn, False)
            self._set_status("No solution. Check blocked hexes and your stock.", BAD)
            self.draw_board()
            return

        if result.used:
            summary = ", ".join(f"{n}x {a}" for a, n in sorted(result.used.items(), key=lambda kv: (-kv[1], kv[0])))
            self._append_log(f"Uses: {summary}")

        if not result.placed:
            self._set_enabled(self.run_btn, False)
            self._set_status("Already connected, nothing to place.", GOOD)
            self.draw_board()
            return

        radius = BOARD_RADII[self.size_var.get()]
        save_plan(radius=radius, placements=list(result.placed.items()), plan_path=PLAN_FILE)
        self._set_enabled(self.run_btn, True)
        self.progress["value"] = 0
        self._set_status(f"Solved: {len(result.placed)} placements, ready to run.", GOOD)
        self.draw_board()

    # ------------------------------------------------------------- clicker
    def _on_run_clicker(self) -> None:
        if self._clicker_running:
            return
        if not (self.last_result and self.last_result.ok and self.last_result.placed):
            messagebox.showwarning("No plan", "Press Solve first.")
            return
        if not COORDS_FILE.exists():
            messagebox.showerror("Missing coords.json", f"Could not find {COORDS_FILE}. Run the recorder first.")
            return

        plan = load_json(PLAN_FILE)
        coords = load_json(COORDS_FILE)
        try:
            countdown = max(0.0, float(self.countdown_var.get()))
        except (ValueError, tk.TclError):
            countdown = 3.0
        speed = int(self.speed_var.get())
        total = len(plan.get("placements", []))
        self._save_settings()

        self._clicker_running = True
        self._set_enabled(self.run_btn, False)
        self._set_enabled(self.solve_btn, False)
        self.progress.config(maximum=max(1, total))
        self.progress["value"] = 0
        self._set_status("Starting... switch to Minecraft.", ACCENT)
        # Minimize so this window never covers the game while clicking.
        self.root.iconify()

        def worker() -> None:
            ok = False
            try:
                ok = run_plan(
                    plan=plan,
                    coords=coords,
                    countdown_sec=countdown,
                    speed=speed,
                    dry_run=False,
                    log_fn=lambda m: self.root.after(0, self._append_log, m),
                    progress_fn=lambda i, n: self.root.after(0, self._set_progress, i, n),
                )
            except Exception as exc:  # keep the GUI alive whatever happens
                self.root.after(0, self._append_log, f"ERROR: {exc}")
            finally:
                self.root.after(0, self._finish_clicker, ok)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_clicker(self, ok: bool = False) -> None:
        self._clicker_running = False
        self._set_enabled(self.solve_btn, True)
        self._set_enabled(self.run_btn, bool(self.last_result and self.last_result.placed))
        if ok:
            self._set_status("Finished.", GOOD)
        else:
            self._set_status("Stopped before finishing.", WARN)
        self.root.deiconify()
        self.root.lift()

    # ---------------------------------------------------------------- draw
    def draw_board(self) -> None:
        self.canvas.delete("all")
        self.hex_polys.clear()

        w = max(320, self.canvas.winfo_width())
        h = max(320, self.canvas.winfo_height())
        cx, cy = w / 2.0, h / 2.0
        radius = BOARD_RADII[self.size_var.get()]
        hex_r = min(w, h) / (radius * 4.5)

        centers = {(x, y, z): cube_to_pixel(x, y, hex_r, cx, cy) for x, y, z in self.coords}

        placed = self.last_result.placed if self.last_result else {}
        edges = self.last_result.edges if self.last_result else []

        # pass 1: hexes
        for coord in self.coords:
            px, py = centers[coord]
            verts = hex_vertices(px, py, hex_r * 0.94)
            asp_name = self.board_aspects.get(coord) or placed.get(coord)
            width = 2

            if coord in self.dead:
                fill, outline = HEX_DEAD
            elif coord in self.board_aspects:
                fill, outline = HEX_START
                fill = self._tint.get(asp_name, fill)
                width = 3
            elif coord in placed:
                fill, outline = HEX_PLACED
                fill = self._tint.get(asp_name, fill)
            else:
                fill, outline = HEX_EMPTY

            poly_id = self.canvas.create_polygon(verts, fill=fill, outline=outline, width=width)
            self.hex_polys[poly_id] = coord

        # pass 2: links between connected hexes
        for u, v in edges:
            if u in centers and v in centers:
                x0, y0 = centers[u]
                x1, y1 = centers[v]
                self.canvas.create_line(x0, y0, x1, y1, fill="#34d399", width=3, capstyle=tk.ROUND)

        # pass 3: icons and labels
        icon_size = max(14, int(hex_r * 0.9))
        for coord in self.coords:
            px, py = centers[coord]
            asp_name = self.board_aspects.get(coord) or placed.get(coord)

            if coord in self.dead:
                self.canvas.create_text(px, py, text="X", fill="#3b4666", font=("Segoe UI", max(9, int(hex_r * 0.32)), "bold"))
                continue
            if not asp_name:
                continue

            icon = self._icon(asp_name, icon_size) if HAS_PIL else None
            label = f"[{asp_name}]" if coord in placed else asp_name
            if icon:
                self.canvas.create_image(px, py - hex_r * 0.12, image=icon)
                self.canvas.create_text(
                    px, py + hex_r * 0.46, text=label, fill=TEXT,
                    font=("Segoe UI", max(7, int(hex_r * 0.22)), "bold"),
                )
            else:
                self.canvas.create_text(
                    px, py, text=label, fill=TEXT,
                    font=("Segoe UI", max(8, int(hex_r * 0.26)), "bold"),
                )

        if not self.board_aspects:
            self.canvas.create_text(
                w / 2, h - 14,
                text="Pick an aspect on the right, then click hexes (drag to paint). Right-click blocks a hex.",
                fill=MUTED, font=FONT,
            )


def main() -> None:
    root = tk.Tk()
    if not ASPECTS_FILE.exists():
        messagebox.showerror("Missing aspects.json", f"Could not find {ASPECTS_FILE}")
        root.destroy()
        return
    ResearchBoardGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()