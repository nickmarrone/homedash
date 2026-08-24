# Panel harness — headless browser for the wall panel

The panel is a client-rendered SvelteKit app. A Svelte render error still returns
HTTP 200, so the backend test suite can be entirely green while the wall display
shows a blank rectangle. This harness closes that gap: it drives real Chrome,
screenshots what it sees, and fails on console errors and non-2xx responses.

## Setup (once per sandbox)

```bash
tools/panel/setup.sh
```

Installs Google Chrome, trusts the sandbox proxy CA in Chrome's NSS store, and
installs Playwright without its browser download.

### Why not `npx playwright install`

The sbx sandbox runs a default-deny network policy and `cdn.playwright.dev` is
not on the allow list, so Playwright cannot fetch its own Chromium build:

```
Blocked by network policy: domain cdn.playwright.dev:443
  detail: no matching allow rule — blocked by default deny policy
```

`dl.google.com` *is* reachable, so we install the real Chrome `.deb` and drive it
through Playwright's `channel: "chrome"`. Everything else about Playwright works
normally — this only changes where the binary comes from.

To use stock Playwright Chromium instead, allow the CDN from the **host**:

```bash
sbx policy allow network cdn.playwright.dev
```

then `npx playwright install chromium` and drop `channel` from `LAUNCH` in
`panel.mjs`.

### Two container-specific details worth knowing

- **`--no-sandbox`.** Chrome's own sandbox needs user namespaces that aren't
  available here. Standard for CI images; already set in `panel.mjs`.
- **The proxy CA.** Chrome on Linux reads user-trusted CAs from its own NSS
  database (`~/.pki/nssdb`), *not* `/etc/ssl/certs`. Without the `certutil` step
  in `setup.sh`, every https request the page makes dies at the TLS handshake
  (`net_error -202`) because the sandbox proxy MITMs it. This bites anything
  loading Google Fonts or a remote API from the browser.

## Use

```bash
# Full smoke: build frontend if needed, boot backend on a scratch DB,
# click through all four views, screenshot each. Exits non-zero on any issue.
node tools/panel/panel.mjs smoke

# Against a server you already have running
node tools/panel/panel.mjs smoke --url http://127.0.0.1:8000

# One page, one screenshot, console report
node tools/panel/panel.mjs shot http://127.0.0.1:8000 /tmp/panel.png
```

Screenshots land in `tools/panel/shots/` (gitignored). Read them with the Read
tool — they render inline, which is the whole point: it's the only way to see
that the month grid is actually a grid.

## Notes

- `smoke` points the backend at a throwaway SQLite file via `HOMEDASH_DB_PATH`,
  so it never touches `backend/data/homedash.db`.
- The viewport is 1280x800 as a stand-in for the wall panel. When the display is
  chosen (Phase 3, open decision #2) change it to that panel's real resolution —
  layout bugs on a 1280-wide desktop window are not the ones that matter.
- Outbound calls the *app* makes are still subject to the network policy:
  `api.open-meteo.com` is blocked here, so the header reads "Weather
  unavailable." in screenshots. That's the sandbox, not a regression.
