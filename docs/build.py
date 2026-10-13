#!/usr/bin/env python3
"""Build the documentation site in docs/ from the page bodies in docs/src/.

Usage: python3 docs/build.py

Each src/<name>.html is the body of one page; this wraps it in the shared
head, navigation and footer and writes docs/<name>.html. GitHub Pages serves
docs/ as is (.nojekyll keeps Jekyll out of the way). Standard library only.
"""

import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "https://github.com/pierrecagne/omarchy-arxiv"

# (file, navigation label, page title, description)
PAGES = [
    ("index", "Overview", "arXiv Reader for Omarchy",
     "The newest arXiv preprints in the Omarchy bar, and AI reading reports written for your expertise."),
    ("install", "Install", "Install",
     "Requirements, installing the plugin and setting up an AI engine."),
    ("usage", "Using it", "Using it",
     "Browsing, starring, summarising and reading reports; keys and scripting."),
    ("configuration", "Configuration", "Configuration",
     "Every setting, who the reports are for, report styles and custom prompts."),
    ("engines", "AI engines & sandbox", "AI engines and the sandbox",
     "Claude Code, Codex and custom commands, and what the sandbox lets them read."),
    ("security", "Privacy & security", "Privacy and security",
     "What the plugin sends where, which files it writes, and how it handles untrusted papers."),
    ("troubleshooting", "Troubleshooting", "Troubleshooting",
     "Common problems and where to look."),
]

LAYOUT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="site">
  <div class="site-inner">
    <div class="brand"><a href="index.html">arXiv Reader</a><span>an Omarchy plugin</span></div>
    <nav class="site" aria-label="Documentation">
{nav}
      <a href="{repo}">GitHub</a>
    </nav>
  </div>
</header>
<main>
{body}
{pager}
</main>
<footer class="site">
  <div class="site-inner">
    <p>This plugin was vibe coded with Claude Opus 5.5. Read and check the code
    yourself before using it: like every Omarchy plugin it runs unsandboxed
    inside <code>omarchy-shell</code> with your permissions.</p>
    <p>Thank you to arXiv for use of its open access interoperability. This
    project is not affiliated with or endorsed by arXiv, Anthropic or OpenAI.</p>
    <p><a href="{repo}">Source on GitHub</a> · MIT License · by Pierre Cagne</p>
  </div>
</footer>
</body>
</html>
"""


def nav(current):
    links = []
    for name, label, _, _ in PAGES:
        attr = ' aria-current="page"' if name == current else ""
        links.append('      <a href="%s.html"%s>%s</a>' % (name, attr, html.escape(label)))
    return "\n".join(links)


def pager(index):
    prev_link = next_link = "<span></span>"
    if index > 0:
        name, label = PAGES[index - 1][:2]
        prev_link = '<a href="%s.html">&larr; %s</a>' % (name, html.escape(label))
    if index < len(PAGES) - 1:
        name, label = PAGES[index + 1][:2]
        next_link = '<a href="%s.html">%s &rarr;</a>' % (name, html.escape(label))
    return '<div class="pager">%s%s</div>' % (prev_link, next_link)


def main():
    for index, (name, _, title, description) in enumerate(PAGES):
        with open(os.path.join(HERE, "src", name + ".html")) as handle:
            body = handle.read().strip()
        page_title = title if name == "index" else title + " · arXiv Reader for Omarchy"
        out = LAYOUT.format(title=html.escape(page_title), description=html.escape(description),
                            nav=nav(name), repo=REPO, body=body, pager=pager(index))
        with open(os.path.join(HERE, name + ".html"), "w") as handle:
            handle.write(out)
        print("wrote docs/%s.html" % name)


if __name__ == "__main__":
    main()
