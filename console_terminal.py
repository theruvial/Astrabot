"""A real terminal inside a Golurk Console pane (2026-11-25).

The other panes show a program's output line by line. That cannot work for
a full-screen, interactive program such as the Strata monitor (a Textual
TUI): it draws by moving the cursor around, wants to know the window size,
and reads keys and mouse clicks. So this module runs such a program the way
a terminal emulator does:

* ``TerminalSession`` starts it inside a Windows pseudo console (ConPTY,
  through pywinpty), so the program believes it is in a real terminal.
  A reader thread feeds everything it prints into a ``pyte`` screen, which
  interprets the escape codes and keeps the current grid of cells.
* ``TerminalView`` draws that grid into a tk Text widget a few times a
  second (only the rows that changed) and sends keys, clicks and the wheel
  back to the program as the escape sequences a terminal would send.

The pure parts (``screen_runs``, ``key_sequence``, ``mouse_sequence``,
``grid_size``) have no tkinter or process in them and are unit tested.

Needs ``pywinpty`` and ``pyte`` in the console's Python
(``py -3.14 -m pip install pywinpty pyte``). Without them the pane says so
and the rest of the console works as before.
"""

from __future__ import annotations

import os
import threading

try:
    import pyte
except ImportError:  # pragma: no cover - checked by available()
    pyte = None
try:
    from winpty import PtyProcess
except ImportError:  # pragma: no cover
    PtyProcess = None

DEFAULT_FG = "d9d9d9"
DEFAULT_BG = "121212"
# The 16 ANSI colours by the names pyte gives them (it passes 256-colour and
# truecolour cells through as hex already).
NAMED = {
    "black": "000000", "red": "cd3131", "green": "0dbc79", "brown": "e5e510",
    "yellow": "e5e510", "blue": "2472c8", "magenta": "bc3fbc", "cyan": "11a8cd",
    "white": "e5e5e5",
    "brightblack": "666666", "brightred": "f14c4c", "brightgreen": "23d18b",
    "brightbrown": "f5f543", "brightyellow": "f5f543", "brightblue": "3b8eea",
    "brightmagenta": "d670d6", "brightcyan": "29b8db", "brightwhite": "ffffff",
}

# pyte keeps private (DEC "?") modes shifted left by 5 in screen.mode.
MOUSE_CLICKS, MOUSE_DRAG, MOUSE_ANY = 1000 << 5, 1002 << 5, 1003 << 5
MOUSE_SGR = 1006 << 5


def available() -> str:
    """Empty when terminals can run here, else the reason they cannot."""
    missing = [name for name, mod in (("pyte", pyte), ("pywinpty", PtyProcess)) if mod is None]
    if missing:
        return (f"Needs {' and '.join(missing)}: py -3.14 -m pip install pywinpty pyte, "
                "then restart the console.")
    return ""


def colour(value, default) -> str:
    """A pyte colour (name, hex or 'default') as a 6-digit hex string. Pure."""
    if not value or value == "default":
        return default
    value = str(value).lower()
    if value in NAMED:
        return NAMED[value]
    if len(value) == 6 and all(c in "0123456789abcdef" for c in value):
        return value
    return default


def screen_runs(line, columns, cursor_col=None):
    """One screen row as runs of (text, fg, bg, bold). Pure.

    ``line`` maps column -> pyte Char (a pyte buffer row; missing columns
    are blank). Neighbouring cells with the same look join into one run, so
    a mostly blank row is one insert. Reverse video is resolved here. The
    second half of a wide character is an empty cell and adds nothing.
    ``cursor_col`` draws the cursor as a reversed cell.
    """
    runs = []
    text, look = [], None
    for col in range(columns):
        char = line[col] if col in line else None
        data = char.data if char is not None else " "
        if data == "":
            continue
        fg = colour(getattr(char, "fg", None), DEFAULT_FG)
        bg = colour(getattr(char, "bg", None), DEFAULT_BG)
        bold = bool(getattr(char, "bold", False))
        if bool(getattr(char, "reverse", False)) != (col == cursor_col):
            fg, bg = bg, fg
        cell_look = (fg, bg, bold)
        if cell_look != look and text:
            runs.append(("".join(text), *look))
            text = []
        look = cell_look
        text.append(data)
    if text:
        runs.append(("".join(text), *look))
    return runs


