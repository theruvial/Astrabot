#!/bin/sh
# One-time move from "audiobook-watcher" to "automated-librarian" (2026-11-22).
# Run from the clone after pulling master:  sh ~/repos/Astrabot/automated-librarian/migrate.sh
# Safe to run twice: each step is skipped once done. Tests run first; if they fail nothing changes.
set -eu
cd "$(dirname "$0")"
OLD=audiobook-watcher
NEW=automated-librarian
UNITS="$HOME/.config/systemd/user"

[ -f automated_librarian.py ] || { echo "run this from the automated-librarian folder"; exit 1; }
python3 -m unittest discover -s tests -q || { echo "tests FAILED; nothing was changed"; exit 1; }

# Stop the old service first so nothing writes to the folders while they move.
if [ -f "$UNITS/$OLD.service" ]; then
    systemctl --user disable --now "$OLD" || true
    rm -f "$UNITS/$OLD.service"
    echo "removed the $OLD unit"
fi

move() {  # move $1 to $2 unless it is already there
    if [ -e "$1" ] && [ ! -e "$2" ]; then
        mkdir -p "$(dirname "$2")"
        mv "$1" "$2"
        echo "moved $1 -> $2"
    fi
}
move "$HOME/.config/$OLD" "$HOME/.config/$NEW"            # env file (secrets; mode kept)
move "$HOME/.local/state/$OLD" "$HOME/.local/state/$NEW"  # queue, history, plans

mkdir -p "$UNITS"
install -m 644 "$NEW.service" "$UNITS/$NEW.service"
systemctl --user daemon-reload
systemctl --user enable --now "$NEW"
sleep 5
systemctl --user is-active "$NEW"
journalctl --user -u "$NEW" --since "-10s" --no-pager -o cat | tail -n 8
