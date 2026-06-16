# Deploying Autograph for free — Render + Vercel

The **backend (FastAPI)** goes to Render. The **frontend (Next.js)** goes to
Vercel. Both have generous free tiers, both auto-deploy from your GitHub
repo on every `git push`.

Your GitHub repo: <https://github.com/devanshu-tg/schema_creation_agent>

---

## Part 1 — Backend on Render (~10 min)

### 1. Push the latest code

From the project root:

```powershell
git add Dockerfile .dockerignore render.yaml DEPLOY_RENDER_VERCEL.md
git commit -m "deploy: Render + Vercel config"
git push
```

### 2. Sign up at <https://render.com>

- "**Sign in with GitHub**" — Render reads your repos through GitHub OAuth.
- After sign-in, you'll land on the Dashboard.

### 3. Create the web service

- Top-right **"New +"** button → **"Web Service"**.
- Pick **"Build and deploy from a Git repository"** → **Next**.
- Find `devanshu-tg/schema_creation_agent` → click **Connect**.
  - If the repo doesn't show up, click "**Configure account**" → grant
    Render access to that repo.

### 4. Configure the service

Render reads `render.yaml` at the repo root, so most fields auto-fill.
Verify:

| Field | Value |
|---|---|
| Name | `autograph-backend` |
| Region | `Oregon (US West)` (or any free region) |
| Branch | `main` |
| Runtime | `Docker` (detected from `Dockerfile`) |
| Instance type | `Free` |
| Health check path | `/api/health` |

### 5. Add the secret environment variables

Scroll to **Environment Variables**. The `render.yaml` already declared three
variables that need actual values:

| Key | Value (copy from your local `.env`) |
|---|---|
| `GEMINI_API_KEY` | *(from `.env`)* |
| `TG_HOST` | *(from `.env`, e.g. `https://tg-<id>.i.tgcloud.io`)* |
| `TG_SECRET` | *(from `.env`)* |

Click **"Add Environment Variable"** for each, paste the value.
(`TG_GRAPHNAME`, `TG_TGCLOUD`, and `GEMINI_MODEL` come from `render.yaml`
automatically — don't re-add them.)

### 6. Click "Create Web Service"

Render starts building. You'll see a live log stream:
- Pulling base image → `python:3.12-slim`
- Installing dependencies → `uv sync` (~3-5 min first time)
- Building image
- Starting `uvicorn`

When you see `Uvicorn running on http://0.0.0.0:PORT`, it's live.

### 7. Verify the URL

At the top of the Render service page you'll see a URL like:
```
https://autograph-backend.onrender.com
```

Test it:
```powershell
curl https://autograph-backend.onrender.com/api/health
```

Expected:
```json
{"status":"ok","version":"0.1.0","use_cases":[...]}
```

**Save this URL** — you'll paste it into Vercel in Part 2.

---

## Part 2 — Frontend on Vercel (~5 min)

### 1. Sign up at <https://vercel.com>

- "**Continue with GitHub**" — same as Render.
- After sign-in, click **"Add New..."** → **"Project"**.

### 2. Import the repo

- Find `devanshu-tg/schema_creation_agent` → **Import**.
- If it doesn't show, click **"Adjust GitHub App permissions"** → add the repo.

### 3. Configure the project

Vercel auto-detects Next.js, but the project is in a **subdirectory**
(`frontend/`), so you have to point it there:

| Field | Value |
|---|---|
| **Root Directory** | click **Edit** → pick `frontend` |
| **Framework Preset** | `Next.js` (auto-detected) |
| **Build Command** | leave default (`npm run build`) |
| **Output Directory** | leave default (`.next`) |

### 4. Add the environment variable

Expand **"Environment Variables"** at the bottom of the form:

| Key | Value |
|---|---|
| `NEXT_PUBLIC_API_BASE` | `https://autograph-backend.onrender.com/api` |

(Replace with the actual Render URL from Part 1, Step 7.)

### 5. Click "Deploy"

Vercel builds + deploys in ~60 seconds.

You'll get a URL like:
```
https://schema-creation-agent.vercel.app
```

That's the URL to share with your team.

---

## Part 3 — Verify end-to-end

1. Open the Vercel URL in a browser.
2. The Savanna AI panel should load. Drop a CSV in.
3. Type "design me a schema" — agent calls Render backend → Gemini → TG MCP.
4. Check the Network tab — `/api/workspaces/...` requests should hit
   `autograph-backend.onrender.com`.

---

## What about free-tier gotchas?

### Cold starts on Render
Free Render web services **spin down after 15 minutes of inactivity**. The
first request after that takes **~30 seconds** to wake up (container boots,
deps load, Gemini SDK warms up). Subsequent requests are fast.

**Workaround**: keep it warm with a cron job that pings `/api/health` every
10 minutes. (UptimeRobot is free, or use Render's own cron job.)

### Ephemeral storage on Render
The container has no persistent disk on free tier. `build/workspaces/<id>/`
disappears whenever the instance restarts. For demo / first-impression
this is fine; for real use you'd want to move workspace state to a real DB.

### One shared TG graph
The `TG_GRAPHNAME=mcp_demo` is single-tenant. Two teammates designing at
once will overwrite each other. Solvable but needs a request-scoped graph
name.

### Re-deploys
- **Backend**: `git push` to `main` → Render rebuilds + redeploys automatically.
- **Frontend**: same — Vercel watches the repo + redeploys on push.

So your dev loop is just: edit → commit → push. ~2-3 minutes to live URL.

---

## Cost summary

Render free: **$0** (with 15-min idle spindown).
Vercel free: **$0** (hobby tier, unlimited).
Gemini API: **paid** by usage. Budget ~$0.50-2 per full schema design.

For a personal demo and a handful of teammates, infrastructure is free.
