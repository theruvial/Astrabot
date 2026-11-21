#!/bin/sh
# Update the watcher from GitHub: test the incoming code first, then pull it and restart the service.
# Nothing changes on disk or in the running service if the tests fail.
set -eu
cd "$(dirname "$0")"
git fetch -q
UP=$(git rev-parse --abbrev-ref '@{u}')
if [ "$(git rev-parse HEAD)" = "$(git rev-parse '@{u}')" ]; then echo "already up to date ($UP)"; exit 0; fi
echo "incoming changes from $UP:"; git log --oneline HEAD..'@{u}'
T=$(mktemp -d); trap 'git worktree remove --force "$T" 2>/dev/null || true; rm -rf "$T"' EXIT
git worktree add -q --detach "$T" '@{u}'
( cd "$T/audiobook-watcher" && python3 -m unittest discover -s tests -q ) || { echo "tests FAILED on the incoming code; nothing was changed"; exit 1; }
git pull -q --ff-only
systemctl --user restart audiobook-watcher
sleep 5
systemctl --user is-active audiobook-watcher
journalctl --user -u audiobook-watcher --since "-10s" --no-pager -o cat | grep -E "NAS|tag check|Audiobookshelf|Astrabot|watching" || true
