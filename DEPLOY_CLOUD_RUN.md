# Deploying the backend to Google Cloud Run

This walks you through deploying the **FastAPI backend** to Cloud Run. The
Next.js frontend deploys separately (Vercel is easiest — see end).

**What you get**: a public HTTPS URL like
`https://autograph-backend-xxxxx-uc.a.run.app/api/health` that scales to
zero (no bill when idle).

---

## 0. One-time prerequisites

### 0a. Install the Google Cloud CLI

**Windows (PowerShell):**
```powershell
winget install --id Google.CloudSDK
# restart your terminal after install
```

Or download from <https://cloud.google.com/sdk/docs/install>.

### 0b. Sign in + pick a project

```powershell
gcloud auth login
gcloud projects create autograph-demo-12345    # or use an existing project
gcloud config set project autograph-demo-12345
```

Project IDs are globally unique — pick something with random digits.

### 0c. Enable the APIs Cloud Run needs

```powershell
gcloud services enable `
  run.googleapis.com `
  cloudbuild.googleapis.com `
  artifactregistry.googleapis.com `
  secretmanager.googleapis.com
```

First time this is run on a new project it takes ~30 seconds.

### 0d. Enable billing on the project

Cloud Run's free tier (**2M requests / month, 360k vCPU-seconds, 180k
GB-seconds**) is generous — for a personal demo you won't pay anything.
But Cloud Run still requires a billing account to be **linked** to the
project, even at $0 usage.

In the Cloud Console: <https://console.cloud.google.com/billing> → link
a billing account to your project. New Google Cloud users get $300 free
credit for 90 days that covers anything beyond the free tier.

---

## 1. Store your secrets in Secret Manager

Don't bake `GEMINI_API_KEY` or TG credentials into the image. Store them
as Cloud Run secrets, then reference them at deploy time.

```powershell
# From the project root, create one secret per sensitive value.
# Replace <VALUE> placeholders with the matching value from your .env.
"<GEMINI_API_KEY-FROM-DOTENV>" | gcloud secrets create gemini-api-key --data-file=-
"<TG_SECRET-FROM-DOTENV>"     | gcloud secrets create tg-secret    --data-file=-
"<TG_HOST-FROM-DOTENV>"       | gcloud secrets create tg-host      --data-file=-
```

---

## 2. Build the container

Cloud Build reads the `Dockerfile` at the repo root and pushes the image
to Artifact Registry.

```powershell
gcloud builds submit --tag gcr.io/$env:PROJECT/autograph-backend
```

(Set `$env:PROJECT` to your project ID, or paste it inline.) First build
takes ~3-5 minutes because uv has to install pandas/pyarrow from scratch.
Subsequent builds with unchanged dependencies are ~30-60 seconds.

---

## 3. Deploy to Cloud Run

```powershell
gcloud run deploy autograph-backend `
  --image gcr.io/$env:PROJECT/autograph-backend `
  --region us-central1 `
  --platform managed `
  --allow-unauthenticated `
  --memory 1Gi `
  --cpu 1 `
  --timeout 600 `
  --max-instances 3 `
  --set-env-vars TG_GRAPHNAME=mcp_demo,TG_TGCLOUD=true,GEMINI_MODEL=gemini-3.1-pro-preview `
  --update-secrets GEMINI_API_KEY=gemini-api-key:latest,TG_SECRET=tg-secret:latest,TG_HOST=tg-host:latest
```

What each flag does:
- `--memory 1Gi` — pandas + Gemini + MCP subprocess need ~700 MB working set
- `--timeout 600` — Cloud Run's default 5-minute request limit; lifted to 10
  because schema-design turns can take 60-120 seconds with Gemini 3.1 Pro
- `--max-instances 3` — caps your bill if traffic spikes; bump for prod
- `--allow-unauthenticated` — anyone with the URL can hit it. **For a real
  prod deploy, drop this flag and put Cloud IAP or your own auth in front**

After ~30 seconds the command prints a URL:
```
Service URL: https://autograph-backend-xxxxx-uc.a.run.app
```

Verify it works:
```powershell
curl https://autograph-backend-xxxxx-uc.a.run.app/api/health
```

You should see `{"status":"ok",...}`.

---

## 4. Point the frontend at the new backend

In `frontend/.env.local`:

```env
NEXT_PUBLIC_API_BASE=https://autograph-backend-xxxxx-uc.a.run.app/api
```

Then either:

### 4a. Run the frontend locally for now
```powershell
cd frontend
npm run dev
```
Teammates can `git clone`, set `NEXT_PUBLIC_API_BASE` in their `.env.local`,
and `npm run dev` to share the deployed backend.

### 4b. Deploy the frontend to Vercel (free tier)
```powershell
npm install -g vercel
cd frontend
vercel        # follow prompts, set NEXT_PUBLIC_API_BASE as a project env var
```

You'll get a URL like `https://autograph.vercel.app`. Share THAT with
teammates — they don't need anything installed.

---

## 5. Redeploy after a code change

The fast path:

```powershell
gcloud builds submit --tag gcr.io/$env:PROJECT/autograph-backend
gcloud run deploy autograph-backend --image gcr.io/$env:PROJECT/autograph-backend --region us-central1
```

Two commands, ~1 minute (subsequent builds are cached). Add this as a
script to `package.json` or a `.cmd` file so you don't retype it.

---

## What's still missing / what to watch out for

1. **No persistent storage.** Cloud Run containers are ephemeral — each
   instance restart wipes `build/workspaces/<id>/`. For your demo this
   is fine (chats / workspaces are session state) but for real
   multi-user use, move workspace state to Cloud Storage or a database.

2. **Single TG tenant.** Everyone hitting the URL writes to the same
   `mcp_demo` graph. Two teammates designing at once will overwrite
   each other. To fix: give each teammate their own graph + thread
   `TG_GRAPHNAME` through per-request.

3. **No authentication.** `--allow-unauthenticated` means anyone with
   the URL can spend your Gemini quota and operate your Savanna graph.
   Add Cloud IAP, Firebase Auth, or a shared bearer token before
   sharing publicly.

4. **CORS is `*`.** Fine while testing; restrict to the Vercel frontend
   origin in `src/tg_schema_agent/api/app.py` before going public.

5. **Cold starts.** First request after idle takes ~3-5 seconds for the
   container + uv + Python to boot. Set `--min-instances 1` (costs
   ~$5-10/month) to keep one instance warm.

---

## Cost expectations (free tier)

For a personal demo with a handful of teammates:
- Cloud Run: $0 (well under the 2M request / 360k vCPU-sec free tier)
- Cloud Build: $0 (120 build-minutes/day free)
- Artifact Registry: $0 (0.5 GB storage free)
- Secret Manager: $0 (first 10k accesses/month free)
- **Gemini API**: not free — `gemini-3.1-pro-preview` is paid per token.
  Schema-design turns burn ~30-80k tokens. Budget ~$0.50-2 per design.

So the total infrastructure bill is $0; your only cost is Gemini.
