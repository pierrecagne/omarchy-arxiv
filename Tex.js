// arXiv serves titles and abstracts as raw TeX. There is no LaTeX engine in a
// bar panel, so this turns the subset that actually shows up in abstracts --
// font macros, named symbols, sub/superscripts -- into Unicode plus the small
// markup vocabulary Qt's StyledText understands (<i> <b> <sub> <sup>).
//
// The rule everywhere below: only rewrite what is unambiguous. A macro this
// does not know is left standing as written, so a reader sees `\wreath` and
// knows it is TeX rather than being shown something subtly wrong.

var SYMBOLS = {
  "in": "∈", "notin": "∉", "ni": "∋", "leq": "≤", "le": "≤",
  "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "equiv": "≡",
  "cong": "≅", "simeq": "≃", "sim": "∼", "approx": "≈",
  "propto": "∝", "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
  "supseteq": "⊇", "sqsubseteq": "⊑", "models": "⊨", "vdash": "⊢",
  "dashv": "⊣", "perp": "⊥", "mid": "∣", "parallel": "∥",
  "ll": "≪", "gg": "≫", "asymp": "≍",
  "times": "×", "otimes": "⊗", "oplus": "⊕", "odot": "⊙",
  "boxtimes": "⊠", "cap": "∩", "cup": "∪", "sqcup": "⊔",
  "sqcap": "⊓", "wedge": "∧", "vee": "∨", "setminus": "∖",
  "circ": "∘", "bullet": "•", "cdot": "⋅", "ast": "∗",
  "star": "⋆", "pm": "±", "mp": "∓", "div": "÷", "amalg": "⨿",
  "bigoplus": "⨁", "bigotimes": "⨂", "bigcup": "⋃", "bigcap": "⋂",
  "coprod": "∐", "prod": "∏", "sum": "∑", "int": "∫", "oint": "∮",
  "to": "→", "rightarrow": "→", "longrightarrow": "⟶", "leftarrow": "←",
  "longleftarrow": "⟵", "leftrightarrow": "↔", "mapsto": "↦",
  "longmapsto": "⟼", "hookrightarrow": "↪", "hookleftarrow": "↩",
  "twoheadrightarrow": "↠", "Rightarrow": "⇒", "Longrightarrow": "⟹",
  "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⟺", "implies": "⟹",
  "uparrow": "↑", "downarrow": "↓", "rightsquigarrow": "⇝",
  "forall": "∀", "exists": "∃", "nexists": "∄", "neg": "¬",
  "lnot": "¬", "emptyset": "∅", "varnothing": "∅", "top": "⊤",
  "bot": "⊥", "Box": "□", "square": "□", "Diamond": "◇",
  "diamond": "⋄",
  "infty": "∞", "partial": "∂", "nabla": "∇", "hbar": "ℏ",
  "ell": "ℓ", "aleph": "ℵ", "beth": "ℶ", "wp": "℘",
  "Re": "ℜ", "Im": "ℑ", "prime": "′", "dagger": "†",
  "ddagger": "‡", "angle": "∠", "cdots": "⋯", "ldots": "…",
  "dots": "…", "vdots": "⋮", "ddots": "⋱",
  "langle": "⟨", "rangle": "⟩", "lvert": "|", "rvert": "|", "vert": "|",
  "lVert": "‖", "rVert": "‖", "Vert": "‖", "backslash": "\\",
  "colon": ":", "quad": " ", "qquad": "  ", "textrm": "", "displaystyle": "",
  "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
  "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
  "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
  "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
  "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
  "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ",
  "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
  "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
  "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
  "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
  "rtimes": "⋊",
  "ltimes": "⋉",
  "clubsuit": "♣",
  "lozenge": "◊",
  "bigsqcup": "⨆",
  "nmid": "∤",
  "ncong": "≇",
  "nsubseteq": "⊈",
  "notni": "∌",
  "curvearrowright": "↷",
  "upharpoonright": "↾",
  "triangleq": "≜",
  "coloneqq": "≔",
  "leadsto": "⇝"
}

// Unicode files the "mathematical alphanumeric" letters in one block each,
// except for the ones standardised earlier, which live in Letterlike Symbols.
// Those holes must be filled by hand or the block arithmetic lands on
// unassigned code points.
var HOLES = {
  script: { B: "ℬ", E: "ℰ", F: "ℱ", H: "ℋ", I: "ℐ",
            L: "ℒ", M: "ℳ", R: "ℛ", e: "ℯ", g: "ℊ", o: "ℴ" },
  fraktur: { C: "ℭ", H: "ℌ", I: "ℑ", R: "ℜ", Z: "ℨ" },
  bb: { C: "ℂ", H: "ℍ", N: "ℕ", P: "ℙ", Q: "ℚ",
        R: "ℝ", Z: "ℤ" },
  sans: {}
}
var BASES = { script: 0x1D49C, fraktur: 0x1D504, bb: 0x1D538, sans: 0x1D5A0 }
var MACRON = "̄"

