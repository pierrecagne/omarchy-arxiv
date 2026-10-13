#!/usr/bin/env python3
"""Turn starred arXiv preprints into Markdown reading reports.

Usage: digest.py [--config -] [--background] [reports-dir]

Reads the widget's starred.json, skips anything already reported, and for each
remaining paper downloads its LaTeX source (or the PDF) and hands it to an AI
engine together with a report prompt. One report per preprint; reports.json
records what is done so a re-run with the same stars is a no-op.

The widget passes its settings as one line of JSON on standard input
(--config -, see DEFAULTS), never on the command line: any account can read
that in /proc, and the settings describe the reader. With --background the
script detaches once it has them, so the run outlives the widget. Engines:

  claude   Claude Code (`claude -p`), reading the paper with its file tools.
  codex    Codex (`codex exec`), reading the paper with shell commands.
  command  Any command that takes the prompt on stdin and prints Markdown;
           the paper's text is included in the prompt.

With the sandbox on (the default), Claude Code and Codex can read only the
paper's folder and the extra folders listed in the settings. Claude Code is
confined by its own --restricted mode; Codex runs inside bubblewrap. With it
off they can read whatever the user can. Neither can ever write files: the
report comes back on stdout (or Codex's last-message file).

Everything is written back after each paper, so an interrupted run keeps the
reports it had already finished and retries only the rest.
"""

import fcntl
import gzip
import io
import json
import os
import shlex
import shutil
import tarfile
import tempfile
import re
import subprocess
import sys
import traceback
import time
import urllib.error
import urllib.request
from datetime import datetime

APP_ID = "io.github.pierrecagne.arxiv"
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), APP_ID)
STARRED = os.path.join(STATE_DIR, "starred.json")
REPORTS = os.path.join(STATE_DIR, "reports.json")
LOCK = os.path.join(STATE_DIR, "digest.lock")
LOG = os.path.join(STATE_DIR, "digest.log")

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(HERE, "prompts")
STYLES = {"auto": "auto.md", "theory": "theory.md", "empirical": "empirical.md"}

DEFAULT_REPORTS_DIR = os.path.expanduser("~/Documents/arxiv-reports")
DEFAULT_READER = "a working researcher in the paper's own field"
USER_AGENT = "omarchy-arxiv-assistant/1.0 (+https://github.com/pierrecagne/omarchy-arxiv)"
DOWNLOAD_GAP = 3.0
# Text that is worth handing to the model. Figures and fonts are not.
SOURCE_SUFFIXES = (".tex", ".bbl", ".bib", ".sty", ".cls", ".txt")
MAX_SOURCE_BYTES = 4 * 1024 * 1024
ENGINE_TIMEOUT = 1800
# A custom command gets the paper inline; past this it is cut, with a note.
MAX_INLINE_CHARS = 600000
# Style files only go inline when small: a vendored TikZ library is noise.
MAX_INLINE_STY_BYTES = 50000

DEFAULTS = {
    "reportsDir": DEFAULT_REPORTS_DIR,
    "engine": "claude",
    "model": "",
    "command": "",
    "sandbox": True,
    "extraReadDirs": "",
    "reader": "",
    "style": "auto",
    "promptFile": "",
}

ENGINE_LABELS = {"claude": "Claude Code", "codex": "Codex", "command": "a custom command"}


class EngineError(RuntimeError):
    """A problem with the engine setup rather than with one paper: stop the run."""


