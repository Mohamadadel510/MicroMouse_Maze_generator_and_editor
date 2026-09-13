# Micromouse Maze Designer

A local desktop app (Python + Tkinter) for designing or randomly generating
micromouse test mazes, exporting/importing them as plain-text files
(including the [mms simulator's](https://github.com/mackorone/mms) "num"
format), and optionally asking an LLM (Claude, Gemini, or Groq) questions
about the current maze.

## Requirements

- Python 3.8+
- Tkinter — bundled with most Python installers. On Debian/Ubuntu, install
  it separately if needed:
  ```
  sudo apt install python3-tk
  ```
- No third-party packages. The "Ask AI" panel calls each provider's API
  directly with the standard library's `urllib`.

## Running it

```
python micromouse_maze_designer.py
```

## Designing a maze

- **Size dropdown** — 4×4 (test), 8×8, 16×16, or 32×32.
- **Click mode dropdown** — switches what a canvas click does:
  - **Walls** (default) — click a cell edge to toggle a wall on or off.
  - **Set Start** — click any cell to make it the start (green). Can't
    overlap the goal.
  - **Set Goal** — click a cell to add or remove it from the goal room
    (coral). The goal can be any shape and any size, but its cells must
    stay a single connected block — **Generate maze** will refuse and
    tell you if they aren't.
- **Generate maze** — builds a random, guaranteed-solvable maze with a
  recursive-backtracker algorithm, using whatever start/goal you've set.
  Every cell is reachable from the start.
- **Clear walls** — removes all internal walls, keeping just the outer
  border.
- **Reset border** — restores the outer border without touching interior
  walls.

Start and goal default to the classic layout (bottom-left corner start,
center goal — a 2×2 block for even sizes, a single cell for odd sizes)
whenever you pick a new size, but you're free to move either one anywhere
on the grid using the click modes above. Whatever shape the goal ends up
as, it's always sealed as one open chamber (no internal walls between its
cells) with **exactly one entrance** from the rest of the maze — matching
official micromouse maze rules.

## Saving and loading mazes

Two file formats are supported, from the toolbar:

### Save as .txt / Load .txt — this app's own ASCII format

Human-readable maze art, e.g. for a 4×4 maze:

```
# size: 4
# start: 3,0
# goal: 1,1 1,2 2,1 2,2
+---+---+---+---+
|               |
+   +---+---+   +
|   | G   G |   |
+   +   +   +   +
|   | G   G |   |
+---+   +---+   +
| S             |
+---+---+---+---+
```

The three `#` header lines are metadata (size, start cell, goal cell(s) as
`row,col` with row 0 at the top). The grid encodes walls: a `-` between
`+` marks a wall on that horizontal edge, a `|` marks a wall on that
vertical edge. **Load .txt** reads this format back exactly — saving and
reloading a maze reproduces the identical wall layout.

### Export for mms (.num) — for the mms simulator

Writes the maze in the "num" format documented in the
[mms README](https://github.com/mackorone/mms#num-format): one line per
cell, `X Y N E S W`, where `X`/`Y` use mms's coordinate system (X
increases right, Y increases **up**, origin at the bottom-left corner),
and `N`/`E`/`S`/`W` are `1` if a wall is present on that side, else `0`.
This format only describes walls — it has no concept of start/goal
placement, so wherever you've put yours in this app won't carry over;
mms determines start/goal from its own maze conventions.

This export was checked line-for-line against the exact example maze in
the mms README to confirm the coordinate mapping and column order are
correct. Load the exported file into mms as a custom maze file to test
your maze-solving algorithm against it.

Note: this app only writes the `.num` format — it doesn't read it back in
(only its own `.txt` format round-trips through **Load .txt**).

## Asking an LLM about the maze

The right-hand panel lets you ask a question about the maze currently on
screen (e.g. "is this solvable?", "what's the shortest path from S to
G?"). It sends the maze as ASCII text plus your question to whichever
provider you pick.

1. **Provider** — choose Claude, Gemini, or Groq. The **Model** field and
   API key label update automatically.
2. **API key** — either paste it into the field, or set it as an
   environment variable before launching the app so it's picked up
   automatically:
   ```
   export ANTHROPIC_API_KEY="sk-ant-..."
   export GEMINI_API_KEY="..."
   export GROQ_API_KEY="gsk_..."
   ```
   Get a key from:
   - Claude: <https://console.anthropic.com>
   - Gemini: <https://aistudio.google.com/apikey>
   - Groq: <https://console.groq.com/keys>
3. **Model** — pre-filled with a reasonable default per provider, but you
   can type in a different model name if a provider updates or renames
   theirs.
4. **Question** — type what you want to ask, then **Send**.

The API key is only kept in memory for the running session. It is never
written to a maze file or saved to disk by this app. Don't hardcode a key
into the script if you plan to share it — use the environment variable
instead.

## Troubleshooting

- **"couldn't connect to display" / Tkinter errors on launch** — you're
  likely running on a headless machine (e.g. over SSH with no X server).
  Run it on a machine with a desktop environment, or set up X forwarding.
- **API error 404 on a model name** — providers periodically retire and
  rename models. Check that provider's current model list and update the
  **Model** field.
- **"Couldn't parse that file" on Load** — only files saved by this app's
  own **Save as .txt** exporter are guaranteed to load back in; hand-edited
  or third-party maze files may not match the expected format.
- **"Can't generate maze: Goal cells must form a single connected block"**
  — switch to **Set Goal** mode and check your goal cells are all
  touching (sharing an edge, not just a corner). Diagonal-only adjacency
  doesn't count as connected.
