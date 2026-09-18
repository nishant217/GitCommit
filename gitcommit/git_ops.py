"""Git repository operations: commit, pull --rebase, push."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from gitcommit.config import Config
from gitcommit.logger import get_logger


class GitError(RuntimeError):
    pass


def _run(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    log = get_logger()
    merged = os.environ.copy()
    if env:
        merged.update(env)
    log.debug("git cmd: %s (cwd=%s)", " ".join(args), cwd)
    result = subprocess.run(
        args,
        cwd=str(cwd),
        env=merged,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise GitError(f"{' '.join(args)} failed: {msg}")
    return result


def ensure_repo(cfg: Config) -> None:
    log = get_logger()
    repo = cfg.repo_path
    repo.mkdir(parents=True, exist_ok=True)

    if not (repo / ".git").exists():
        log.info("Initializing git repo at %s", repo)
        _run(["git", "init", "-b", cfg.branch], cwd=repo)
        _run(["git", "config", "user.name", cfg.author_name], cwd=repo)
        _run(["git", "config", "user.email", cfg.author_email], cwd=repo)

        journal = repo / cfg.journal_file
        if not journal.exists():
            journal.write_text("# Activity journal\n\n", encoding="utf-8")
            _run(["git", "add", cfg.journal_file], cwd=repo)
            _run(
                ["git", "commit", "-m", "Initial commit: activity journal"],
                cwd=repo,
            )

    # Always sync author for contribution credit
    if cfg.author_name:
        _run(["git", "config", "user.name", cfg.author_name], cwd=repo)
    if cfg.author_email:
        _run(["git", "config", "user.email", cfg.author_email], cwd=repo)

    if cfg.remote_url:
        remotes = _run(["git", "remote"], cwd=repo, check=False)
        if "origin" not in (remotes.stdout or ""):
            _run(["git", "remote", "add", "origin", cfg.remote_url], cwd=repo)
        else:
            _run(["git", "remote", "set-url", "origin", cfg.remote_url], cwd=repo)


def _push_env(cfg: Config) -> dict[str, str]:
    """Env for git push; inject token into HTTPS URL via GIT_ASKPASS-free URL rewrite."""
    env: dict[str, str] = {
        "GIT_AUTHOR_NAME": cfg.author_name,
        "GIT_AUTHOR_EMAIL": cfg.author_email,
        "GIT_COMMITTER_NAME": cfg.author_name,
        "GIT_COMMITTER_EMAIL": cfg.author_email,
    }
    return env


def _authenticated_remote(cfg: Config) -> str | None:
    """Return an HTTPS remote URL with embedded token if GITHUB_TOKEN is set."""
    token = cfg.github_token
    if not token:
        return None

    result = _run(["git", "remote", "get-url", "origin"], cwd=cfg.repo_path, check=False)
    if result.returncode != 0:
        return None
    url = (result.stdout or "").strip()
    if not url.startswith("http"):
        return None  # SSH remotes use keys; leave alone

    parsed = urlparse(url)
    # Use x-access-token for GitHub PAT
    netloc = f"x-access-token:{quote(token, safe='')}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def append_journal_entry(cfg: Config, message: str, when: datetime) -> str:
    journal = cfg.repo_path / cfg.journal_file
    journal.parent.mkdir(parents=True, exist_ok=True)
    stamp = when.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    entry = f"- [{stamp}] {message}\n"
    with open(journal, "a", encoding="utf-8") as fh:
        fh.write(entry)
    return entry


def make_commit(cfg: Config, message: str, when: datetime, dry_run: bool = False) -> str:
    log = get_logger()
    entry = append_journal_entry(cfg, message, when)
    if dry_run:
        log.info("[dry-run] Would commit: %s (%s)", message, entry.strip())
        # Undo file change for dry-run
        journal = cfg.repo_path / cfg.journal_file
        if journal.is_file():
            content = journal.read_text(encoding="utf-8")
            if content.endswith(entry):
                journal.write_text(content[: -len(entry)], encoding="utf-8")
        return message

    env = _push_env(cfg)
    paths = [cfg.journal_file]
    try:
        state_file = cfg.state_path
        if state_file.is_file():
            resolved = state_file.resolve()
            repo = cfg.repo_path.resolve()
            if repo == resolved or repo in resolved.parents:
                paths.append(str(resolved.relative_to(repo)))
    except Exception:
        pass

    # -f so state.json is added even if a broad gitignore rule matches
    _run(["git", "add", "-f", *paths], cwd=cfg.repo_path, env=env)
    _run(["git", "commit", "-m", message], cwd=cfg.repo_path, env=env)
    log.info("Committed: %s", message)
    return message


def pull_rebase(cfg: Config) -> None:
    log = get_logger()
    env = _push_env(cfg)
    # Check if origin exists
    remotes = _run(["git", "remote"], cwd=cfg.repo_path, check=False)
    if "origin" not in (remotes.stdout or ""):
        log.warning("No remote 'origin'; skipping pull")
        return

    branch = cfg.branch
    result = _run(
        ["git", "pull", "--rebase", "origin", branch],
        cwd=cfg.repo_path,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        # Abort rebase if started, then raise
        _run(["git", "rebase", "--abort"], cwd=cfg.repo_path, check=False)
        raise GitError(
            f"pull --rebase failed: {(result.stderr or result.stdout or '').strip()}"
        )
    log.info("pulled --rebase from origin/%s", branch)


def push(cfg: Config) -> None:
    log = get_logger()
    remotes = _run(["git", "remote"], cwd=cfg.repo_path, check=False)
    if "origin" not in (remotes.stdout or ""):
        raise GitError("No remote 'origin' configured; set remote_url or add origin")

    env = _push_env(cfg)
    auth_url = _authenticated_remote(cfg)
    args = ["git", "push", "origin", f"HEAD:{cfg.branch}"]
    if auth_url:
        # Push to token URL without rewriting stored remote permanently
        args = ["git", "push", auth_url, f"HEAD:{cfg.branch}"]

    result = _run(args, cwd=cfg.repo_path, env=env, check=False)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        # Redact token if somehow present
        token = cfg.github_token
        if token and token in err:
            err = err.replace(token, "***")
        raise GitError(f"push failed: {err}")
    log.info("Pushed to origin/%s", cfg.branch)


def repo_status_summary(cfg: Config) -> dict[str, str]:
    branch = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cfg.repo_path,
        check=False,
    )
    remote = _run(
        ["git", "remote", "get-url", "origin"],
        cwd=cfg.repo_path,
        check=False,
    )
    head = _run(
        ["git", "log", "-1", "--oneline"],
        cwd=cfg.repo_path,
        check=False,
    )
    return {
        "branch": (branch.stdout or "").strip() or "n/a",
        "remote": (remote.stdout or "").strip() or "n/a",
        "head": (head.stdout or "").strip() or "n/a",
    }
