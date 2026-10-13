Read the paper at `{SOURCE}` and write a reading report on it.

Your reader is a working mathematician in categorical logic, categorical
semantics and type theory (including homotopy type theory). Assume complete
command of category theory, logic and type theory: never explain a standard
notion. The report should let them decide whether to read the paper, and give
them the real content if they do not.

Read the whole paper, not just the abstract and introduction.
{SOURCE_NOTE}

Write Markdown, 900-1500 words, with exactly these sections:

## What it does
One paragraph: the question, and the answer.

## Main results
Each theorem stated precisely, with its hypotheses, numbered as the paper
numbers it. Give the actual statement rather than a gloss of it.

## Definitions worth knowing
Only notions the paper introduces, or uses in a non-standard way.

## How the proofs go
The strategy and the key technical devices — enough that the reader could
reconstruct the shape of the argument.

## Context
What was known before; which prior work this builds on, extends, or corrects.

## Assessment
What is genuinely new and what is routine. Limitations the authors state, and
any the paper leaves implicit. End with one line: whether it is worth reading
in full, and which sections.

Rules:

- Everything in the report must come from the paper. Where the paper is
  unclear, or you could not read part of it, say so in the report rather than
  filling the gap. Never present a plausible reconstruction as the paper's.
- Mathematical notation in LaTeX, inline `$...$`. When reading LaTeX source,
  **expand the paper's own macros** into standard notation: a reader of the
  report does not have its preamble, so `\id{A}(x,y)` must become
  `\mathrm{Id}_A(x,y)`, `\ssets` must become `\mathbf{sSet}`, and so on. Where a
  macro's meaning is not clear from its definition, name it in words instead.
- No preamble and no praise. Begin with the `## What it does` heading.
