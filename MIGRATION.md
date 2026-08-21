# Migrating to a private, org-only dashboard (later)

Goal: move this repo under the **Lexsi GitHub org** and serve **private GitHub Pages**
so only org members can view it — at no extra cost (the org already pays). Do this
whenever your GitHub account is added to the org. **No code changes are needed** —
the dashboard loads its data with relative paths, so it works at any Pages URL.

## One prerequisite to confirm

Private Pages **access control** (restricting the site to org members) is a
**GitHub Enterprise Cloud** feature. On the Team plan you can publish Pages from a
private repo, but the *site itself is still public*. So before relying on this,
confirm the Lexsi org is **Enterprise Cloud**. (If it's Team-only, keep the repo
public and gate the UI with Cloudflare Access instead — see the project history.)

## Checklist (≈5 minutes)

1. **Transfer the repo to the org**
   - `gh repo transfer hemgo-lexsi/gpu-price-dashboard <lexsi-org>` — or GitHub →
     repo **Settings → General → Danger Zone → Transfer ownership**.
   - History, Actions workflows, and branches come along. The URL becomes
     `https://<lexsi-org>.github.io/gpu-price-dashboard/` (the app still works —
     relative paths).

2. **Re-add the Actions secrets** (secrets do **not** survive a transfer)
   - Repo **Settings → Secrets and variables → Actions**:
     `GCP_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.
   - Optional — the dashboard runs fine without them (AWS/GCP just fall back to
     reference prices).

3. **Allow the workflow to commit data**
   - **Settings → Actions → General → Workflow permissions → Read and write**.
   - (The workflow also declares `permissions: contents: write`, so this usually
     already works.)

4. **Enable private Pages**
   - **Settings → Pages → Build and deployment → Source = Deploy from a branch**,
     branch `main`, folder `/ (root)`.
   - Under **Visibility**, choose **Private** (Enterprise Cloud) so only org
     members can open it.

5. **Kick a build and verify**
   - **Actions → "Update GPU prices" → Run workflow** (confirms it commits data).
   - Open the new Pages URL as an org member → works. Open it signed-out /
     incognito → should be **blocked**.

## Refresh cadence vs Actions minutes

Private repos draw Actions minutes from the org plan (Enterprise ≈ 50,000/mo — the
current ~10-min cadence uses ~4,300/mo, well within budget). If the org caps
minutes, raise the interval in `.github/workflows/update.yml` (`cron: "*/10 …"` →
`"*/30 …"`).

## Rollback

Transfer back to a personal account, or set **Settings → Pages → Visibility →
Public**. Nothing else to undo.

---

*Tip: once you're in the org, I (Claude Code, authenticated as your GitHub account)
can run the transfer, re-add the secrets, and enable private Pages for you via the
`gh` CLI — just say the word.*