def log(message):
    """Nothing watches this script's stdout - the shell launches it detached -
    so the trail of a run has to live in a file. It records the settings, so
    only the user can read it."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + str(message) + "\n")
    except OSError:
        pass


def notify(message, urgency="normal"):
    try:
        subprocess.run(["omarchy-notification-send", "-u", urgency, "arXiv", message],
                       check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def load(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_private(path, text):
    """Replace path with text, readable by the user only. Always a new file,
    so a report written over an older one does not keep its permissions."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".", suffix=".tmp")  # 0600
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def save(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


# ---- settings ---------------------------------------------------------------

def truthy(value):
    return str(value).strip().lower() not in ("false", "off", "0", "no", "")


def normalise_engine(value):
    text = str(value or "").lower()
    if "codex" in text:
        return "codex"
    if "command" in text or "custom" in text:
        return "command"
    return "claude"


def normalise_style(value):
    text = str(value or "").lower()
    for key in ("theory", "empirical", "custom"):
        if key in text:
            return key
    return "auto"


def split_dirs(value):
    """Comma-separated folders, expanded and resolved; missing ones are dropped."""
    out = []
    for raw in str(value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        path = os.path.realpath(os.path.expanduser(raw))
        if os.path.isdir(path):
            out.append(path)
        else:
            log("extra folder not found, ignored: " + raw)
    return out


def parse_config(argv):
    config = dict(DEFAULTS)
    args = [a for a in argv[1:] if a != "--background"]
    if "--config" in args:
        index = args.index("--config")
        source = args[index + 1] if index + 1 < len(args) else ""
        del args[index:index + 2]
        try:
            if source != "-":
                raise ValueError("the settings come on stdin: --config -")
            config.update(json.loads(sys.stdin.readline()))
        except (ValueError, TypeError) as error:
            log("bad --config, using defaults: " + str(error))
    if args:
        config["reportsDir"] = args[0]

    config["reportsDir"] = os.path.expanduser(str(config["reportsDir"] or DEFAULT_REPORTS_DIR))
    config["engine"] = normalise_engine(config["engine"])
    config["style"] = normalise_style(config["style"])
    config["sandbox"] = truthy(config["sandbox"])
    config["extraReadDirs"] = split_dirs(config["extraReadDirs"])
    config["model"] = str(config["model"] or "").strip()
    config["command"] = str(config["command"] or "").strip()
    config["reader"] = str(config["reader"] or "").strip()
    config["promptFile"] = os.path.expanduser(str(config["promptFile"] or "").strip())
    return config


# ---- fetching the paper -----------------------------------------------------

def safe_id(paper_id):
    """An identifier usable as a path component: math/0309285v2 has a slash."""
    return str(paper_id).replace("/", "-")


def slug(title):
    text = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")
    return (text[:60].rstrip("-")) or "untitled"


def fetch_bytes(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def safe_members(tar):
    """Only plain files, only text, and nothing that escapes the directory."""
    for member in tar.getmembers():
        name = member.name
        if not member.isfile():
            continue
        if name.startswith("/") or ".." in name.split("/"):
            continue
        if not name.lower().endswith(SOURCE_SUFFIXES):
            continue
        if member.size > MAX_SOURCE_BYTES:
            continue
        yield member


def pick_main_tex(srcdir):
    """The file carrying \\documentclass, else the largest .tex."""
    candidates = []
    for name in os.listdir(srcdir):
        if not name.lower().endswith(".tex"):
            continue
        full = os.path.join(srcdir, name)
        try:
            head = open(full, errors="replace").read(4000)
        except OSError:
            continue
        candidates.append((("\\documentclass" in head), os.path.getsize(full), name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def fetch_source(paper, workdir):
    """arXiv's e-print endpoint, which is the LaTeX source for most submissions.

    Falls back to the PDF when the submission is PDF-only, when the archive
    holds no .tex, or when anything about the download goes wrong. Returns
    (kind, directory, entry filename).
    """
    paper_id = paper["id"]
    srcdir = os.path.join(workdir, safe_id(paper_id))
    shutil.rmtree(srcdir, ignore_errors=True)
    os.makedirs(srcdir, exist_ok=True)

    try:
        data = fetch_bytes("https://arxiv.org/e-print/" + paper_id)
        if data.startswith(b"%PDF"):
            raise ValueError("e-print is a PDF submission")

        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
                for member in safe_members(tar):
                    target = os.path.join(srcdir, os.path.basename(member.name))
                    with tar.extractfile(member) as source, open(target, "wb") as out:
                        out.write(source.read())
        except tarfile.ReadError:
            # A single-file submission arrives gzipped and bare.
            raw = gzip.decompress(data)
            if raw.startswith(b"%PDF"):
                raise ValueError("e-print is a PDF submission")
            with open(os.path.join(srcdir, "main.tex"), "wb") as out:
                out.write(raw)

        main = pick_main_tex(srcdir)
        if main:
            others = [n for n in sorted(os.listdir(srcdir)) if n != main]
            log("source: LaTeX, main=" + main + ", also " + (", ".join(others) or "nothing"))
            return ("tex", srcdir, main)
        raise ValueError("no .tex in the e-print archive")

    except (OSError, ValueError, EOFError, tarfile.TarError,
            urllib.error.URLError) as error:
        log("source: falling back to PDF (" + str(error)[:120] + ")")

    pdf_name = safe_id(paper_id) + ".pdf"
    pdf_path = os.path.join(srcdir, pdf_name)
    url = paper.get("pdfUrl") or ("https://arxiv.org/pdf/" + paper_id)
    body = fetch_bytes(url)
    if not body.startswith(b"%PDF"):
        raise ValueError("arXiv did not return a PDF")
    with open(pdf_path, "wb") as handle:
        handle.write(body)
    return ("pdf", srcdir, pdf_name)


def page_count(path):
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=30)
        match = re.search(r"^Pages:\s+(\d+)", out.stdout, re.M)
        return int(match.group(1)) if match else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def paper_text(kind, srcdir, entry_name):
    """The paper as one string, for engines that get it inline."""
    if kind == "pdf":
        if shutil.which("pdftotext") is None:
            raise RuntimeError("this paper is PDF-only and pdftotext (poppler) is not installed")
        out = subprocess.run(["pdftotext", "-layout", os.path.join(srcdir, entry_name), "-"],
                             capture_output=True, text=True, timeout=120)
        text = out.stdout
    else:
        names = sorted(os.listdir(srcdir))
        order = [entry_name]
        order += [n for n in names if n != entry_name and n.lower().endswith(".tex")]
        order += [n for n in names if n.lower().endswith((".bbl", ".txt"))]
        order += [n for n in names if n.lower().endswith((".sty", ".cls"))
                  and os.path.getsize(os.path.join(srcdir, n)) <= MAX_INLINE_STY_BYTES]
        parts = []
        for name in order:
            with open(os.path.join(srcdir, name), errors="replace") as handle:
                parts.append("===== " + name + " =====\n" + handle.read())
        text = "\n\n".join(parts)

    if len(text) > MAX_INLINE_CHARS:
        text = text[:MAX_INLINE_CHARS] + "\n\n[... the rest of the paper was cut for length ...]"
    return text


# ---- the prompt -------------------------------------------------------------

SOURCE_NOTES = {
    ("files", "tex"): ("This is the LaTeX source. `\\input`/`\\include` files and the macro "
                       "definitions sit in the same directory, and the bibliography may be in "
                       "a .bbl file - read whatever you need to answer accurately."),
    ("files", "pdf"): "This is a PDF, so work through it in page ranges.",
    ("codex", "pdf"): "This is a PDF; `pdftotext` is available to extract its text.",
    ("inline", "tex"): ("Its LaTeX source is included at the end of this message, file by "
                        "file, between <paper> tags: the main file first, then the other "
                        "files, the bibliography and small style files."),
    ("inline", "pdf"): ("Its text, extracted from the PDF, is included at the end of this "
                        "message between <paper> tags. Formulas may have lost their layout."),
}


def template_text(config):
    if config["style"] == "custom":
        path = config["promptFile"]
        if not path or not os.path.isfile(path):
            raise EngineError("The report style is Custom but the prompt file "
                              + (path or "(not set)") + " does not exist.")
        return open(path, errors="replace").read()
    return open(os.path.join(PROMPTS_DIR, STYLES[config["style"]])).read()


def build_prompt(config, kind, entry_name, inline_text=None):
    engine = config["engine"]
    if inline_text is not None:
        note = SOURCE_NOTES[("inline", kind)]
    elif engine == "codex" and kind == "pdf":
        note = SOURCE_NOTES[("codex", "pdf")]
    else:
        note = SOURCE_NOTES[("files", kind)]

    dirs = config["extraReadDirs"]
    if dirs and inline_text is None:
        note += ("\n\nThe reader's own material is in " + ", ".join("`" + d + "`" for d in dirs)
                 + ". Read it where it helps relate the paper to their work, and say so when "
                 "you do; the report stays about the paper.")

    prompt = (template_text(config)
              .replace("{READER}", config["reader"] or DEFAULT_READER)
              .replace("{SOURCE}", entry_name)
              .replace("{SOURCE_NOTE}", note)
              .replace("{PDF}", entry_name))
    if inline_text is not None:
        prompt += "\n\n<paper>\n" + inline_text + "\n</paper>\n"
    return prompt


# ---- engines ----------------------------------------------------------------

_claude_help = None


def claude_supports_restricted():
    global _claude_help
    if _claude_help is None:
        try:
            _claude_help = subprocess.run(["claude", "--help"], capture_output=True,
                                          text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            _claude_help = ""
    return "--restricted" in _claude_help


def claude_argv(config):
    argv = ["claude", "-p", "--output-format", "text", "--no-session-persistence",
            # Read-only tools in both modes: no shell, no writes, no web.
            "--tools", "Read,Grep,Glob"]
    if config["sandbox"]:
        if not claude_supports_restricted():
            raise EngineError("This Claude Code has no --restricted mode, so it cannot be "
                              "sandboxed. Update Claude Code, or turn the sandbox off.")
        # --restricted confines the file tools to the working directory and
        # --add-dir folders, and ignores the user's own settings files;
        # dontAsk turns anything that would need approval into a refusal.
        argv += ["--restricted", "--strict-mcp-config", "--permission-mode", "dontAsk"]
    else:
        # Pre-approved, so reads outside the paper folder do not stall on a
        # permission prompt that nobody can answer.
        argv += ["--allowedTools", "Read,Grep,Glob"]
    for folder in config["extraReadDirs"]:
        argv += ["--add-dir", folder]
    if config["model"]:
        argv += ["--model", config["model"]]
    return argv


def install_root(executable):
    """The folder an engine's binary lives in, two levels up from the file
    (…/<version>/bin/codex -> …/<version>), so the jail can see the install."""
    real = os.path.realpath(executable)
    return os.path.dirname(os.path.dirname(real))


def jail(argv, workdir, read_only, read_write):
    """Run argv inside bubblewrap with the home directory replaced by an empty
    tmpfs: only the system directories, the paper folder and the listed
    folders are visible."""
    if shutil.which("bwrap") is None:
        raise EngineError("Sandboxing Codex needs bubblewrap. Install it with "
                          "`omarchy pkg add bubblewrap`, or turn the sandbox off.")
    home = os.path.expanduser("~")
    args = ["bwrap", "--die-with-parent", "--new-session",
            "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
            "--ro-bind-try", "/opt", "/opt",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/bin", "/sbin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib", "/lib64",
            "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/tmp", "--tmpfs", "/var/tmp", "--tmpfs", "/run",
            # /etc/resolv.conf points here when systemd-resolved is in use.
            "--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve",
            "--tmpfs", home]
    for folder in read_only:
        args += ["--ro-bind", folder, folder]
    for folder in read_write:
        args += ["--bind", folder, folder]
    args += ["--chdir", workdir, "--"]
    return args + argv


def codex_argv(config, workdir, out_file):
    executable = shutil.which("codex")
    # By its real path: PATH may go through a symlink (mise's `latest`) whose
    # target is all the jail exposes.
    argv = [os.path.realpath(executable) if config["sandbox"] else "codex", "exec", "--skip-git-repo-check", "--ephemeral", "--color", "never",
            "-s", "read-only", "-C", workdir, "-o", out_file]
    if config["model"]:
        argv += ["-m", config["model"]]
    argv += ["-"]
    if not config["sandbox"]:
        return argv

    codex_home = os.path.realpath(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))
    read_only = [install_root(executable)] + config["extraReadDirs"]
    # A Node launcher (npm install) needs its interpreter as well.
    try:
        with open(os.path.realpath(executable), "rb") as handle:
            if b"node" in handle.read(64).split(b"\n")[0] and shutil.which("node"):
                read_only.append(install_root(shutil.which("node")))
    except OSError:
        pass
    return jail(argv, workdir, read_only, [workdir, codex_home])


def engine_binary(config):
    if config["engine"] == "command":
        try:
            words = shlex.split(config["command"])
        except ValueError:
            words = []
        return words[0] if words else ""
    return config["engine"]


def summarise(config, kind, entry_name, workdir):
    engine = config["engine"]
    out_file = None
    if engine == "command":
        if not config["command"]:
            raise EngineError("The engine is a custom command, but no command is set.")
        prompt = build_prompt(config, kind, entry_name, paper_text(kind, workdir, entry_name))
        argv = ["sh", "-c", config["command"]]
    elif engine == "codex":
        prompt = build_prompt(config, kind, entry_name)
        out_file = os.path.join(workdir, ".report.md")
        argv = codex_argv(config, workdir, out_file)
    else:
        prompt = build_prompt(config, kind, entry_name)
        argv = claude_argv(config)

    log("running " + engine + " in " + workdir + " on " + entry_name + " (" + kind + "): "
        + " ".join(shlex.quote(a) for a in argv))
    # The prompt goes on stdin: --tools and --add-dir are variadic and would
    # swallow a positional prompt that followed them.
    result = subprocess.run(argv, input=prompt, cwd=workdir, capture_output=True,
                            text=True, timeout=ENGINE_TIMEOUT)
    log(engine + " exit=" + str(result.returncode) + " stdout=" + str(len(result.stdout))
        + "b stderr=" + repr(result.stderr[-400:]))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or engine + " failed").strip()[-200:])

    if out_file:
        try:
            body = open(out_file, errors="replace").read().strip()
        except OSError:
            body = ""
    else:
        body = result.stdout.strip()
    if len(body) < 400:
        raise RuntimeError(ENGINE_LABELS[engine] + " returned too little to be a report")
    return body


def header(paper, pages, kind, config):
    authors = ", ".join(paper.get("authors") or []) or "unknown"
    by = ENGINE_LABELS[config["engine"]]
    if config["engine"] == "command":
        by = "`" + engine_binary(config) + "`"
    if config["model"]:
        by += " (" + config["model"] + ")"
    bits = [
        "# " + str(paper.get("title") or paper["id"]),
        "",
        authors + "  ",
        "arXiv:" + paper["id"] + "  ·  " + str(paper.get("primary") or "") +
        "  ·  submitted " + str(paper.get("published") or "")[:10] +
        ("  ·  " + str(pages) + " pages" if pages else "") + "  ",
        str(paper.get("absUrl") or ""),
        "",
        "*Report generated " + datetime.now().strftime("%Y-%m-%d %H:%M") + " by " + by +
        (" from the LaTeX source." if kind == "tex" else " from the PDF.") + "*",
        "",
        "---",
        "",
    ]
    return "\n".join(bits)


def process(paper, config, workdir):
    paper_id = paper["id"]
    kind, srcdir, entry_name = fetch_source(paper, workdir)
    pages = page_count(os.path.join(srcdir, entry_name)) if kind == "pdf" else 0
    body = summarise(config, kind, entry_name, srcdir)

    # A report can quote the user's own notes, so it is theirs alone, and so
    # is a folder made for it. A folder they chose keeps the mode they gave it.
    reports_dir = config["reportsDir"]
    os.makedirs(reports_dir, mode=0o700, exist_ok=True)
    report_path = os.path.join(reports_dir, safe_id(paper_id) + "-" + slug(paper.get("title")) + ".md")
    write_private(report_path, header(paper, pages, kind, config) + body + "\n")
    shutil.rmtree(srcdir, ignore_errors=True)
    return report_path


def detach():
    """Carry on in a session of our own, with no tie to the widget's Process,
    which kills its child when the bar reloads."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    os.close(devnull)


def main(argv):
    config = parse_config(argv)
    if "--background" in argv:
        detach()
    reports_dir = config["reportsDir"]
    os.makedirs(STATE_DIR, exist_ok=True)
    log("settings: engine=%s model=%s sandbox=%s style=%s reader=%r extra=%s reports=%s"
        % (config["engine"], config["model"] or "default", config["sandbox"], config["style"],
           config["reader"] or "default", config["extraReadDirs"], reports_dir))

    # One run at a time: a second keypress while the first run is working
    # should do nothing rather than fetch and summarise everything twice.
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        notify("A summary run is already going.")
        return 0

    binary = engine_binary(config)
    if not binary or shutil.which(binary) is None:
        notify("The " + (binary or "engine") + " command is not on PATH, so no report "
               "can be written.", "critical")
        return 1

    starred = load(STARRED, {}).get("papers") or {}
    reports = load(REPORTS, {}).get("reports") or {}

    # A report whose file has been deleted counts as not done, so removing the
    # markdown and re-running regenerates it.
    pending = [p for pid, p in sorted(starred.items())
               if pid not in reports or not os.path.exists(reports[pid].get("path", ""))]

    if not pending:
        notify("Nothing new to summarise." if starred else "No starred preprints.")
        return 0

    label = ENGINE_LABELS[config["engine"]]
    log("run start: " + str(len(pending)) + " pending, engine " + config["engine"]
        + (" (sandboxed)" if config["sandbox"] else " (no sandbox)")
        + ", reports -> " + reports_dir)
    notify("Summarising " + str(len(pending)) + " preprint" + ("s" if len(pending) > 1 else "")
           + " with " + label + "…")

    # Start from a clean scratch dir: a run killed mid-paper leaves sources
    # behind, and the lock means nothing else is using it. Private, since
    # Codex leaves its report in it.
    workdir = os.path.join(STATE_DIR, "work")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, mode=0o700, exist_ok=True)
    os.chmod(workdir, 0o700)

    done, failed = 0, []
    for index, paper in enumerate(pending):
        if index > 0:
            time.sleep(DOWNLOAD_GAP)
        try:
            log("processing " + paper["id"])
            path = process(paper, config, workdir)
        except EngineError as error:
            # The setup is wrong, not this paper: the next one would fail too.
            log("ENGINE " + str(error))
            notify(str(error), "critical")
            return 1
        except Exception as error:  # one bad paper must not sink the whole run
            failed.append((paper["id"], str(error)[:120]))
            log("FAILED " + paper["id"] + ": " + repr(error)[:400])
            log(traceback.format_exc()[-800:])
            print("FAILED " + paper["id"] + ": " + str(error)[:200], file=sys.stderr)
            continue

        reports[paper["id"]] = {
            "path": path,
            "title": paper.get("title", ""),
            "engine": config["engine"],
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
        }
        # Written after every paper: an interrupted run keeps what it finished.
        save(REPORTS, {"version": 1, "reports": reports})
        done += 1
        log("wrote " + path)

    if done and not failed:
        notify(str(done) + " report" + ("s" if done > 1 else "") + " ready in " + reports_dir)
    elif done:
        notify(str(done) + " done, " + str(len(failed)) + " failed: " + failed[0][1], "critical")
    else:
        notify("Could not summarise: " + failed[0][1], "critical")

    return 0 if done or not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
