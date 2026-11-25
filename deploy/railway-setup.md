# PRESENCE AI on Railway — first-time deploy, step by step

Written for someone who has never deployed anything. About 25 minutes.

The repository already contains everything Railway needs: a `Dockerfile`, a
`railway.json`, and a `/api/health` endpoint Railway uses as its health check.
There are no build settings to fill in and nothing to install.

> **Read this first.** Conversation history is stored in a file on disk. Without
> a Railway **volume** (step 6) that file is wiped on every deploy. Step 6 is
> what stops that being true, and the limitation is written up honestly in the
> PDF either way.

---

## Step 1 — Create the Railway account

1. Open <https://railway.com> and click **Login**.
2. Choose **Login with GitHub** and authorize Railway.

The free trial gives about **$5 of usage**. Before sharing the link with
judges, set a limit under **Account Settings → Usage**, and check whether the
service would sleep once the credit runs out — a sleeping link still opens, but
the first request is slow.

## Step 2 — Get the code onto GitHub

Railway builds from GitHub, not from your laptop.

- Repository: <https://github.com/siudro/presence-ai>
- Branch: **`main`**

If your Mac has newer changes than GitHub:

```bash
cd presence-ai
git add -A
git commit -m "Prepare deployment"
git push origin main
```

Check the repository online: `Dockerfile` and `railway.json` must appear at the
**top level**, next to the `presence` folder. That is the signal the deployment
files are on GitHub.

## Step 3 — Create the project

1. Railway → **New Project** → **Deploy from GitHub repo**.
2. If prompted, **Configure GitHub App** and grant access to
   `siudro/presence-ai` only.
3. Select **`siudro/presence-ai`**.

The first build starts immediately. **Without variables the deploy will not
finish cleanly** — step 5 fixes that. Don't go looking for the bug yet.

## Step 4 — Watch the build

Service → **Deployments**. A green **build** stage means the Dockerfile built.
If the build fails at a `COPY`, the `Dockerfile` is in the wrong place: it must
sit at the repository root, next to `railway.json`.

## Step 5 — Add the environment variables

Service → **Variables** → **+ New Variable** (or **Raw Editor** to paste them
all at once). Use these names exactly:

| Variable | Value | Required |
|---|---|---|
| `GEMINI_API_KEY` | key from <https://aistudio.google.com/apikey>, starts `AIza` | **yes** — AI chat answers |
| `COHERE_API_KEY` | key from <https://dashboard.cohere.com/api-keys> | **yes** — speech-to-text |
| `LLM_CONFIG` | `llm_config.deploy.json` | **yes** — selects the committed Gemini config |
| `DATA_DIR` | `/data` | **yes** — must match the volume mount in step 6 |
| `PORT` | `8080` | optional — Railway injects its own; only set it if step 8 fails |

**Do not set `HOST`.** The app already binds `0.0.0.0`, which is what Railway
requires. Setting `HOST=127.0.0.1` makes the health check fail and the deploy
loop.

Saving a variable triggers a redeploy automatically.

### Why `LLM_CONFIG` exists

On a Mac you paste a key in Settings and the app writes `llm_config.json`. A
deployed server has no terminal to paste into, so the repository ships
`presence/llm_config.deploy.json` instead. **That file contains only the name of
the environment variable**, never a key — it is safe to show anyone.

## Step 6 — Add a volume, so history survives

1. In the project canvas: **+ New** → **Volume** (or right-click → New Volume).
2. Attach it to the PRESENCE AI service.
3. Set the **mount path** to `/data`.

`DATA_DIR=/data` is what makes `sessions.jsonl` land on this volume instead of
inside the container. Without it, the progress page is empty after every deploy.

## Step 7 — Generate the public link

1. Service → **Settings** → **Networking**.
2. **Public Networking** → **Generate Domain**.
3. Accept the suggested port. Railway gives you something like
   `https://presence-ai-production-xxxx.up.railway.app`.

That HTTPS address is the **Link to the Chatbot** for the PDF.

## Step 8 — Prove it works (from the public URL, not localhost)

```bash
URL=https://your-url-here.up.railway.app

curl -s $URL/api/health
# {"ok":true,"service":"presence-ai","version":"1.0",...}

curl -s $URL/api/key
# "configured": true     -> the Cohere key was found
# "llm": {"configured": true, ...}  -> the AI provider has a key

curl -s -o /dev/null -w "%{http_code}\n" $URL/
# 200
```

Then open the URL in a **private browser window**, so no local cookie hides a
problem:

1. Start a recording and speak for ~20 seconds.
2. The transcript appears → Cohere is wired correctly.
3. Ask the coach a question. The line under the answer must read
   *"Answered by your configured LLM endpoint."* If it reads *"Answered from
   your last measurements"*, the AI key is not being read.
4. Open the progress screen → `/api/history` and the volume both work.
5. In Railway press **Redeploy**, then reload the progress screen. Old sessions
   are still there → the volume is doing its job.

If 2 or 3 fails, open **Deployments → View Logs**. The startup banner prints
which keys it found (last four characters only) and the exact `api_key_env`
name it is looking for.

## Step 9 — Microphone on a judge's machine

Browsers only allow microphone access on **HTTPS** or `localhost`. Railway's
domain is HTTPS, so this works. If a judge sees "microphone blocked", they need
to allow the mic in their browser's site settings — it is not an app bug.

---

## Optional — automatic tests and deploys on every push

**This file is not in the repository yet.** The agent that prepared the deploy
does not have GitHub permission to add a workflow file, so it has to be added
by you, once. Steps 1–2 below are what add it.

### 1. Add the workflow file

In the GitHub repository, click **Add file → Create new file**, name it
`.github/workflows/tests.yml` (typing the slashes creates the folders), and
paste the contents of the `tests.yml` block handed to you with this task.
Commit it to `main`.

That is it for the tests: from now on every push runs
`python3 run_all_tests.py`, builds the Docker image, boots it and checks
`/api/health`. A red run shows as a red X on the commit.

### 2. Let it deploy for you (optional)

1. Railway → **Account Settings** → **Tokens** → **Create Token**, scoped to
   the project. Copy it.
2. GitHub repo → **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**: name `RAILWAY_TOKEN`, value the token.
3. If the Railway service is not called `presence-ai`, add a repository
   **variable** `RAILWAY_SERVICE` with its name.

Without the secret, the tests still run and the deploy job skips and says so —
nothing fails. A red test run never reaches Railway, because the deploy job
waits for the test and image jobs to pass.

> Railway also redeploys on its own whenever `main` changes, once the project
> is connected to the repository in step 3. The workflow matters because it is
> what stops a broken commit from reaching judges.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build fails at `COPY presence/` | `Dockerfile` not at the repo root | move it next to `railway.json` |
| Deploy restarts with `Permission denied: /data` | volume owned by root | the entrypoint chowns it; if it persists, recreate the volume with mount path `/data` |
| Health check times out | `HOST` variable set, or the app not binding `0.0.0.0` | delete `HOST` |
| Page loads, chat says "Answered from your last measurements" | `GEMINI_API_KEY` or `LLM_CONFIG` missing | add both, redeploy |
| No transcript appears | `COHERE_API_KEY` missing | add it, redeploy |
| History empties after a deploy | no volume, or `DATA_DIR` not `/data` | step 6 |
| Worked, then stopped after days | free trial credit used up | Railway → Usage; upgrade or claim more |