# keysym -> what a terminal sends (normal cursor-key mode).
SPECIAL_KEYS = {
    "Up": "\x1b[A", "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D",
    "Home": "\x1b[H", "End": "\x1b[F", "Prior": "\x1b[5~", "Next": "\x1b[6~",
    "Insert": "\x1b[2~", "Delete": "\x1b[3~",
    "Return": "\r", "KP_Enter": "\r", "BackSpace": "\x7f", "Tab": "\t",
    "ISO_Left_Tab": "\x1b[Z", "Escape": "\x1b",
    "F1": "\x1bOP", "F2": "\x1bOQ", "F3": "\x1bOR", "F4": "\x1bOS",
    "F5": "\x1b[15~", "F6": "\x1b[17~", "F7": "\x1b[18~", "F8": "\x1b[19~",
    "F9": "\x1b[20~", "F10": "\x1b[21~", "F11": "\x1b[23~", "F12": "\x1b[24~",
}
SHIFT, CONTROL, ALT = 0x1, 0x4, 0x20000  # tk event.state bits on Windows


def key_sequence(keysym, char, state=0) -> str:
    """What to send the program for one key press, or '' to send nothing. Pure.

    Ctrl+letter becomes its control character (Ctrl+C is \\x03), Alt+key
    is ESC then the key, Shift+Tab is back-tab. A bare modifier sends nothing.
    """
    if keysym == "Tab" and state & SHIFT:
        return "\x1b[Z"
    if keysym in SPECIAL_KEYS:
        out = SPECIAL_KEYS[keysym]
    elif state & CONTROL and len(keysym) == 1 and keysym.isalpha():
        out = chr(ord(keysym.lower()) - 96)
    elif state & CONTROL and keysym in ("bracketleft", "space"):
        out = "\x1b" if keysym == "bracketleft" else "\x00"
    elif char:
        out = char  # printable, or a control character tk already made
    else:
        return ""
    if state & ALT and not out.startswith("\x1b"):
        out = "\x1b" + out
    return out


def mouse_sequence(button, col, row, pressed=True, sgr=True) -> str:
    """A mouse report for 0-based ``col``/``row``. Pure.

    ``button``: 0 left, 1 middle, 2 right, 64/65 wheel up/down, plus 32 for
    motion. SGR form (mode 1006) when the program asked for it, else the
    old X10 form, which cannot go past column 223.
    """
    if sgr:
        return f"\x1b[<{button};{col + 1};{row + 1}{'M' if pressed else 'm'}"
    if col > 222 or row > 222:
        return ""
    code = button if pressed else 3
    return "\x1b[M" + chr(32 + code) + chr(33 + col) + chr(33 + row)


# Fonts whose block and box glyphs are all one cell wide. Consolas is not
# one of them: it has no eighth-blocks (the sparklines' ▁-▇), so
# Windows borrows wider glyphs from another font and the monitor's boxes
# break apart. Cascadia Mono ships with Windows Terminal.
TERMINAL_FONTS = ("Cascadia Mono", "Cascadia Code", "Consolas")


def pick_font(families, size=10) -> tuple:
    """The first TERMINAL_FONTS family that is installed. Pure."""
    installed = set(families)
    for family in TERMINAL_FONTS:
        if family in installed:
            return (family, size)
    return ("Courier New", size)


