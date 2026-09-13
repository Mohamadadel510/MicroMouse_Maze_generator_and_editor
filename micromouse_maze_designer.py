#!/usr/bin/env python3
"""
Micromouse Maze Designer
=========================

A local desktop app (Tkinter, standard library only) for designing or
randomly generating micromouse test mazes, exporting/importing them as
plain .txt files, and optionally asking an LLM (Claude) questions about
the current maze via the Anthropic API.

Run:
    python micromouse_maze_designer.py

Requirements:
    - Python 3.8+
    - Tkinter (bundled with most Python installers; on Debian/Ubuntu:
      `sudo apt install python3-tk` if it's missing)
    - No third-party packages required. The "Ask AI" panel uses the
      standard library's urllib to call the Anthropic API directly.

To use "Ask AI":
    - Pick a provider in the dropdown (Claude, Gemini, or Groq) and get
      an API key for it:
        Claude: https://console.anthropic.com
        Gemini: https://aistudio.google.com/apikey
        Groq:   https://console.groq.com/keys
    - Either paste the key into the "API key" field in the app, or set
      it once as an environment variable before launching (picked up
      automatically when you select that provider):
        export ANTHROPIC_API_KEY="sk-ant-..."
        export GEMINI_API_KEY="..."
        export GROQ_API_KEY="gsk_..."
    - The key is only kept in memory for the running session. It is
      never written to the maze .txt file or to disk by this app.

Maze file format (.txt):
    Plain ASCII maze art, e.g. for a 4x4 maze:

        # size: 4
        # start: 3,0
        # goal: 1,1 1,2 2,1 2,2
        +---+---+---+---+
        |               |
        +   +---+---+   +
        |   |   G   |   |
        +   +   G   +   +
        |   |   G   G   |
        +   +---+   +   +
        |S              |
        +---+---+---+---+

    The three "#" header lines are metadata (size, start cell, goal
    cell(s), as row,col with row 0 at the top). The grid below encodes
    walls: a "-" between "+" marks a wall on that horizontal edge, a
    "|" marks a wall on that vertical edge. This app can both write and
    read this format, so a saved file can be reloaded later or handed
    to another tool/teammate.

Maze size and start/goal placement:
    - Size can be 4x4, 8x8, 16x16, or 32x32.
    - Start and goal are both user-settable via the "Click mode" dropdown
      ("Set Start" / "Set Goal"), not fixed to the corner/center. The
      goal can be any shape, but its cells must form a single connected
      block (Generate will refuse otherwise) so it comes out as one
      sealed chamber with exactly one entrance.
"""

import json
import os
import random
import threading
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ----------------------------------------------------------------------
# Maze model
#
# For an N x N maze:
#   h_walls[r][c] : wall on the TOP edge of cell (r, c), r in 0..N (N+1 rows)
#   v_walls[r][c] : wall on the LEFT edge of cell (r, c), c in 0..N (N+1 cols)
# Start is a single (row, col) cell; goal is a set of one or more cells
# that must form a single 4-connected block. Both are user-settable — the
# functions below only supply sensible defaults (corner start, center
# goal) for a freshly sized/cleared maze.
# ----------------------------------------------------------------------


def empty_grids(n):
    h_walls = [[False] * n for _ in range(n + 1)]
    v_walls = [[False] * (n + 1) for _ in range(n)]
    return h_walls, v_walls


def set_border(n, h_walls, v_walls):
    for c in range(n):
        h_walls[0][c] = True
        h_walls[n][c] = True
    for r in range(n):
        v_walls[r][0] = True
        v_walls[r][n] = True


def default_start(n):
    return (n - 1, 0)  # bottom-left corner


def default_goal_cells(n):
    mid = n // 2
    if n % 2 == 1:
        return [(mid, mid)]
    return [(mid - 1, mid - 1), (mid - 1, mid), (mid, mid - 1), (mid, mid)]


def is_contiguous(cells):
    """True if `cells` (an iterable of (r, c) tuples) form a single
    4-connected block. An empty set or single cell counts as contiguous."""
    cells = set(cells)
    if len(cells) <= 1:
        return True
    start = next(iter(cells))
    seen = {start}
    stack = [start]
    while stack:
        r, c = stack.pop()
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if (nr, nc) in cells and (nr, nc) not in seen:
                seen.add((nr, nc))
                stack.append((nr, nc))
    return seen == cells


