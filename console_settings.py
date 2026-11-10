"""The Golurk Console's Settings window.

Lists the built-in windows (read only, they cannot be removed) and the
services you added (editable, removable), plus a General page. Nothing
is written until Save; Cancel throws the edits away.

``open_settings(root, colours, cfg, on_save)``: ``on_save(general,
services)`` is called with the edited values and returns the message to
show, or raises to say it could not save.
"""

from __future__ import annotations

from pathlib import Path

from console_services import (
    BUILTIN_PANES,
    BUILTIN_TITLES,
    DEFAULT_SERVICE,
    IS_WINDOWS,
    RUN_AS,
    check_entry,
    clean_service,
    command_text,
    pairs_text,
    parse_pairs,
    set_startup,
    startup_installed,
    text_lines,
    unique_key,
    _int,
)

BUILTIN_NOTE = (
    "This window is part of the console, so it cannot be removed or edited here. "
    "It can be collapsed with the arrow on its header. Restart buttons for it are in "
    "the top bar."
)


def open_settings(root, colours, cfg, on_save):
    import json
    import tkinter as tk
    from tkinter import filedialog, messagebox

    bg, card, text_colour, accent = (colours["bg"], colours["card"], colours["text"],
                                     colours["accent"])
    topbar, secondary, on_primary = colours["topbar"], colours["secondary"], colours["on_primary"]
    ok_colour, err_colour = "#b5e8b5", "#f2b8b5"
    font, small, mono = ("Segoe UI", 10), ("Segoe UI", 9), ("Consolas", 10)

    general = {"console_name": cfg.get("console_name", ""),
               "startup_delay_seconds": cfg.get("startup_delay_seconds", 0),
               "start_at_login": startup_installed()}
    services = [json.loads(json.dumps(clean_service(s))) for s in cfg.get("services", [])]

    win = tk.Toplevel(root)
    win.title("Console settings")
    win.geometry("1000x760")
    win.configure(bg=bg)
    win.transient(root)

    left = tk.Frame(win, bg=bg, width=260)
    left.pack(side="left", fill="y", padx=(10, 0), pady=10)
    left.pack_propagate(False)
    entries = tk.Listbox(left, bg=card, fg=text_colour, selectbackground=accent,
                         selectforeground=on_primary, relief="flat", font=font,
                         activestyle="none", exportselection=False, highlightthickness=0)
    entries.pack(fill="both", expand=True)

    def small_button(parent, label, command):
        button = tk.Button(parent, text=label, command=command, bg=topbar, fg=text_colour,
                           activebackground=card, relief="flat", font=small, padx=8, pady=4)
        button.pack(side="top", fill="x", pady=(6, 0))
        return button

    right_outer = tk.Frame(win, bg=bg)
    right_outer.pack(side="left", fill="both", expand=True, padx=10, pady=10)
    bottom = tk.Frame(right_outer, bg=bg)
    bottom.pack(side="bottom", fill="x", pady=(8, 0))
    status = tk.Label(bottom, text="", bg=bg, fg=secondary, font=small, anchor="w",
                      justify="left", wraplength=620)
    status.pack(side="left", fill="x", expand=True)
    scroll_area = tk.Frame(right_outer, bg=bg)
    scroll_area.pack(fill="both", expand=True)
    form_canvas = tk.Canvas(scroll_area, bg=bg, highlightthickness=0)
    form_scroll = tk.Scrollbar(scroll_area, command=form_canvas.yview)
    form_scroll.pack(side="right", fill="y")
    form_canvas.pack(side="left", fill="both", expand=True)
    form_canvas.configure(yscrollcommand=form_scroll.set)
    form = tk.Frame(form_canvas, bg=bg)
    form_window = form_canvas.create_window((0, 0), window=form, anchor="nw")
    form.bind("<Configure>", lambda e: form_canvas.configure(scrollregion=form_canvas.bbox("all")))
    form_canvas.bind("<Configure>", lambda e: form_canvas.itemconfigure(form_window, width=e.width))

    def scroll_form(event):
        if form.winfo_height() > form_canvas.winfo_height():
            form_canvas.yview_scroll(-2 if getattr(event, "delta", 0) > 0 else 2, "units")

    win.bind("<MouseWheel>", scroll_form, add="+")

    # (kind, ref): ("general", None) | ("builtin", key) | ("service", index)
    items: list = []
    current = {"item": None, "collect": None}

    def label_for(kind, ref):
        if kind == "general":
            return "General"
        if kind == "builtin":
            return f"{BUILTIN_TITLES[ref]}  (built in)"
        return "Service: " + (services[ref].get("title") or services[ref].get("name") or "?")

    def refresh_list(select=None):
        items.clear()
        items.append(("general", None))
        items.extend(("builtin", key) for key in BUILTIN_PANES)
        items.extend(("service", i) for i in range(len(services)))
        entries.delete(0, "end")
        for kind, ref in items:
            entries.insert("end", label_for(kind, ref))
        index = items.index(select) if select in items else 0
        entries.selection_clear(0, "end")
        entries.selection_set(index)
        show(items[index])

    def collect():
        if current["collect"]:
            current["collect"]()

    # -- field builders ------------------------------------------------------
    row = {"n": 0}

    def clear_form():
        for widget in form.winfo_children():
            widget.destroy()
        form_canvas.yview_moveto(0)
        row["n"] = 0

    def heading(text):
        tk.Label(form, text=text, bg=bg, fg=accent, font=("Segoe UI", 12, "bold"),
                 anchor="w").grid(row=row["n"], column=0, columnspan=3, sticky="w", pady=(0, 8))
        row["n"] += 1

    def hint(text):
        label = tk.Label(form, text=text, bg=bg, fg=secondary, font=small, anchor="w",
                         justify="left", wraplength=500)
        label.grid(row=row["n"], column=1, sticky="ew", pady=(0, 6))
        label.bind("<Configure>", lambda e: e.widget.configure(wraplength=max(200, e.width - 4)))
        row["n"] += 1

    def field_label(text):
        tk.Label(form, text=text, bg=bg, fg=text_colour, font=font, anchor="nw",
                 justify="left").grid(row=row["n"], column=0, sticky="nw", padx=(0, 10),
                                      pady=(4, 2))

    def entry(label, value, browse=None):
        field_label(label)
        var = tk.StringVar(value="" if value is None else str(value))
        box = tk.Entry(form, textvariable=var, bg=card, fg=text_colour, font=mono,
                       insertbackground=text_colour, relief="flat")
        box.grid(row=row["n"], column=1, sticky="ew", pady=(4, 2), ipady=3)
        if browse:
            tk.Button(form, text="Browse...", command=lambda: browse(var), bg=topbar,
                      fg=text_colour, relief="flat", font=small).grid(
                row=row["n"], column=2, sticky="w", padx=(6, 0))
        row["n"] += 1
        return var

    def text(label, value, height=3, buttons=()):
        field_label(label)
        box = tk.Text(form, height=height, bg=card, fg=text_colour, font=mono,
                      insertbackground=text_colour, relief="flat", wrap="word", undo=True)
        box.insert("1.0", value or "")
        box.grid(row=row["n"], column=1, sticky="ew", pady=(4, 2))
        if buttons:
            col = tk.Frame(form, bg=bg)
            col.grid(row=row["n"], column=2, sticky="nw", padx=(6, 0), pady=(4, 0))
            for blabel, command in buttons:
                tk.Button(col, text=blabel, command=lambda c=command: c(box), bg=topbar,
                          fg=text_colour, relief="flat", font=small).pack(fill="x", pady=(0, 4))
        row["n"] += 1
        return box

    def choice(label, options, value):
        field_label(label)
        names = list(options.values())
        var = tk.StringVar(value=options.get(value, names[0]))
        menu = tk.OptionMenu(form, var, *names)
        menu.configure(bg=card, fg=text_colour, activebackground=topbar, relief="flat",
                       highlightthickness=0, font=font, anchor="w")
        menu["menu"].configure(bg=card, fg=text_colour, font=font)
        menu.grid(row=row["n"], column=1, sticky="w", pady=(4, 2))
        row["n"] += 1
        return lambda: next((k for k, v in options.items() if v == var.get()), value)

    def check(label, value):
        var = tk.BooleanVar(value=bool(value))
        tk.Checkbutton(form, text=label, variable=var, bg=bg, fg=text_colour,
                       selectcolor=card, activebackground=bg, activeforeground=text_colour,
                       font=font, anchor="w").grid(row=row["n"], column=1, columnspan=2,
                                                   sticky="w", pady=(4, 2))
        row["n"] += 1
        return var

    def get_text(box):
        return box.get("1.0", "end-1c")

    def browse_folder(var):
        path = filedialog.askdirectory(parent=win, initialdir=var.get() or None)
        if path:
            var.set(path)

    def browse_program(box):
        path = filedialog.askopenfilename(
            parent=win, title="Choose the program, script or .bat to run",
            filetypes=[("Programs and scripts", "*.exe *.bat *.cmd *.ps1 *.py"),
                       ("All files", "*.*")])
        if path:
            quoted = f'"{path}"' if " " in path else path
            if path.lower().endswith(".ps1"):
                quoted = f"& {quoted}"
            box.insert("1.0", quoted + (" " if get_text(box).strip() else ""))

    # -- pages ---------------------------------------------------------------
    def show(item):
        collect()
        clear_form()
        form.columnconfigure(1, weight=1)
        current["item"] = item
        current["collect"] = None
        kind, ref = item
        status.configure(text="")
        remove_button.configure(state="normal" if kind == "service" else "disabled")
        if kind == "general":
            heading("General")
            name = entry("Console name", general["console_name"])
            hint("The window title.")
            delay = entry("Startup delay (s)", general["startup_delay_seconds"])
            hint("Wait this long after the console opens before starting the services you "
                 "added. The built-in windows start straight away.")
            login = check("Open this console automatically when I log in to Windows",
                          general["start_at_login"])
            if not IS_WINDOWS:
                hint("(Start at login is only available on Windows.)")

            def collect_general():
                general["console_name"] = name.get().strip() or "Golurk Console"
                general["startup_delay_seconds"] = max(0, _int(delay.get(), 0))
                general["start_at_login"] = bool(login.get())
            current["collect"] = collect_general
            return
        if kind == "builtin":
            heading(BUILTIN_TITLES[ref])
            hint(BUILTIN_NOTE)
            return

        spec = services[ref]
        heading(f"Service: {spec.get('title') or spec.get('name')}")
        title = entry("Name", spec.get("title") or spec.get("name") or "")
        run_as = choice("Run as", RUN_AS, spec.get("run_as", "program"))
        hint("Program: a path to an .exe (or a name on PATH) followed by its arguments; quote "
             "paths with spaces. PowerShell: any PowerShell command. Batch file / cmd: a .bat "
             "file or cmd syntax, e.g. \"D:\\Servers\\Dragonwilds\\start.bat\". Several lines "
             "run one after another; a line ending in ` (PowerShell) or ^ (cmd) continues "
             "onto the next.")
        command = text("Command", command_text(spec.get("command")), height=4,
                       buttons=[("Browse...", browse_program)])
        hint("Leave empty to only show the pane without running anything.")
        cwd = entry("Working folder", spec.get("cwd", ""), browse=browse_folder)
        env = text("Environment\nvariables", pairs_text(spec.get("env")), height=3)
        hint("One per line: NAME=value")
        stop = text("Stop\ncommands", "\n".join(spec.get("stop_input") or []), height=2)
        hint("Typed into the program's console to stop it cleanly, one per line (for example "
             "/save then /quit). Leave empty to just end it.")
        grace = entry("Stop wait (s)", spec.get("stop_grace", 30))
        hint("How long to wait for a clean stop before ending it forcibly.")
        auto = check("Start this when the console opens", spec.get("autostart", True))

        user = spec.get("user") or {}
        heading_gap = tk.Label(form, text="Run under another Windows account", bg=bg,
                               fg=accent, font=("Segoe UI", 11, "bold"), anchor="w")
        heading_gap.grid(row=row["n"], column=0, columnspan=3, sticky="w", pady=(14, 4))
        row["n"] += 1
        as_user = check("Run as another Windows user (for example a limited guest account)",
                        user.get("enabled"))
        hint("The program and everything it starts run under that account instead of yours. "
             "The account must be allowed to log on to this PC, and needs read and execute "
             "access to the program's folder, and write access to wherever it saves.")
        username = entry("User name", user.get("username", ""))
        domain = entry("Domain", user.get("domain", ""))
        hint("Leave the domain blank for an account on this PC.")
        password_env = entry("Password variable", user.get("password_env", ""))
        hint("The NAME of an environment variable that holds the account's password. The "
             "password itself is never saved here or in any log. Leave blank for an account "
             "that has no password.")

        def collect_service():
            spec["title"] = title.get().strip() or spec.get("name") or "Service"
            spec["run_as"] = run_as()
            spec["command"] = get_text(command).strip()
            spec["cwd"] = cwd.get().strip()
            spec["env"] = parse_pairs(get_text(env))
            spec["stop_input"] = text_lines(get_text(stop))
            spec["stop_grace"] = _int(grace.get(), 30)
            spec["autostart"] = bool(auto.get())
            spec["user"] = {"enabled": bool(as_user.get()),
                            "username": username.get().strip(),
                            "domain": domain.get().strip(),
                            "password_env": password_env.get().strip()}
        current["collect"] = collect_service

    def on_select(_event=None):
        selection = entries.curselection()
        if not selection or items[selection[0]] == current["item"]:
            return
        collect()
        current["collect"] = None
        refresh_list(select=items[selection[0]])

    entries.bind("<<ListboxSelect>>", on_select)

    def add_service():
        collect()
        current["collect"] = None
        taken = {s.get("name") for s in services}
        name = unique_key("New service", taken)
        spec = json.loads(json.dumps(DEFAULT_SERVICE))
        spec.update({"name": name, "title": "New service"})
        services.append(spec)
        refresh_list(select=("service", len(services) - 1))

    def remove():
        kind, ref = current["item"] or ("general", None)
        if kind != "service":
            status.configure(text="Built-in windows cannot be removed.", fg=err_colour)
            return
        if not messagebox.askyesno("Remove", f"Remove {label_for(kind, ref)}?\n\nIt is "
                                   "stopped when you press Save.", parent=win):
            return
        current["collect"] = None
        services.pop(ref)
        refresh_list()

    small_button(left, "+ Add service", add_service)
    remove_button = small_button(left, "Remove selected", remove)

    def check_current():
        collect()
        kind, ref = current["item"] or ("general", None)
        if kind != "service":
            status.configure(text="Pick a service to check its paths.", fg=secondary)
            return
        problems = check_entry(services[ref])
        status.configure(text="\n".join(problems) if problems
                         else "Paths look good: the program and folder were found.",
                         fg=err_colour if problems else ok_colour)

    def save():
        collect()
        problems = []
        for spec in services:
            problems += [f"{spec.get('title')}: {p}" for p in check_entry(spec)
                         if not p.startswith("No command")]
        if problems and not messagebox.askyesno(
                "Save anyway?", "Some settings look wrong:\n\n" + "\n".join(problems)
                + "\n\nSave anyway?", parent=win):
            return
        try:
            message = on_save(dict(general), [dict(s) for s in services])
        except OSError as exc:
            messagebox.showerror("Not saved", f"Could not save: {exc}", parent=win)
            return
        note = ""
        try:
            if IS_WINDOWS and general["start_at_login"] != startup_installed():
                set_startup(general["start_at_login"])
        except OSError as exc:
            note = f"\n\nCould not change start-at-login: {exc}"
        messagebox.showinfo("Saved", (message or "Saved.") + note, parent=win)
        win.destroy()

    for label, command in (("Save", save), ("Cancel", win.destroy), ("Check paths", check_current)):
        tk.Button(bottom, text=label, command=command,
                  bg=accent if label == "Save" else topbar,
                  fg=on_primary if label == "Save" else text_colour, relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=14, pady=5).pack(side="right", padx=(6, 0))
    refresh_list()
    return win