function alphabet(text, style) {
  var holes = HOLES[style]
  var base = BASES[style]
  var out = ""
  for (var i = 0; i < text.length; i++) {
    var ch = text.charAt(i)
    if (holes[ch] !== undefined) { out += holes[ch]; continue }
    var offset = -1
    if (ch >= "A" && ch <= "Z") offset = ch.charCodeAt(0) - 65
    else if (ch >= "a" && ch <= "z") offset = ch.charCodeAt(0) - 97 + 26
    // Digits have no script or fraktur form; leave them alone.
    out += offset < 0 ? ch : String.fromCodePoint(base + offset)
  }
  return out
}

function escapeHtml(text) {
  return String(text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

// Read the {...} (or single token) that follows a macro, honouring nesting.
function readGroup(text, start) {
  if (text.charAt(start) !== "{") {
    var single = text.charAt(start)
    return single === "" ? null : { body: single, end: start + 1 }
  }
  var depth = 0
  for (var i = start; i < text.length; i++) {
    var ch = text.charAt(i)
    if (ch === "{") depth++
    else if (ch === "}") {
      depth--
      if (depth === 0) return { body: text.substring(start + 1, i), end: i + 1 }
    }
  }
  return null
}

var STYLED = {
  mathcal: "script", mathscr: "script", mathfrak: "fraktur",
  mathbb: "bb", Bbb: "bb", mathsf: "sans"
}
// Upright in this renderer, so these only need their braces dropped.
var PLAIN = ["mathrm", "operatorname", "mathop", "mathnormal", "rm", "texttt", "mathtt"]
// Prose inside a formula: upright, and its spaces are real.
var TEXTUAL = ["text", "textup", "mbox"]
var BOLD = ["mathbf", "boldsymbol", "bm", "textbf"]
var ITALIC = ["emph", "textit", "mathit"]
// \log and friends: upright operator names, printed as spelled.
var OPERATORS = ["log", "exp", "ln", "max", "min", "lim", "colim", "sup", "inf",
                 "liminf", "limsup", "ker", "coker", "im", "dim", "deg", "det",
                 "gcd", "lcm", "hom", "arg", "sin", "cos", "tan", "mod", "Pr"]
// Accents, as combining marks after the character they decorate.
var ACCENTS = {
  overline: "̄", bar: "̄", widetilde: "̃", tilde: "̃",
  widehat: "̂", hat: "̂", check: "̌", dot: "̇", vec: "⃗"
}
// Spacing macros: whatever they tune, a proportional label does not need it.
var DROP = ["!", ",", ";", ":", " ", "/", "left", "right", "big", "Big", "bigg", "Bigg",
            "bigl", "bigr", "Bigl", "Bigr", "limits", "nolimits", "protect"]

// `upright` suppresses the variable-italics rule: inside \mathrm or
// \operatorname a lone letter is part of a name, not a variable.
function convertMath(text, upright, keepSpaces) {
  var out = ""
  var i = 0

  while (i < text.length) {
    var ch = text.charAt(i)

    if (ch === "\\") {
      var match = /^\\([a-zA-Z]+|.)/.exec(text.substring(i))
      if (!match) { out += ch; i++; continue }
      var name = match[1]
      var after = i + match[0].length

      if (name === "mkern" || name === "kern" || name === "hspace") {
        var skip = /^\s*(\{[^{}]*\}|[-\d.]+\s*[a-z]*)/.exec(text.substring(after))
        i = after + (skip ? skip[0].length : 0)
        continue
      }
      if (DROP.indexOf(name) !== -1) { i = after; continue }

      if (STYLED[name]) {
        var group = readGroup(text, after)
        if (group) { out += alphabet(group.body.replace(/[{}]/g, ""), STYLED[name]); i = group.end; continue }
      }
      if (PLAIN.indexOf(name) !== -1) {
        var plain = readGroup(text, after)
        if (plain) { out += convertMath(plain.body, true, keepSpaces); i = plain.end; continue }
      }
      if (TEXTUAL.indexOf(name) !== -1) {
        var prose = readGroup(text, after)
        if (prose) { out += convertMath(prose.body, true, true); i = prose.end; continue }
      }
      if (BOLD.indexOf(name) !== -1) {
        var bold = readGroup(text, after)
        if (bold) { out += "<b>" + convertMath(bold.body, upright, keepSpaces) + "</b>"; i = bold.end; continue }
      }
      if (ITALIC.indexOf(name) !== -1) {
        var italic = readGroup(text, after)
        if (italic) { out += "<i>" + convertMath(italic.body, true, keepSpaces) + "</i>"; i = italic.end; continue }
      }
      if (ACCENTS[name] !== undefined) {
        var accented = readGroup(text, after)
        // The combining mark goes after each character it decorates, which is
        // right for the short spans (a letter, sometimes two) abstracts accent.
        if (accented) {
          var mark = ACCENTS[name]
          out += convertMath(accented.body, upright, keepSpaces).replace(/(<[^>]*>)|(\S)/g, function(all, tag, ch) {
            return tag ? tag : ch + mark
          })
          i = accented.end
          continue
        }
      }
      if (OPERATORS.indexOf(name) !== -1) { out += name; i = after; continue }
      if (name === "not") {
        // Negate whatever follows by overlaying a long solidus on it.
        var negated = /^\\[a-zA-Z]+|^./.exec(text.substring(after))
        if (negated) {
          out += convertMath(negated[0], upright, keepSpaces) + "̸"
          i = after + negated[0].length
          continue
        }
      }
      if (name === "sqrt") {
        var radicand = readGroup(text, after)
        if (radicand) { out += "√(" + convertMath(radicand.body, upright, keepSpaces) + ")"; i = radicand.end; continue }
      }
      if (name === "frac" || name === "dfrac" || name === "tfrac") {
        var num = readGroup(text, after)
        var den = num ? readGroup(text, num.end) : null
        if (num && den) {
          out += "(" + convertMath(num.body, upright, keepSpaces) + ")/(" + convertMath(den.body, upright, keepSpaces) + ")"
          i = den.end
          continue
        }
      }
      if (name === "xrightarrow" || name === "xleftarrow") {
        var label = readGroup(text, after)
        var arrow = name === "xrightarrow" ? "→" : "←"
        if (label) { out += arrow + "<sup>" + convertMath(label.body, upright, keepSpaces) + "</sup>"; i = label.end; continue }
      }
      if (SYMBOLS[name] !== undefined) { out += SYMBOLS[name]; i = after; continue }

      // Unknown macro: show it as written rather than guess at it.
      out += "\\" + name
      i = after
      continue
    }

    if (ch === "_" || ch === "^") {
      var script = readGroup(text, i + 1)
      if (script) {
        var tag = ch === "_" ? "sub" : "sup"
        out += "<" + tag + ">" + convertMath(script.body, upright, keepSpaces) + "</" + tag + ">"
        i = script.end
        continue
      }
    }

    // TeX sets a lone Latin letter in math as an italic variable; a run of
    // them is an operator name and stays upright. Deciding it here, one run at
    // a time, keeps the markup this function emits out of the question.
    if (/[A-Za-z]/.test(ch)) {
      var run = /^[A-Za-z]+/.exec(text.substring(i))[0]
      out += (run.length === 1 && !upright) ? "<i>" + run + "</i>" : run
      i += run.length
      continue
    }

    if (ch === "{" || ch === "}") { i++; continue }
    if (ch === "~") { out += " "; i++; continue }
    // TeX ignores spaces in math mode: `\langle B` sets as a tight bracket.
    if (!keepSpaces && /\s/.test(ch)) { i++; continue }

    out += ch
    i++
  }

  return out
}

// Text mode: only the handful of macros that appear outside $...$, then hand
// every math span to the converter.
function render(raw) {
  var text = escapeHtml(raw)
  if (text === "") return ""

  var out = ""
  var i = 0
  while (i < text.length) {
    var two = text.substr(i, 2)

    if (two === "$$" || two === "\\[") {
      var closer = two === "$$" ? "$$" : "\\]"
      var end = text.indexOf(closer, i + 2)
      if (end === -1) { out += text.substring(i); break }
      out += convertMath(text.substring(i + 2, end), false, false)
      i = end + closer.length
      continue
    }
    if (two === "\\(") {
      var closeParen = text.indexOf("\\)", i + 2)
      if (closeParen === -1) { out += text.substring(i); break }
      out += convertMath(text.substring(i + 2, closeParen), false, false)
      i = closeParen + 2
      continue
    }
    if (text.charAt(i) === "$") {
      var closeDollar = text.indexOf("$", i + 1)
      if (closeDollar === -1) { out += text.substring(i); break }
      out += convertMath(text.substring(i + 1, closeDollar), false, false)
      i = closeDollar + 1
      continue
    }
    if (text.charAt(i) === "\\") {
      var macro = /^\\([a-zA-Z]+)/.exec(text.substring(i))
      if (macro) {
        var name = macro[1]
        var after = i + macro[0].length
        if (ITALIC.indexOf(name) !== -1) {
          var italic = readGroup(text, after)
          if (italic) { out += "<i>" + render(italic.body) + "</i>"; i = italic.end; continue }
        }
        if (BOLD.indexOf(name) !== -1) {
          var bold = readGroup(text, after)
          if (bold) { out += "<b>" + render(bold.body) + "</b>"; i = bold.end; continue }
        }
        if (PLAIN.indexOf(name) !== -1) {
          var plain = readGroup(text, after)
          if (plain) { out += render(plain.body); i = plain.end; continue }
        }
        if (SYMBOLS[name] !== undefined) { out += SYMBOLS[name]; i = after; continue }
      }
    }

    out += text.charAt(i)
    i++
  }
  return out
}

if (typeof module !== "undefined") {
  module.exports = { render: render, convertMath: convertMath, alphabet: alphabet }
}