def generate_maze(n, start, goal_set):
    """Recursive-backtracker perfect maze, with one twist: all goal cells
    are treated as a single node in the spanning tree. That guarantees the
    goal room ends up as one open chamber (no internal walls) connected to
    the rest of the maze by exactly one doorway, with every other cell
    still fully reachable — walls are only ever removed, never re-added,
    so nothing gets cut off after the fact.

    `start` is a (row, col) tuple; `goal_set` is an iterable of (row, col)
    tuples that must form a single 4-connected block (required so the
    "one open chamber" guarantee corresponds to a maze a robot can
    actually navigate). Raises ValueError if that's not the case, or if
    start overlaps the goal."""
    goal_set = set(goal_set)
    if not goal_set:
        raise ValueError("Set at least one goal cell first.")
    if not is_contiguous(goal_set):
        raise ValueError("Goal cells must form a single connected block.")
    if start in goal_set:
        raise ValueError("Start cell can't also be a goal cell.")

    h_walls, v_walls = empty_grids(n)
    for r in range(1, n):
        for c in range(n):
            h_walls[r][c] = True
    for r in range(n):
        for c in range(1, n):
            v_walls[r][c] = True
    set_border(n, h_walls, v_walls)

    visited = [[False] * n for _ in range(n)]
    region_visited = False
    visited[start[0]][start[1]] = True
    stack = [start]

    def neighbors(r, c):
        opts = []
        if r > 0 and not visited[r - 1][c]:
            opts.append((r - 1, c, "up"))
        if r < n - 1 and not visited[r + 1][c]:
            opts.append((r + 1, c, "down"))
        if c > 0 and not visited[r][c - 1]:
            opts.append((r, c - 1, "left"))
        if c < n - 1 and not visited[r][c + 1]:
            opts.append((r, c + 1, "right"))
        return opts

    def carve(r, c, direction):
        if direction == "up":
            h_walls[r][c] = False
        elif direction == "down":
            h_walls[r + 1][c] = False
        elif direction == "left":
            v_walls[r][c] = False
        elif direction == "right":
            v_walls[r][c + 1] = False

    while stack:
        r, c = stack[-1]
        opts = neighbors(r, c)
        if not opts:
            stack.pop()
            continue
        nr, nc, direction = random.choice(opts)

        if (nr, nc) in goal_set:
            # Enter the goal room through exactly one doorway, then treat
            # it as a dead end: don't push it onto the stack, so it can
            # never branch out to a second doorway.
            carve(r, c, direction)
            for gr, gc in goal_set:
                visited[gr][gc] = True
            region_visited = True
            continue

        carve(r, c, direction)
        visited[nr][nc] = True
        stack.append((nr, nc))

    # Open up the goal room's interior (it's one chamber, not separate cells).
    for r, c in goal_set:
        if (r - 1, c) in goal_set:
            h_walls[r][c] = False
        if (r, c - 1) in goal_set:
            v_walls[r][c] = False

    assert region_visited, "goal room ended up disconnected — this shouldn't happen"
    return h_walls, v_walls


