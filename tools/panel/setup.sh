#!/usr/bin/env bash
# Install the headless browser this harness drives. Idempotent - safe to re-run.
#
# Why Google Chrome from dl.google.com rather than `npx playwright install`:
# the sbx sandbox's default-deny network policy blocks cdn.playwright.dev, so
# Playwright cannot fetch its own Chromium build. dl.google.com and the Ubuntu
# archive are both reachable, so we install the real Chrome .deb and drive it
# through Playwright's `channel: "chrome"`. If you later allow the CDN with
#   sbx policy allow network cdn.playwright.dev
# then `npx playwright install chromium` works too and you can drop the channel.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v google-chrome >/dev/null 2>&1; then
  echo "google-chrome already installed: $(google-chrome --version)"
else
  echo "==> installing google-chrome-stable"
  deb="$(mktemp -d)/chrome.deb"
  curl -fsSL -o "$deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$deb"
  rm -f "$deb"
  echo "installed: $(google-chrome --version)"
fi

# Chrome on Linux reads user-trusted CAs from its own NSS database, not from
# /etc/ssl/certs. Without this the sandbox proxy's MITM certificate is rejected
# and every outbound https request from the page fails the TLS handshake.
ca=/usr/local/share/ca-certificates/proxy-ca.crt
if [ -f "$ca" ]; then
  if command -v certutil >/dev/null 2>&1; then :; else
    echo "==> installing libnss3-tools (for certutil)"
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libnss3-tools
  fi
  mkdir -p "$HOME/.pki/nssdb"
  [ -f "$HOME/.pki/nssdb/cert9.db" ] || certutil -d "sql:$HOME/.pki/nssdb" -N --empty-password
  if certutil -d "sql:$HOME/.pki/nssdb" -L | grep -q sbx-proxy-ca; then
    echo "proxy CA already trusted by Chrome"
  else
    certutil -d "sql:$HOME/.pki/nssdb" -A -t "C,," -n sbx-proxy-ca -i "$ca"
    echo "==> added sandbox proxy CA to Chrome's NSS store"
  fi
fi

# PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD stops `npm install` from reaching for the
# blocked CDN and failing the install outright.
echo "==> installing node deps"
cd "$here"
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-audit --no-fund

echo
echo "done. try:  node tools/panel/panel.mjs smoke"
