#!/usr/bin/env python3
import getpass
import html
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from secrets import token_hex


ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
WORK = ROOT / "workspaces"
KEY = ROOT / ".ssh" / "id_ed25519"
JOBS = {}


def api(method, **data):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                for k, v in data.items()}).encode(),
    )
    with urllib.request.urlopen(req, timeout=40) as res:
        out = json.load(res)
    if not out["ok"]:
        raise RuntimeError(out.get("description", "Telegram API error"))
    return out["result"]


def repo_url(repo):
    if re.fullmatch(r"[\w.-]+/[\w.-]+(?:\.git)?", repo):
        return f"git@github.com:{repo.removesuffix('.git')}.git"
    if repo.startswith("git@") or repo.startswith("ssh://"):
        return repo
    raise ValueError("repo must be user/repo, git@host:path, or ssh://...")


def commit_url(repo, commit):
    if repo.startswith("git@github.com:"):
        path = repo.split(":", 1)[1]
    elif repo.startswith("ssh://") and urllib.parse.urlsplit(repo).hostname == "github.com":
        path = urllib.parse.urlsplit(repo).path.lstrip("/")
    else:
        return ""
    return f"https://github.com/{path.removesuffix('.git')}/commit/{commit}"


def command(job, args, cwd=None, env=None, log=None):
    out = open(log, "ab") if log else subprocess.DEVNULL
    try:
        if job["stopped"]:
            return -signal.SIGKILL
        p = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        job["proc"] = p
        if job["stopped"]:
            os.killpg(p.pid, signal.SIGKILL)
        return p.wait()
    finally:
        job["proc"] = None
        if log:
            out.close()


def send(text, reply=None, markup=None, parse_mode=None):
    data = {"chat_id": CHAT, "text": text[:4000]}
    if reply:
        data["reply_parameters"] = {"message_id": reply}
    if markup:
        data["reply_markup"] = markup
    if parse_mode:
        data["parse_mode"] = parse_mode
    return api("sendMessage", **data)


def cleanup_workspace(path, log, final):
    """Remove the per-job clone and its temporary output files."""
    # Keep this guard close to the deletion so a future caller cannot
    # accidentally turn cleanup into a recursive delete of another path.
    if path.parent != WORK or not path.name.startswith("job-"):
        raise ValueError(f"refusing to clean unexpected workspace: {path}")
    shutil.rmtree(path, ignore_errors=True)
    for artifact in (log, final):
        try:
            artifact.unlink()
        except OSError:
            pass


def work(job, prompt, reply):
    jid = job["id"]
    path = WORK / f"job-{time.strftime('%Y%m%d-%H%M%S')}-{jid}"
    log = path.with_suffix(".log")
    final = path.with_suffix(".final")
    ssh = f"ssh -i {shlex.quote(str(KEY))} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    env = os.environ | {"GIT_SSH_COMMAND": ssh}
    try:
        if command(job, ["git", "clone", "--", REPO, str(path)], env=env, log=log):
            raise RuntimeError("git clone failed")
        for key, value in (("user.name", "cod-claw"),
                           ("user.email", "328166668+cod-claw@users.noreply.github.com")):
            if command(job, ["git", "config", key, value], cwd=path, log=log):
                raise RuntimeError(f"git config {key} failed")
        rc = command(job, ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox",
                           "--dangerously-bypass-hook-trust", "--color", "never", "-C", str(path),
                           "--output-last-message", str(final), prompt], log=log)
        if job["stopped"]:
            send("Agent interrupted.", reply)
            return
        if rc:
            raise RuntimeError(f"codex exited with status {rc}")
        if command(job, ["git", "add", "-A"], cwd=path, log=log):
            raise RuntimeError("git add failed")
        changed = command(job, ["git", "diff", "--cached", "--quiet"], cwd=path, log=log)
        if changed not in (0, 1):
            raise RuntimeError("git diff failed")
        if changed:
            title = " ".join(prompt.split())[:60] or "agent changes"
            if command(job, ["git", "commit", "-m", f"cod: {title}"], cwd=path, log=log):
                raise RuntimeError("git commit failed")
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                         cwd=path, text=True).strip()
        if command(job, ["git", "push"], cwd=path, env=env, log=log):
            raise RuntimeError("git push failed")
        if job["stopped"]:
            send("Agent interrupted.", reply)
            return
        answer = final.read_text().strip()[:3900] if final.exists() else "Agent finished."
        url = commit_url(REPO, commit)
        if url:
            suffix = f'\n\nPushed commit: <a href="{html.escape(url, quote=True)}">{commit}</a>'
            while len(html.escape(answer)) + len(suffix) > 4000:
                answer = answer[:-100]
            send(html.escape(answer) + suffix, reply, parse_mode="HTML")
        else:
            send(f"{answer}\n\nPushed commit: {commit}", reply)
    except Exception as e:
        if job["stopped"]:
            send("Agent interrupted.", reply)
        else:
            tail = log.read_text(errors="replace")[-2500:].strip() if log.exists() else ""
            send(f"Agent failed: {e}" + (f"\n\n{tail}" if tail else ""), reply)
    finally:
        cleanup_workspace(path, log, final)
        JOBS.pop(jid, None)
        try:
            api("deleteMessage", chat_id=CHAT, message_id=job["message"])
        except Exception:
            pass


