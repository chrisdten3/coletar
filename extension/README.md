# Coleta browser bridge

Keep using the normal ChatGPT or Claude website. Coleta can supply context before
Send and capture the resulting turn without becoming a separate chatbot.

## Enable automatic mode

1. Load this folder unpacked in Chrome, or use `scripts/package_extension.sh` to
   build the extension ZIP. Reload the extension and any existing provider tabs
   after upgrading to 0.3.1.
2. In extension options, enter your **Coleta server** and **Coleta API key**. Never
   enter provider cookies, subscription tokens, or provider API keys.
3. Enable **Automatic context and conversation capture**, read its disclosure, and
   save. This is a new opt-in: existing recall/capture settings do not enable it.
4. Enable `COLETAR_CAPTURE_TURNS=true` on that server. Keep semantic extraction on
   the existing worker (`uv run coletar worker`). An unavailable capture backend is
   shown in the extension status; it does not prevent normal chatting.

Use an HTTPS server, or HTTP loopback for local development. The server must allow
`https://claude.ai`, `https://chatgpt.com`, and/or `https://chat.openai.com` in its
existing CORS allowlist. The backend still derives the provider from the browser's
Origin header, never the request body's surface label.

## Normal Send / Enter flow

1. A trusted user click on the native Send button, or plain Enter in the supported
   composer, starts a turn. Shift/modified Enter and IME composition are ignored.
2. The original prompt, with a fresh turn ID, is queued in the encrypted local
   outbox. The extension attempts `/v1/capture` before retrieval. A bounded
   `/v1/search` lookup retrieves eligible context directly;
   this is not a model-selected MCP call.
3. Relevant context is placed visibly before the prompt, using the server's
   background-not-instructions block. The website's Send button is activated once.
   No provider HTTP request is intercepted, altered, or replayed.
4. The website generates and displays its response normally. Coleta observes only
   the new response associated with this send, on the active, visible conversation.
   After observed streaming controls disappear and the text settles, it queues the
   response under the same turn ID with `role=assistant`.
5. The matching active page uploads queued records to `/v1/capture`. User turns
   enter the existing asynchronous extraction pipeline. Assistant replies are
   encrypted evidence with agent provenance; they are **not mined as user facts**.

The extension is fail-open: local enqueue has a 100 ms budget, capture acknowledgement
a 200 ms budget, and lookup a 400 ms budget. An unavailable lookup sends the unchanged prompt. Capture is independent:
**local queueing is not a server durability acknowledgement**. The diagram's
store-before-generation guarantee cannot hold during an outage in this mode.
If the user edits the draft, changes settings, navigates away, or leaves the active
window during lookup, the extension cancels its send and preserves the draft.

## Delivery and retention

- AES-GCM ciphertext is kept in extension-local storage. Its non-extractable
  WebCrypto key is kept in extension IndexedDB, not Chrome Sync. This protects
  queued storage from plaintext inspection; it is not OS-backed secret storage or
  protection against a compromised browser/extension.
- The outbox is limited to **100 records / 2 MB**, with **24-hour retention**.
  Full queues reject new records visibly. An extension alarm purges expired queue
  entries without reading any provider page. This local limit is separate from
  the server's configured raw-episode TTL.
- Retries back off to 60 seconds and run only on the same active provider origin
  with the same Coleta endpoint/key. Changing accounts never transfers pending
  captures to the new account. Disabling automatic mode clears unsent captures.
- Identified turns support up to 100,000 characters. Oversized capture is rejected,
  not truncated; lookup alone uses at most the first 4,000 characters.
- The server uses a tenant/principal/provider/turn/role identity and Store leases.
  Duplicate deliveries return one episode without resetting its key, TTL, or
  extraction state. A retry cannot resurrect a crypto-shredded episode.
- No provider session credentials are read or sent. Stored context remains subject
  to the existing tenant, locality, and retrieval filters.

## Scope and limitations

This is a best-effort browser integration. Website selectors and editor behavior
can change. The adapters support ChatGPT and Claude text composers only; native
apps, mobile apps, attachments, voice, image-only replies, edits/regeneration,
archives, and background conversations are not captured by this flow.

A reply is deliberately skipped if completion is ambiguous, the user presses Stop,
the tab/window becomes inactive, navigation occurs, or two minutes elapse. A very
fast reply whose streaming controls were never observed may also be skipped. The
extension does not treat a quiet-but-still-streaming response as completed.

Rich-text insertion is verified after the editor processes input, allowing paragraph
spacing and non-breaking-space differences. If insertion fails, Coleta sends the
original prompt only after verifying restoration. An unverified draft is never sent.

Injected context is part of the visible user message, not a hidden system prompt.
The status indicator is informational; no additional Coleta click is required.
Manual recall and composer-only capture remain available with automatic mode off.

MCP remains available for explicit search and memory management. Historical
acquisition remains a user-initiated official export/import, never archive scraping.

## Verification

```sh
node --test tests/extension/*.test.cjs
uv run pytest tests/test_capture.py tests/test_rest_bridge.py tests/test_extraction_batch.py
```

Synthetic adapter tests cover Send/Enter, consent, duplicate clicks, edited drafts,
lookup failure, interrupted streams, and reply provenance. These do not establish
compatibility with the current provider DOM. Before release, manually load the
extension and test normal Send and Enter on each provider, a no-match query, an
unavailable Coleta server, navigation during lookup, and stopping a stream. Inspect
Coleta's capture records for distinct user/assistant roles and one record per turn.
