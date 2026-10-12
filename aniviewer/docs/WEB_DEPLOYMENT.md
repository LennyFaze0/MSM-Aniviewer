# Web Viewer Deployment

Public address: https://lennyfaze0.github.io/MSM-Aniviewer/

This is a static Vite application, not a Jekyll site. GitHub Pages serves only the
generated `aniviewer/web-app/dist` artifact. No Python server, private credentials,
game installation, or user assets are needed on GitHub.

## Initial Setup

In the repository's **Settings > Pages > Build and deployment**, choose
**GitHub Actions** as Source. No Jekyll template or branch publishing is needed.
This one-time setting requires repository administration access.

The root `.github/workflows/deploy-pages.yml` installs the locked dependencies
with Node 24 and runs unit tests, desktop/mobile browser tests, the production
build, and production smoke tests. Only then does it upload and deploy the Pages
artifact. The final step checks the public `build-info.json` against the commit
that triggered the workflow, allowing for CDN propagation.

The workflow publishes changes to the web app and its shared UI assets on `main`.
It can also be run manually under **Actions > Deploy web viewer to GitHub Pages >
Run workflow**. Failed builds leave the previously deployed site in place.
If the first run fails because Pages was not enabled, enable it and rerun the
workflow. Failure screenshots are retained as a workflow artifact for seven days.

## Local Verification

```sh
cd aniviewer/web-app
npm ci
npx playwright install chromium
npm test
npm run test:e2e -- --workers=2
npm run build
npm run test:production -- --workers=2
```

Production tests launch a static preview on port 4175. They check hashed assets,
the parser worker, a synthetic BIN/atlas fixture, PNG export, and complete buffered
audio across loops and interruption. They require no private game data. Tests
with optional private fixtures remain explicitly skipped if those are absent.

To check the actual published website instead of a local preview:

```sh
ANIVIEWER_DEPLOYED_URL=https://lennyfaze0.github.io/MSM-Aniviewer/ npm run test:production -- --workers=2
```

## Release Boundaries

- Commit source, package lock, notices, shared viewer icons, and web documentation.
- Never commit `node_modules`, `dist`, test output, local game assets, credentials,
  or unrelated workspace files. The root workflow is the required exception to
  keeping application source under `aniviewer/`.
- Keep the Vite base URL `/MSM-Aniviewer/` aligned with the repository name.
- GitHub Pages HTTPS enables browser features unavailable on an ordinary LAN HTTP
  preview. Codec, download, and folder-handle support still vary by browser.
- Stored assets are origin-specific. Assets retained in a localhost/LAN preview
  are not transferred to the public site; users import them there once.
- DOF bundles are not hosted or proxied. CDN CORS limitations and manual imports
  still apply. The included manifests contain filenames/sizes only.
- This publishes the current browser feature set, not a claim of full desktop
  feature parity. See `WEB_VIEWER_ROADMAP.md` and `WEB_EXPORT_PARITY.md`.

For rollback, redeploy a known-good workflow run or revert the release commit with
a new commit. Do not force-push or rewrite repository history to roll back a site.
