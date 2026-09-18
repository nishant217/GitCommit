# GitCommit Bot

Randomized daily GitHub commits so your contribution graph stays active — with a few natural skip days each month.

**Recommended: run it on GitHub Actions (cloud).** Your laptop can stay off. No VPS, no cron on your PC.

---

## Cloud setup (set and forget)

This repo is a standalone project. Push it to GitHub once, add 3 secrets, and Actions does the rest every day.

### 1. Create / push the GitHub repo

```bash
cd GitCommit
git init
git add .
git commit -m "Initial commit: GitCommit Bot"
git branch -M main
# create an empty repo on GitHub, then:
git remote add origin git@github.com:YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Use a **public or private repo you own**. Commits on the default branch (`main`) count toward your graph.

### 2. Create a Personal Access Token

GitHub → **Settings** → **Developer settings** → **Personal access tokens** → generate a token (classic) with **`repo`** scope.

> Important: the default Actions `GITHUB_TOKEN` commits as `github-actions[bot]`, which **does not** paint your green squares. You need your own PAT.

### 3. Add repository secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → New secret:

| Secret | Value |
|--------|--------|
| `GH_PAT` | your personal access token |
| `COMMIT_AUTHOR_NAME` | your GitHub name |
| `COMMIT_AUTHOR_EMAIL` | email linked to GitHub (or `ID+user@users.noreply.github.com`) |

### 4. Edit `config.cloud.yaml` (optional)

Set your timezone, commit window, min/max commits, skip days. Commit and push.

### 5. Enable Actions

Open the **Actions** tab → enable workflows if asked.  
Trigger a test: **Daily contribution commits** → **Run workflow** → enable **force** → Run.

After that, leave it alone. GitHub runs the workflow hourly during the day window; the bot:

1. Picks **3–4 skip days** for the month (stable)  
2. On other days, waits until a **random time** in your window  
3. Makes **1–5 commits** to `activity.log` and pushes as **you**

No local machine, no deploy server, no monthly bill beyond GitHub’s free Actions minutes.

---

## How the schedule works

| Rule | Behavior |
|------|----------|
| Monthly | 3–4 random days with **zero** commits |
| Other days | 1 run at a random time between `commit_window_start` and `end` |
| Each run | Random 1–5 commits, short gaps, varied messages |
| Cloud | GitHub Actions wakes hourly; commits only when that random time has arrived |

You do **not** need to deploy a website or keep a PC on.

---

## Optional: run on your PC instead

Only if you prefer local cron (not needed for cloud):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
gitcommit init    # installs local cron
```

See below for local CLI details.

---

## Local CLI (optional)

```bash
pip install -e .
gitcommit status
gitcommit dry-run --now
gitcommit run --now
gitcommit cloud-run --force   # same logic Actions uses
gitcommit pause / resume
gitcommit dashboard          # local calendar UI only
```

Config resolution: `--config` → `GITCOMMIT_CONFIG` → `./config.yaml` → `~/.gitcommit-bot/config.yaml`.

### Configuration keys

See [`config.cloud.yaml`](config.cloud.yaml) / [`config.example.yaml`](config.example.yaml):

| Key | Meaning |
|-----|---------|
| `timezone` | IANA TZ, e.g. `Asia/Kolkata` |
| `commit_window_start` / `end` | Random daily window |
| `min_commits` / `max_commits` | Commits per active day |
| `skip_days_min` / `max` | Blank days per month |
| `author_name` / `author_email` | Must match GitHub (cloud: use secrets) |

---

## Adjusting the Actions time window

In [`.github/workflows/daily-commit.yml`](.github/workflows/daily-commit.yml) the cron is:

```yaml
- cron: "12 3-17 * * *"   # hours 03–17 UTC
```

For **Asia/Kolkata (UTC+5:30)** that is roughly **08:30–22:30** local.  
Change the UTC hour range if you live elsewhere, then push.

---

## Pause without deleting the repo

- Add a file `.gitcommit-data/PAUSED` and push, **or**
- Disable the workflow under Actions → workflow → `...` → Disable

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Commits appear but graph stays empty | Author email must match GitHub; use `GH_PAT` not default token; commits on default branch |
| Workflow fails on push | Check `GH_PAT` has `repo` scope and isn’t expired |
| No run today | Check Actions logs; may be a skip day (`gitcommit status` / state in `.gitcommit-data/`) |
| Want a commit right now | Actions → Run workflow → **force** = true |

---

## Project layout

```
GitCommit/
├── config.cloud.yaml              # cloud settings
├── config.example.yaml            # local template
├── activity.log                   # file the bot appends to
├── .github/workflows/daily-commit.yml
└── gitcommit/                     # Python bot
```

## License

MIT