def start(message, username):
    text = message.get("text", "")
    prompt = re.sub(fr"@{re.escape(username)}\b", "", text,
                    flags=re.IGNORECASE).strip()
    if not prompt:
        send("Send me a task for Codex.", message["message_id"])
        return
    jid = token_hex(4)
    status = send("Spinning up agent…", message["message_id"], {
        "inline_keyboard": [[{"text": "Interrupt", "callback_data": f"stop:{jid}"}]]
    })
    job = {"id": jid, "message": status["message_id"], "proc": None, "stopped": False}
    JOBS[jid] = job
    threading.Thread(target=work, args=(job, prompt, message["message_id"]), daemon=True).start()


def stop(query):
    jid = query.get("data", "").removeprefix("stop:")
    job = JOBS.get(jid)
    if not job:
        api("answerCallbackQuery", callback_query_id=query["id"], text="Agent already finished")
        return
    job["stopped"] = True
    p = job.get("proc")
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    api("answerCallbackQuery", callback_query_id=query["id"], text="Interrupting agent…")


def main():
    global TOKEN, CHAT, REPO
    missing = [x for x in ("git", "ssh-keygen", "codex") if not shutil.which(x)]
    if missing:
        raise SystemExit("missing commands: " + ", ".join(missing))

    cfg = dict(line.strip().split("=", 1) for line in ENV.read_text().splitlines()
               if line.strip() and not line.lstrip().startswith("#") and "=" in line) \
        if ENV.exists() else {}
    prompts = (("TELEGRAM_BOT_TOKEN", "Telegram bot token: ", True),
               ("TELEGRAM_CHAT_ID", "Telegram chat id: ", False),
               ("DEFAULT_REPO", "Default repo: ", False))
    for key, label, secret in prompts:
        if not cfg.get(key):
            cfg[key] = (getpass.getpass(label) if secret else input(label)).strip()
    if not all(cfg.get(key) for key, _, _ in prompts):
        raise SystemExit("configuration values cannot be empty")
    ENV.write_text("".join(f"{key}={cfg[key]}\n" for key, _, _ in prompts))
    os.chmod(ENV, 0o600)
    TOKEN, CHAT = cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_CHAT_ID"]
    REPO = repo_url(cfg["DEFAULT_REPO"])

    KEY.parent.mkdir(exist_ok=True)
    if not KEY.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                        "cod-agent", "-f", str(KEY)], check=True)
    elif not KEY.with_suffix(".pub").exists():
        KEY.with_suffix(".pub").write_bytes(
            subprocess.check_output(["ssh-keygen", "-y", "-f", str(KEY)]) + b"\n")
    os.chmod(KEY, 0o600)
    print("\nAdd this SSH key to GitHub:\n")
    print(KEY.with_suffix(".pub").read_text().strip(), "\n", flush=True)

    me = api("getMe")
    print(f"Listening as @{me['username']} for chat {CHAT}", flush=True)
    WORK.mkdir(exist_ok=True)
    offset = 0
    while True:
        try:
            for update in api("getUpdates", offset=offset, timeout=30,
                              allowed_updates=["message", "callback_query"]):
                offset = update["update_id"] + 1
                if "message" in update and str(update["message"]["chat"]["id"]) == CHAT:
                    start(update["message"], me["username"])
                elif "callback_query" in update:
                    q = update["callback_query"]
                    if str(q.get("message", {}).get("chat", {}).get("id")) == CHAT:
                        stop(q)
        except KeyboardInterrupt:
            for job in list(JOBS.values()):
                job["stopped"] = True
                p = job.get("proc")
                if p and p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            print("Stopped.", flush=True)
            return
        except Exception as e:
            print(f"poll error: {e}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
