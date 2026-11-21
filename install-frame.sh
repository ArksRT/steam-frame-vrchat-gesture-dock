#!/bin/sh
set -eu

src=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
dst="$HOME/.local/share/vrchat-gesture-dock"
unit="$HOME/.config/systemd/user/vrchat-gesture-dock.service"

python3 -c 'import aiohttp'
install -d "$dst" "$(dirname -- "$unit")"
install -m 644 "$src/dashboard.js" "$src/frame_dock.py" "$dst/"
install -d -m 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
pub=$(cat "$src/frame-tunnel-key.pub")
case "$pub" in
    'ssh-ed25519 '*) ;;
    *) echo "Missing Ed25519 tunnel key" >&2; exit 1 ;;
esac
entry="restrict,port-forwarding,permitlisten=\"127.0.0.1:27656\",permitopen=\"127.0.0.1:1\",command=\"/usr/bin/false\" $pub"
if ! grep -Fxq -- "$entry" "$HOME/.ssh/authorized_keys"; then
    printf '\n%s\n' "$entry" >> "$HOME/.ssh/authorized_keys"
fi

cat > "$unit" <<'UNIT'
[Unit]
Description=VRChat gesture switch for the Steam Frame Dashboard
After=graphical-session.target

[Service]
ExecStart=/usr/bin/python3 %h/.local/share/vrchat-gesture-dock/frame_dock.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable vrchat-gesture-dock.service
systemctl --user restart vrchat-gesture-dock.service
systemctl --user is-active vrchat-gesture-dock.service
