# coletar preview site

A static, self-contained site (plain HTML/CSS/JS, no build step, no external
dependencies) — a private preview/demo, not part of the product itself.

## Run it locally

```bash
cd docs/preview
python3 -m http.server 8080
```

Then open `http://localhost:8080` and enter the password: `byqiyas`.

The password gate is client-side only — enough to keep the link out of casual
browsing and search engines, not real security. The password is visible in
`app.js` for anyone who looks.

## Publish on GitHub Pages

Repo Settings → Pages → Source: "Deploy from a branch" → Branch: `main`,
folder `/docs`. The site will be live at `<your-pages-url>/preview/`.

## What's in here

- `index.html` / `styles.css` / `app.js` — the whole site.
- Three demo sections (Live Sync, True Migration, Selective Context) are
  entirely mock: fake client-side state, no backend, nothing persisted.
