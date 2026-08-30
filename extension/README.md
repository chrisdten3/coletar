# coletar composer bridge

*Portable memory in the prompt box, with no Project instructions.*

Reliable unprompted reads on claude.ai require an instruction snippet pasted into a
Project — that was measured, not assumed (see `docs/CONNECTORS.md`). This extension is
the answer to "why should a user have to do that."

## The boundary, which is the whole design

**It reads the box you type into. Nothing else.**

| | |
|---|---|
| Read the composer — your own input | ✅ |
| Write to the composer — injection | ✅ |
| Read the assistant's replies | ❌ never |
| Read conversation history | ❌ never |

Reading what you type into a text field is the category browser software has always
occupied — password managers, text expanders, spell checkers. Reading the model's
*Output* is the thing both providers' terms name, and this does not do it.

That is enforced structurally, not promised. `COMPOSERS` in `content.js` is the only
DOM lookup in the file; there is no selector for a message, a response or a
transcript, so no code path can reach one.

## What it does

**Recall** — click the **✦ memory** button (bottom right), or press the configurable
shortcut, and relevant memory is written
into the box *above* what you typed. You read it and send it yourself. Nothing is ever
added to a message you did not see, which is the difference between assistance and
someone quietly editing your words.

**Capture** — when you send, what you typed is offered to `/v1/capture`, where the same
precision-first extractor the local proxy uses decides whether anything durable was in
it. Most turns contain nothing and store nothing: 4.3% false-positive rate against the
labelled set. Only your own words are ever sent; the reply is not, and would not be
mined if it were.

## Install

1. `chrome://extensions` → enable Developer mode → **Load unpacked** → select this folder
2. Open the options page and set:
   - **Server** — `https://coletar-mcp.fly.dev`
   - **API key** — the same `sk-live-…` your MCP connector uses; it decides which tenant you reach
   - **Shortcut** — optional. Chrome claims most `⌘⇧` combinations on macOS (`⌘⇧M` is
     the profile switcher), so the default is `Ctrl+Shift+M` and the button is the
     affordance that cannot collide

The server must allow your surface's origin. Defaults cover claude.ai and chatgpt.com;
change `COLETAR_CORS_ALLOW_ORIGINS` to add others. It is an allowlist and never a
wildcard — these endpoints are authenticated, and a wildcard would let any page you
visit attempt to spend your token.

## How it relates to the MCP connector

They solve the same problem on different terms, and both can run at once.

| | MCP connector | This extension |
|---|---|---|
| Reads | when the model decides to | when you press the shortcut |
| Setup | connector + Project snippet | install + key |
| Captures | when the model decides to | every turn you send, filtered |
| Where it works | any MCP client | claude.ai, chatgpt.com |

The connector is better where it works, because the model picks what is relevant from
its own understanding of the conversation. The extension is what makes the web surface
work without asking the user to configure a Project — and it captures turns the model
would never have thought to save.

## What this is not

It is not a scraper, and the distinction is not cosmetic. Products in this space that
read the page do exist; none of them discuss what happens to your account if a provider
decides that is automated extraction of Output. Anthropic suspended accounts over
third-party automation in early 2026, against users' own subscriptions. That is the
line this stays on the right side of.