def grid_size(width, height, cell_width, line_height, minimum=(10, 40)) -> tuple[int, int]:
    """(rows, columns) that fit in a widget of this pixel size. Pure."""
    rows = max(minimum[0], int(height // max(1, line_height)))
    cols = max(minimum[1], int(width // max(1, cell_width)))
    return rows, cols


class TerminalSession:
    """One program in a pseudo console, with its screen kept by pyte.

    Thread-safe: the reader thread feeds the screen under ``lock``, and the
    window reads it under the same lock. ``on_exit`` is called (from the
    reader thread) once the program ends.
    """

    def __init__(self, argv, cwd, rows=30, cols=100, env=None, on_exit=None):
        self.argv = list(argv)
        self.cwd = str(cwd)
        self.env = env
        self.on_exit = on_exit
        self.lock = threading.Lock()
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self.proc = None
        self.alive = False
        self._thread = None

    @property
    def size(self):
        return self.screen.lines, self.screen.columns

    def start(self):
        env = dict(os.environ if self.env is None else self.env)
        env.setdefault("COLORTERM", "truecolor")
        env["PYTHONIOENCODING"] = "utf-8"
        rows, cols = self.size
        self.proc = PtyProcess.spawn(self.argv, cwd=self.cwd, env=env, dimensions=(rows, cols))
        self.alive = True
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        try:
            while True:
                try:
                    data = self.proc.read(65536)
                except EOFError:
                    break
                if not data:
                    if not self.proc.isalive():
                        break
                    continue
                with self.lock:
                    self.stream.feed(data)
        except Exception:  # a dead pty must never take the console down
            pass
        self.alive = False
        if self.on_exit is not None:
            self.on_exit()

    def write(self, text):
        if self.alive and text:
            try:
                self.proc.write(text)
            except Exception:
                pass

    def resize(self, rows, cols):
        if (rows, cols) == self.size:
            return
        with self.lock:
            self.screen.resize(rows, cols)
            self.screen.dirty.update(range(rows))
        if self.alive:
            try:
                self.proc.setwinsize(rows, cols)
            except Exception:
                pass

    def mode_on(self, mode) -> bool:
        return mode in self.screen.mode

    def close(self):
        """End the program. Safe to call more than once."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.isalive():
                proc.terminate(force=True)
        except Exception:
            pass

    def take_dirty(self):
        """{row: runs} for every row changed since the last call."""
        with self.lock:
            screen = self.screen
            rows = sorted(r for r in screen.dirty if 0 <= r < screen.lines)
            screen.dirty.clear()
            cursor = screen.cursor
            cursor_row = None if cursor.hidden else cursor.y
            return {r: screen_runs(screen.buffer[r], screen.columns,
                                   cursor.x if r == cursor_row else None)
                    for r in rows}, cursor_row


class TerminalView:
    """Draws a TerminalSession into a tk Text widget and forwards input.

    ``make_session(rows, cols)`` returns a started TerminalSession; the view
    calls it on ``start()`` with the size that fits the widget right now.
    """

    FPS_MS = 66

    def __init__(self, parent, make_session, font=None):
        import tkinter as tk
        import tkinter.font as tkfont

        self.tk = tk
        font = font or pick_font(tkfont.families(parent))
        self.make_session = make_session
        self.session = None
        self.font = tkfont.Font(font=font)
        self.bold_font = tkfont.Font(font=font, weight="bold")
        self.text = tk.Text(parent, bg="#" + DEFAULT_BG, fg="#" + DEFAULT_FG, font=self.font,
                            wrap="none", relief="flat", bd=0, padx=0, pady=0, width=1,
                            height=1, highlightthickness=1, highlightbackground="#" + DEFAULT_BG,
                            highlightcolor="#d0bcff", insertwidth=0, cursor="arrow",
                            takefocus=True, spacing1=0, spacing3=0)
        self.text.pack(fill="both", expand=True)
        self._tags = set()
        self._rows_drawn = 0
        self._last_cursor = None
        self._resize_job = None
        self._last_motion = None
        self._buttons_down = set()
        self.text.bind("<Key>", self._on_key)
        self.text.bind("<Configure>", self._on_configure)
        for number, name in ((1, 0), (2, 1), (3, 2)):
            self.text.bind(f"<ButtonPress-{number}>", lambda e, b=name: self._on_button(e, b, True))
            self.text.bind(f"<ButtonRelease-{number}>",
                           lambda e, b=name: self._on_button(e, b, False))
        self.text.bind("<Motion>", self._on_motion)
        self.text.bind("<MouseWheel>", self._on_wheel)
        # Text has its own bindings for selecting and pasting; none of them
        # make sense on a terminal screen.
        for seq in ("<<Paste>>", "<<Cut>>", "<Double-Button-1>", "<Triple-Button-1>"):
            self.text.bind(seq, lambda e: "break")
        self.text.bind("<B1-Motion>", self._on_motion)
        # However the window goes away (Close, the watchdog's restart, a
        # crash in tk), the program goes with it instead of being orphaned.
        self.text.bind("<Destroy>", lambda e: self.stop(), add="+")
        self._tick()

    # -- lifecycle -------------------------------------------------------
    def start(self):
        self.stop()
        rows, cols = self._fit()
        self.session = self.make_session(rows, cols)
        self._redraw_all()

    def stop(self):
        session, self.session = self.session, None
        if session is not None:
            session.close()

    def detach(self):
        """Hand the running session over without ending it (pop out /
        reattach). The view goes blank; returns the session or None."""
        session, self.session = self.session, None
        self._last_cursor = None
        return session

    def attach(self, session):
        """Show a session that is already running, sized to this view."""
        self.stop()
        self.session = session
        self._rows_drawn = 0
        self._last_cursor = None
        if session is not None:
            self._redraw_all()
            self.text.after_idle(self._apply_size)

    @property
    def running(self):
        return self.session is not None and self.session.alive

    def show_message(self, message):
        """Fill the view with a plain message (no program running)."""
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", message)
        self.text.configure(state="disabled")
        self._rows_drawn = 0

    # -- drawing -----------------------------------------------------------
    def _fit(self):
        width = max(1, self.text.winfo_width() - 2)
        height = max(1, self.text.winfo_height() - 2)
        return grid_size(width, height, self.font.measure("M"),
                         self.font.metrics("linespace"))

    def _tag(self, fg, bg, bold):
        name = f"t{fg}{bg}{'b' if bold else ''}"
        if name not in self._tags:
            self.text.tag_configure(name, foreground="#" + fg, background="#" + bg,
                                    font=self.bold_font if bold else self.font)
            self._tags.add(name)
        return name

    def _redraw_all(self):
        session = self.session
        if session is None:
            return
        with session.lock:
            session.screen.dirty.update(range(session.screen.lines))

    def _tick(self):
        try:
            self._draw()
        finally:
            self.text.after(self.FPS_MS, self._tick)

    def _draw(self):
        session = self.session
        if session is None:
            return
        changed, cursor_row = session.take_dirty()
        # The old cursor row must be redrawn without the cursor.
        if self._last_cursor is not None and self._last_cursor != cursor_row \
                and self._last_cursor not in changed:
            with session.lock:
                if self._last_cursor < session.screen.lines:
                    changed[self._last_cursor] = screen_runs(
                        session.screen.buffer[self._last_cursor], session.screen.columns)
        self._last_cursor = cursor_row
        if not changed:
            return
        rows = session.screen.lines
        text = self.text
        text.configure(state="normal")
        if self._rows_drawn != rows:
            text.delete("1.0", "end")
            text.insert("1.0", "\n" * (rows - 1))
            self._rows_drawn = rows
        for row, runs in changed.items():
            line = row + 1
            text.delete(f"{line}.0", f"{line}.end")
            args = []
            for chunk, fg, bg, bold in runs:
                args += [chunk, self._tag(fg, bg, bold)]
            if args:
                text.insert(f"{line}.0", *args)
        text.configure(state="disabled")

    # -- input -------------------------------------------------------------
    def _on_key(self, event):
        if self.session is not None:
            self.session.write(key_sequence(event.keysym, event.char, event.state))
        return "break"

    def _cell(self, event):
        index = self.text.index(f"@{event.x},{event.y}")
        line, col = (int(part) for part in index.split("."))
        return col, line - 1

    def _mouse(self):
        session = self.session
        if session is None or not session.alive:
            return None
        if not (session.mode_on(MOUSE_CLICKS) or session.mode_on(MOUSE_DRAG)
                or session.mode_on(MOUSE_ANY)):
            return None
        return session

    def _on_button(self, event, button, pressed):
        self.text.focus_set()
        session = self._mouse()
        if session is None:
            return "break"
        if pressed:
            self._buttons_down.add(button)
        else:
            self._buttons_down.discard(button)
        col, row = self._cell(event)
        session.write(mouse_sequence(button, col, row, pressed, session.mode_on(MOUSE_SGR)))
        return "break"

    def _on_motion(self, event):
        session = self._mouse()
        if session is None:
            return "break"
        held = min(self._buttons_down) if self._buttons_down else None
        if held is None and not session.mode_on(MOUSE_ANY):
            return "break"
        if held is not None and not (session.mode_on(MOUSE_DRAG) or session.mode_on(MOUSE_ANY)):
            return "break"
        cell = self._cell(event)
        if cell == self._last_motion:
            return "break"
        self._last_motion = cell
        button = 32 + (held if held is not None else 3)
        session.write(mouse_sequence(button, *cell, True, session.mode_on(MOUSE_SGR)))
        return "break"

    def _on_wheel(self, event):
        session = self._mouse()
        if session is None:
            return "break"
        col, row = self._cell(event)
        button = 64 if event.delta > 0 else 65
        session.write(mouse_sequence(button, col, row, True, session.mode_on(MOUSE_SGR)))
        return "break"

    def _on_configure(self, _event):
        # Debounced: dragging the window edge fires dozens of these.
        if self._resize_job is not None:
            self.text.after_cancel(self._resize_job)
        self._resize_job = self.text.after(150, self._apply_size)

    def _apply_size(self):
        self._resize_job = None
        # A window not yet on screen is 1x1; its real <Configure> follows.
        if self.session is not None and self.text.winfo_width() > 1:
            rows, cols = self._fit()
            self.session.resize(rows, cols)