def maze_to_num_format(n, h_walls, v_walls):
    """mms simulator's "num" format: one line per cell, "X Y N E S W",
    where X/Y use mms's coordinate system (X right, Y up, origin at the
    bottom-left) and N/E/S/W are 1 if a wall is present on that side.
    Verified against the exact example in the mms README."""
    lines = []
    for x in range(n):
        for y in range(n):
            r = n - 1 - y  # our row 0 is the top; mms Y 0 is the bottom
            c = x
            north = 1 if h_walls[r][c] else 0
            south = 1 if h_walls[r + 1][c] else 0
            west = 1 if v_walls[r][c] else 0
            east = 1 if v_walls[r][c + 1] else 0
            lines.append(f"{x} {y} {north} {east} {south} {west}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# .txt (de)serialization
# ----------------------------------------------------------------------


def maze_to_text(n, h_walls, v_walls, start, goals):
    sr, sc = start

    lines = []
    lines.append(f"# size: {n}")
    lines.append(f"# start: {sr},{sc}")
    lines.append("# goal: " + " ".join(f"{r},{c}" for r, c in goals))

    goal_set = set(goals)

    for r in range(n + 1):
        row = "+"
        for c in range(n):
            row += "---" if h_walls[r][c] else "   "
            row += "+"
        lines.append(row)

        if r < n:
            row = "|" if v_walls[r][0] else " "
            for c in range(n):
                if (r, c) == (sr, sc):
                    cell = " S "
                elif (r, c) in goal_set:
                    cell = " G "
                else:
                    cell = "   "
                row += cell
                row += "|" if v_walls[r][c + 1] else " "
            lines.append(row)

    return "\n".join(lines) + "\n"


def text_to_maze(text):
    """Parse the ASCII format produced by maze_to_text back into
    (n, h_walls, v_walls, start, goals). Raises ValueError on malformed
    input. Falls back to this app's usual defaults for start/goal if
    those header lines are missing (e.g. a hand-edited file)."""
    raw_lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    header_lines = [ln for ln in raw_lines if ln.lstrip().startswith("#")]
    grid_lines = [ln for ln in raw_lines if not ln.lstrip().startswith("#")]
    if not grid_lines:
        raise ValueError("No maze grid found in file.")

    n = (len(grid_lines) - 1) // 2
    if n <= 0 or len(grid_lines) != 2 * n + 1:
        raise ValueError("Maze grid has an unexpected number of rows.")

    h_walls, v_walls = empty_grids(n)

    for r in range(n + 1):
        line = grid_lines[2 * r]
        for c in range(n):
            seg = line[1 + c * 4 : 1 + c * 4 + 3] if len(line) >= 4 + c * 4 else "   "
            h_walls[r][c] = "-" in seg

    for r in range(n):
        line = grid_lines[2 * r + 1]
        for c in range(n + 1):
            ch = line[c * 4] if len(line) > c * 4 else " "
            v_walls[r][c] = ch == "|"

    def parse_cells(text_after_colon):
        cells = []
        for token in text_after_colon.split():
            r_str, c_str = token.split(",")
            cells.append((int(r_str), int(c_str)))
        return cells

    start = None
    goals = None
    for ln in header_lines:
        body = ln.lstrip("#").strip()
        if body.startswith("start:"):
            parsed = parse_cells(body[len("start:") :])
            if parsed:
                start = parsed[0]
        elif body.startswith("goal:"):
            goals = parse_cells(body[len("goal:") :])

    if start is None:
        start = default_start(n)
    if not goals:
        goals = default_goal_cells(n)

    return n, h_walls, v_walls, start, goals


# ----------------------------------------------------------------------
# LLM API calls (standard library only) — Claude, Gemini, or Groq
# ----------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Provider -> a reasonable default model string. Change these any time —
# they're just what shows up in the "Model" field when you switch providers.
PROVIDER_DEFAULT_MODELS = {
    "Claude": "claude-sonnet-5",
    "Gemini": "gemini-3.8-flash",
    "Groq": "openai/gpt-oss-120b",
}


def _build_prompt(maze_text, question):
    return (
        "Here is a micromouse maze in ASCII form. 'S' marks the start cell, "
        "'G' marks the goal cell(s). Walls are '-' and '|' between '+' corners.\n\n"
        f"{maze_text}\n\n"
        f"Question: {question}"
    )


def ask_claude(api_key, model, maze_text, question, max_tokens=1000):
    prompt = _build_prompt(maze_text, question)
    body = json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    parts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
    return "\n".join(parts).strip() or "(No text content in response.)"


def ask_gemini(api_key, model, maze_text, question, max_tokens=1000):
    prompt = _build_prompt(maze_text, question)
    url = GEMINI_API_URL.format(model=model)
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    try:
        candidate = data["candidates"][0]
        parts = [p.get("text", "") for p in candidate["content"]["parts"]]
        return "\n".join(parts).strip() or "(No text content in response.)"
    except (KeyError, IndexError):
        return f"(Unexpected response shape: {json.dumps(data)[:500]})"


def ask_groq(api_key, model, maze_text, question, max_tokens=1000):
    prompt = _build_prompt(maze_text, question)
    body = json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        GROQ_API_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    try:
        return data["choices"][0]["message"]["content"].strip() or "(No text content in response.)"
    except (KeyError, IndexError):
        return f"(Unexpected response shape: {json.dumps(data)[:500]})"


ASK_FUNCTIONS = {
    "Claude": ask_claude,
    "Gemini": ask_gemini,
    "Groq": ask_groq,
}


# ----------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------


class MazeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Micromouse Maze Designer")
        self.n = 4
        self.h_walls, self.v_walls = empty_grids(self.n)
        set_border(self.n, self.h_walls, self.v_walls)
        self.start = default_start(self.n)
        self.goal = set(default_goal_cells(self.n))

        self._build_layout()
        self._render()

    # -- layout ---------------------------------------------------------

    def _build_layout(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Size:").pack(side=tk.LEFT, padx=(0, 4))
        self.size_var = tk.StringVar(value="4")
        size_box = ttk.Combobox(
            top, textvariable=self.size_var, values=["4", "8", "16", "32"], width=4, state="readonly"
        )
        size_box.pack(side=tk.LEFT, padx=(0, 12))
        size_box.bind("<<ComboboxSelected>>", self.on_size_change)

        ttk.Label(top, text="Click mode:").pack(side=tk.LEFT, padx=(0, 4))
        self.mode_var = tk.StringVar(value="Walls")
        mode_box = ttk.Combobox(
            top,
            textvariable=self.mode_var,
            values=["Walls", "Set Start", "Set Goal"],
            width=10,
            state="readonly",
        )
        mode_box.pack(side=tk.LEFT, padx=(0, 12))
        mode_box.bind("<<ComboboxSelected>>", lambda _e: self._render())

        ttk.Button(top, text="Generate maze", command=self.on_generate).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Clear walls", command=self.on_clear).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Reset border", command=self.on_reset_border).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Save as .txt", command=self.on_save).pack(side=tk.LEFT, padx=(20, 4))
        ttk.Button(top, text="Load .txt", command=self.on_load).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Export for mms (.num)", command=self.on_export_num).pack(side=tk.LEFT, padx=4)

        self.hint_var = tk.StringVar()
        ttk.Label(self.root, textvariable=self.hint_var, padding=(8, 0, 8, 4)).pack(side=tk.TOP, fill=tk.X)
        self._update_hint()

        body = ttk.Frame(self.root, padding=8)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(body, bg="white", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        ai_frame = ttk.LabelFrame(body, text="Ask AI about this maze", padding=8)
        ai_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        ttk.Label(ai_frame, text="Provider:").pack(anchor=tk.W)
        self.provider_var = tk.StringVar(value="Claude")
        provider_box = ttk.Combobox(
            ai_frame,
            textvariable=self.provider_var,
            values=list(ASK_FUNCTIONS.keys()),
            width=35,
            state="readonly",
        )
        provider_box.pack(fill=tk.X, pady=(0, 8))
        provider_box.bind("<<ComboboxSelected>>", self.on_provider_change)

        self.api_key_label_var = tk.StringVar(value="Claude API key:")
        ttk.Label(ai_frame, textvariable=self.api_key_label_var).pack(anchor=tk.W)
        self.api_key_var = tk.StringVar(value=os.environ.get("ANTHROPIC_API_KEY", ""))
        ttk.Entry(ai_frame, textvariable=self.api_key_var, show="*", width=38).pack(
            fill=tk.X, pady=(0, 8)
        )

        ttk.Label(ai_frame, text="Model:").pack(anchor=tk.W)
        self.model_var = tk.StringVar(value=PROVIDER_DEFAULT_MODELS["Claude"])
        ttk.Entry(ai_frame, textvariable=self.model_var, width=38).pack(fill=tk.X, pady=(0, 8))

        ttk.Label(ai_frame, text="Question:").pack(anchor=tk.W)
        self.question_text = tk.Text(ai_frame, height=3, width=38, wrap=tk.WORD)
        self.question_text.insert("1.0", "Is this maze solvable, and what's the shortest path from S to G?")
        self.question_text.pack(fill=tk.X, pady=(0, 8))

        self.ask_btn = ttk.Button(ai_frame, text="Send", command=self.on_ask_ai)
        self.ask_btn.pack(anchor=tk.W, pady=(0, 8))

        ttk.Label(ai_frame, text="Response:").pack(anchor=tk.W)
        self.response_box = scrolledtext.ScrolledText(ai_frame, height=14, width=38, wrap=tk.WORD)
        self.response_box.pack(fill=tk.BOTH, expand=True)
        self.response_box.configure(state=tk.DISABLED)

    # -- drawing ---------------------------------------------------------

    def cell_size(self):
        return {4: 70, 8: 46, 16: 28, 32: 15}.get(self.n, 15)

    def _update_hint(self):
        mode = self.mode_var.get()
        if mode == "Walls":
            text = "Click a cell edge to toggle a wall. Green = start, coral = goal."
        elif mode == "Set Start":
            text = "Click any cell to set it as the start (green)."
        else:
            text = "Click cells to add/remove them from the goal room (coral). Must stay one connected block."
        self.hint_var.set(text)

    def _render(self):
        self._update_hint()
        self.canvas.delete("all")
        cs = self.cell_size()
        pad = 10
        size = self.n * cs
        self.canvas.config(width=size + pad * 2, height=size + pad * 2)

        sr, sc = self.start
        x0, y0 = pad + sc * cs, pad + sr * cs
        self.canvas.create_rectangle(x0, y0, x0 + cs, y0 + cs, fill="#d7ead0", outline="")
        for r, c in self.goal:
            x0, y0 = pad + c * cs, pad + r * cs
            self.canvas.create_rectangle(x0, y0, x0 + cs, y0 + cs, fill="#f6d9cb", outline="")

        for r in range(self.n + 1):
            y = pad + r * cs
            self.canvas.create_line(pad, y, pad + size, y, fill="#dddddd")
        for c in range(self.n + 1):
            x = pad + c * cs
            self.canvas.create_line(x, pad, x, pad + size, fill="#dddddd")

        for r in range(self.n + 1):
            for c in range(self.n):
                if self.h_walls[r][c]:
                    x0, y0 = pad + c * cs, pad + r * cs
                    self.canvas.create_line(x0, y0, x0 + cs, y0, fill="#1a1a1a", width=3)
        for r in range(self.n):
            for c in range(self.n + 1):
                if self.v_walls[r][c]:
                    x0, y0 = pad + c * cs, pad + r * cs
                    self.canvas.create_line(x0, y0, x0, y0 + cs, fill="#1a1a1a", width=3)

        if cs >= 20:
            self.canvas.create_text(
                pad + sc * cs + cs / 2, pad + sr * cs + cs / 2, text="S", fill="#3b6d11", font=("TkDefaultFont", 12, "bold")
            )
            for r, c in self.goal:
                self.canvas.create_text(
                    pad + c * cs + cs / 2, pad + r * cs + cs / 2, text="G", fill="#993c1d", font=("TkDefaultFont", 12, "bold")
                )

        self._pad, self._cs = pad, cs

    # -- interaction ------------------------------------------------------

    def on_canvas_click(self, event):
        pad, cs = self._pad, self._cs
        x, y = event.x - pad, event.y - pad
        mode = self.mode_var.get()

        if mode != "Walls":
            if not (0 <= x <= self.n * cs and 0 <= y <= self.n * cs):
                return
            r, c = int(y // cs), int(x // cs)
            if not (0 <= r < self.n and 0 <= c < self.n):
                return

            if mode == "Set Start":
                if (r, c) in self.goal:
                    messagebox.showwarning("Can't set start", "That cell is part of the goal room.")
                    return
                self.start = (r, c)
            else:  # Set Goal
                if (r, c) == self.start:
                    messagebox.showwarning("Can't set goal", "That cell is the start cell.")
                    return
                if (r, c) in self.goal:
                    if len(self.goal) == 1:
                        messagebox.showwarning("Can't remove", "Keep at least one goal cell.")
                        return
                    self.goal.discard((r, c))
                else:
                    self.goal.add((r, c))
            self._render()
            return

        hit = max(6, cs * 0.22)

        # Check horizontal-edge hits: near a row boundary, within a column span
        for r in range(self.n + 1):
            ry = r * cs
            if abs(y - ry) <= hit:
                c = int(x // cs)
                if 0 <= c < self.n and 0 <= x <= self.n * cs:
                    self.h_walls[r][c] = not self.h_walls[r][c]
                    self._render()
                    return

        # Check vertical-edge hits
        for c in range(self.n + 1):
            cx = c * cs
            if abs(x - cx) <= hit:
                r = int(y // cs)
                if 0 <= r < self.n and 0 <= y <= self.n * cs:
                    self.v_walls[r][c] = not self.v_walls[r][c]
                    self._render()
                    return

    def on_size_change(self, _event):
        self.n = int(self.size_var.get())
        self.h_walls, self.v_walls = empty_grids(self.n)
        set_border(self.n, self.h_walls, self.v_walls)
        self.start = default_start(self.n)
        self.goal = set(default_goal_cells(self.n))
        self._render()

    def on_generate(self):
        try:
            self.h_walls, self.v_walls = generate_maze(self.n, self.start, self.goal)
        except ValueError as exc:
            messagebox.showerror("Can't generate maze", str(exc))
            return
        self._render()

    def on_clear(self):
        self.h_walls, self.v_walls = empty_grids(self.n)
        set_border(self.n, self.h_walls, self.v_walls)
        self._render()

    def on_reset_border(self):
        set_border(self.n, self.h_walls, self.v_walls)
        self._render()

    def on_save(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt")],
            initialfile="maze.txt",
        )
        if not path:
            return
        text = maze_to_text(self.n, self.h_walls, self.v_walls, self.start, self.goal)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        messagebox.showinfo("Saved", f"Maze saved to {path}")

    def on_load(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            n, h_walls, v_walls, start, goals = text_to_maze(text)
        except Exception as exc:  # noqa: BLE001 - show any parse error to the user
            messagebox.showerror("Load failed", f"Couldn't parse that file:\n{exc}")
            return
        self.n = n
        self.size_var.set(str(n) if n in (4, 8, 16, 32) else str(n))
        self.h_walls, self.v_walls = h_walls, v_walls
        self.start = start
        self.goal = set(goals)
        self._render()

    def on_export_num(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="maze_num.txt",
        )
        if not path:
            return
        text = maze_to_num_format(self.n, self.h_walls, self.v_walls)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        messagebox.showinfo("Exported", f"mms num-format maze saved to {path}")

    def on_provider_change(self, _event):
        provider = self.provider_var.get()
        self.api_key_label_var.set(f"{provider} API key:")
        self.model_var.set(PROVIDER_DEFAULT_MODELS[provider])
        env_var = {"Claude": "ANTHROPIC_API_KEY", "Gemini": "GEMINI_API_KEY", "Groq": "GROQ_API_KEY"}[provider]
        self.api_key_var.set(os.environ.get(env_var, ""))

    def on_ask_ai(self):
        provider = self.provider_var.get()
        ask_fn = ASK_FUNCTIONS[provider]
        api_key = self.api_key_var.get().strip()
        model = self.model_var.get().strip() or PROVIDER_DEFAULT_MODELS[provider]
        question = self.question_text.get("1.0", tk.END).strip()

        if not api_key:
            messagebox.showwarning("API key needed", f"Enter your {provider} API key first.")
            return
        if not question:
            messagebox.showwarning("Question needed", "Type a question about the maze first.")
            return

        maze_text = maze_to_text(self.n, self.h_walls, self.v_walls, self.start, self.goal)

        self.ask_btn.configure(state=tk.DISABLED)
        self._set_response(f"Asking {provider}...")

        def worker():
            try:
                answer = ask_fn(api_key, model, maze_text, question)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                answer = f"API error {exc.code}: {detail}"
            except Exception as exc:  # noqa: BLE001 - surface any failure to the user
                answer = f"Request failed: {exc}"
            self.root.after(0, lambda: self._finish_ask(answer))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_ask(self, answer):
        self._set_response(answer)
        self.ask_btn.configure(state=tk.NORMAL)

    def _set_response(self, text):
        self.response_box.configure(state=tk.NORMAL)
        self.response_box.delete("1.0", tk.END)
        self.response_box.insert("1.0", text)
        self.response_box.configure(state=tk.DISABLED)


def main():
    root = tk.Tk()
    MazeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
