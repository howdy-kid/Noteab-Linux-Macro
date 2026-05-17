#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
#this will download the gui for the macro, note that this is completely optional as the gui will automatically fall back to tkinter.
printf '%s\n' "Installing optional dependencies for the imported main WebView GUI..."

if command -v apt-get >/dev/null 2>&1; then
  printf '%s\n' "Detected Debian/Ubuntu-style system. Installing GTK/WebKit packages via apt."
  sudo apt-get update
  sudo apt-get install -y \
    python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gir1.2-webkit2-4.1 || sudo apt-get install -y \
    python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.0
  python3 -m pip install --upgrade pywebview
elif command -v pacman >/dev/null 2>&1; then
  printf '%s\n' "Detected Arch-style system. Installing GTK/WebKit packages via pacman."
  sudo pacman -S --needed python-gobject gtk3 webkit2gtk python-pywebview
elif command -v dnf >/dev/null 2>&1; then
  printf '%s\n' "Detected Fedora-style system. Installing GTK/WebKit packages via dnf."
  sudo dnf install -y python3-gobject gtk3 webkit2gtk4.1 python3-pywebview || \
  sudo dnf install -y python3-gobject gtk3 webkit2gtk3 python3-pywebview
elif command -v zypper >/dev/null 2>&1; then
  printf '%s\n' "Detected openSUSE-style system. Installing GTK/WebKit packages via zypper."
  sudo zypper install -y python3-gobject gtk3 webkit2gtk3 typelib-1_0-WebKit2-4_0 python3-pywebview || true
else
  printf '%s\n' "Could not detect a supported package manager. Installing Qt WebView backend with pip instead."
  python3 -m pip install -r requirements-webview-qt.txt
fi

printf '\n%s\n' "Done. Try: NOTEAB_WEB_GUI=1 ./run_linux.sh"
