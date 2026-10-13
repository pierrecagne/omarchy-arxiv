#!/usr/bin/env python3
"""Turn starred arXiv preprints into Markdown reading reports.

Usage: digest.py [reports-dir]

Reads the widget's starred.json, skips anything already reported, and for each
remaining paper downloads the PDF and hands it to `claude -p` together with
report-prompt.md. One report per preprint; reports.json records what is done so
a re-run with the same stars is a no-op.

Everything is written back after each paper, so an interrupted run keeps the
reports it had already finished and retries only the rest.
"""

import fcntl
import gzip
import io
import json
import os
import shutil
import tarfile
import re
import subprocess
import sys
import traceback
import time
import urllib.error
import urllib.request
from datetime import datetime

STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "omarchy-arxiv")
STARRED = os.path.join(STATE_DIR, "starred.json")
REPORTS = os.path.join(STATE_DIR, "reports.json")
LOCK = os.path.join(STATE_DIR, "digest.lock")
LOG = os.path.join(STATE_DIR, "digest.log")

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "report-prompt.md")

DEFAULT_REPORTS_DIR = os.path.expanduser("~/Documents/arxiv-reports")
USER_AGENT = "omarchy-arxiv-widget/1.0 (bar widget; contact: local user)"
DOWNLOAD_GAP = 3.0
# Text that is worth handing to the model. Figures and fonts are not.
SOURCE_SUFFIXES = (".tex", ".bbl", ".bib", ".sty", ".cls", ".txt")
MAX_SOURCE_BYTES = 4 * 1024 * 1024
CLAUDE_TIMEOUT = 1800


def log(message):
    """Nothing watches this script's stdout - the shell launches it detached -
    so the trail of a run has to live in a file."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a") as handle:
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


def save(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


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


def header(paper, pages, kind):
    authors = ", ".join(paper.get("authors") or []) or "unknown"
    bits = [
        "# " + str(paper.get("title") or paper["id"]),
        "",
        authors + "  ",
        "arXiv:" + paper["id"] + "  ·  " + str(paper.get("primary") or "") +
        "  ·  submitted " + str(paper.get("published") or "")[:10] +
        ("  ·  " + str(pages) + " pages" if pages else "") + "  ",
        str(paper.get("absUrl") or ""),
        "",
        "*Report generated " + datetime.now().strftime("%Y-%m-%d %H:%M") +
        (" from the LaTeX source." if kind == "tex" else " from the PDF.") + "*",
        "",
        "---",
        "",
    ]
    return "\n".join(bits)


SOURCE_NOTES = {
    "tex": ("This is the LaTeX source. `\\input`/`\\include` files and the macro "
            "definitions sit in the same directory, and the bibliography may be in "
            "a .bbl file - read whatever you need to answer accurately."),
    "pdf": ("This is a PDF, so work through it in page ranges."),
}


def summarise(kind, entry_name, workdir):
    prompt = (open(PROMPT).read()
              .replace("{SOURCE}", entry_name)
              .replace("{SOURCE_NOTE}", SOURCE_NOTES[kind])
              .replace("{PDF}", entry_name))
    log("running claude in " + workdir + " on " + entry_name + " (" + kind + ")")
    # The prompt goes on stdin: --allowedTools is variadic and swallows any
    # positional argument that follows it, prompt included.
    result = subprocess.run(
        ["claude", "-p", "--allowedTools", "Read"],
        input=prompt, cwd=workdir, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    log("claude exit=" + str(result.returncode) +
        " stdout=" + str(len(result.stdout)) + "b stderr=" + repr(result.stderr[:400]))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "claude failed").strip()[:200])
    body = result.stdout.strip()
    if len(body) < 400:
        raise RuntimeError("claude returned too little to be a report")
    return body


def process(paper, reports_dir, workdir):
    paper_id = paper["id"]
    kind, srcdir, entry_name = fetch_source(paper, workdir)
    pages = page_count(os.path.join(srcdir, entry_name)) if kind == "pdf" else 0
    body = summarise(kind, entry_name, srcdir)

    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, safe_id(paper_id) + "-" + slug(paper.get("title")) + ".md")
    with open(report_path, "w") as handle:
        handle.write(header(paper, pages, kind) + body + "\n")
    shutil.rmtree(srcdir, ignore_errors=True)
    return report_path


def main(argv):
    reports_dir = os.path.expanduser(argv[1]) if len(argv) > 1 else DEFAULT_REPORTS_DIR
    os.makedirs(STATE_DIR, exist_ok=True)

    # One run at a time: a second keypress while the first run is working
    # should do nothing rather than fetch and summarise everything twice.
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        notify("A summary run is already going.")
        return 0

    if shutil.which("claude") is None:
        notify("The claude CLI is not on PATH, so no report can be written.", "critical")
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

    log("run start: " + str(len(pending)) + " pending, reports -> " + reports_dir)
    notify("Summarising " + str(len(pending)) + " preprint" + ("s" if len(pending) > 1 else "") + "…")

    # Start from a clean scratch dir: a run killed mid-paper leaves sources
    # behind, and the lock means nothing else is using it.
    workdir = os.path.join(STATE_DIR, "work")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    done, failed = 0, []
    for index, paper in enumerate(pending):
        if index > 0:
            time.sleep(DOWNLOAD_GAP)
        try:
            log("processing " + paper["id"])
            path = process(paper, reports_dir, workdir)
        except Exception as error:  # one bad paper must not sink the whole run
            failed.append((paper["id"], str(error)[:120]))
            log("FAILED " + paper["id"] + ": " + repr(error)[:400])
            log(traceback.format_exc()[-800:])
            print("FAILED " + paper["id"] + ": " + str(error)[:200], file=sys.stderr)
            continue

        reports[paper["id"]] = {
            "path": path,
            "title": paper.get("title", ""),
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
