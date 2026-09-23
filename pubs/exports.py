"""Downloads of the publication list: CSV, plain text, LaTeX and BibTeX.

Each writer takes the works in list order and an ExportInfo, and returns the file's text.
"""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from . import inspire
from .templatetags.pubs_extras import role_list


@dataclass
class ExportInfo:
    site_title: str
    source_url: str          # the list page with the same filters
    link: callable           # work -> absolute URL of its page
    person: object = None    # set when the list is filtered by person


# ---- shared pieces ------------------------------------------------------------------------------

def pub_year(w):
    if m := re.search(r"\b((?:19|20)\d{2})\b", w.journal_ref):
        return m.group(1)
    if m := re.match(r"(\d{2})\d{2}\.\d{4,5}$", w.arxiv):
        return f"20{m.group(1)}"
    return str(w.last_update.year) if w.last_update else ""


def authors(w):
    if w.doc_type == "cms_collab":
        return "CMS Collaboration"
    names = [c.person.name for c in w.contributions.all()]
    if names and w.has_other_authors:
        names.append("et al.")
    return ", ".join(names)


def contributors(w, person=None):
    """Who in the group did what: that person's roles when the list is filtered by person; otherwise every
    group contributor for collaboration papers, or just the roles where the author list already names them."""
    if person:
        c = next((c for c in w.contributions.all() if c.person_id == person.pk), None)
        roles = role_list(c) if c else ""
        return f"{person.name}: {roles}" if roles else ""
    named_as_authors = w.doc_type != "cms_collab"
    people = [c.person.name + (f" ({r})" if (r := role_list(c)) else "") for c in w.contributions.all()
              if not named_as_authors or role_list(c)]
    if not people:
        return ""
    return ("Roles: " if named_as_authors else "Group contributors: ") + "; ".join(people)


def _sentence(parts):
    return ", ".join(p for p in parts if p).rstrip(".") + "."


def status_note(w):
    if w.status == "published":
        return ""
    if w.status in ("submitted", "accepted") and w.venue and not w.journal_ref:
        return f"{'Submitted to' if w.status == 'submitted' else 'Accepted by'} {w.venue}"
    label = w.get_status_display()
    return f"{label} (CMS internal)" if w.is_internal_stage else label


def _entries(n):
    return f"{n} entry" if n == 1 else f"{n} entries"


def _header_lines(works, info, what):
    lines = [f"{info.site_title}: {_entries(len(works))} as {what}, exported {date.today():%-d %b %Y}",
             f"From {info.source_url}"]
    internal = sum(w.visibility == "internal" for w in works)
    if internal:
        lines.append(f"Includes {_entries(internal)} still in CMS internal review. Don't share this outside the group.")
    return lines


# ---- CSV ----------------------------------------------------------------------------------------

CSV_COLUMNS = ["Entry", "Title", "Status", "Visibility", "Category", "Type", "CADI", "arXiv", "DOI",
               "Report number", "CDS record", "INSPIRE record", "Journal reference", "Venue", "Last update",
               "Group contributors", "Other authors", "Needs review", "Link"]


def _cell(value):
    # a leading = + - @ makes spreadsheet programs run the cell as a formula
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(works, info):
    out = io.StringIO()
    out.write("﻿")  # lets Excel recognise UTF-8, so accented names survive
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for w in works:
        people = "; ".join(c.person.name + (f" ({r})" if (r := role_list(c)) else "") for c in w.contributions.all())
        writer.writerow([_cell(v) for v in [
            w.code, w.title, w.get_status_display(), w.get_visibility_display(), w.get_category_display(),
            w.get_doc_type_display(), w.cadi, w.arxiv, w.doi, w.report_number, w.cds, w.inspire_recid,
            w.journal_ref, w.venue, w.last_update_display, people, "yes" if w.has_other_authors else "",
            "; ".join(text for _, text in w.flag_help), info.link(w)]])
    return out.getvalue()


# ---- plain text ---------------------------------------------------------------------------------

def to_text(works, info):
    lines = _header_lines(works, info, "plain text") + [""]
    for n, w in enumerate(works, 1):
        parts = [authors(w), f"“{w.title}”", w.journal_ref or w.report_number,
                 w.arxiv and f"arXiv:{w.arxiv}", w.doi and f"doi:{w.doi}",
                 w.url if not (w.arxiv or w.doi) else "", status_note(w)]
        lines.append(f"{n}. " + _sentence(parts))
        if line := contributors(w, info.person):
            lines.append(f"   {line}")
    return "\n".join(lines) + "\n"


# ---- LaTeX --------------------------------------------------------------------------------------

TEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
               "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
TEX_MATH = {"→": r"\to", "←": r"\leftarrow", "↔": r"\leftrightarrow", "±": r"\pm", "∓": r"\mp",
            "×": r"\times", "≈": r"\approx", "≤": r"\leq", "≥": r"\geq", "∼": r"\sim", "√": r"\sqrt{}",
            "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "η": r"\eta",
            "θ": r"\theta", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "π": r"\pi",
            "ρ": r"\rho", "σ": r"\sigma", "τ": r"\tau", "φ": r"\phi", "χ": r"\chi", "ψ": r"\psi",
            "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta", "Λ": r"\Lambda", "Σ": r"\Sigma", "Υ": r"\Upsilon",
            "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega"}
SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
TEX_TOKEN = re.compile(r"√sNN|√s|(.)̄|[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+|.", re.S)


def tex(text):
    """Plain Unicode text as LaTeX: special characters escaped, physics symbols as math."""
    out = []
    for m in TEX_TOKEN.finditer(text or ""):
        tok = m.group()
        if tok == "√sNN":
            out.append(r"$\sqrt{s_{NN}}$")
        elif tok == "√s":
            out.append(r"$\sqrt{s}$")
        elif m.group(1) is not None:
            out.append(rf"$\bar{{{tex(m.group(1))}}}$")
        elif tok[0] in "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻":
            out.append(f"$^{{{tok.translate(SUPERSCRIPT)}}}$")
        elif tok in TEX_SPECIAL:
            out.append(TEX_SPECIAL[tok])
        elif tok in TEX_MATH:
            out.append(f"${TEX_MATH[tok]}$")
        else:
            out.append(tok)
    return re.sub(r"(?<!\\)\$\$", "", "".join(out))  # join neighbouring math, e.g. $\gamma\gamma$


def _href(url, text):
    url = url.replace("%", r"\%").replace("#", r"\#")
    return r"\href{" + url + "}{" + text + "}"


def to_latex(works, info):
    lines = ["% " + line for line in _header_lines(works, info, "LaTeX")]
    lines += [r"% The links need \usepackage{hyperref}.", r"\begin{enumerate}"]
    for w in works:
        pub = tex(w.journal_ref or w.report_number)
        if pub and w.doi and w.journal_ref:
            pub = _href(f"https://doi.org/{w.doi}", pub)
        parts = [tex(authors(w)), f"``{tex(w.title)},''", pub,
                 w.arxiv and _href(f"https://arxiv.org/abs/{w.arxiv}", f"arXiv:{tex(w.arxiv)}"),
                 w.doi and not w.journal_ref and _href(f"https://doi.org/{w.doi}", f"doi:{tex(w.doi)}"),
                 w.url and not (w.arxiv or w.doi) and _href(w.url, tex(w.url)), tex(status_note(w))]
        item = r"  \item " + _sentence(parts).replace(",'', ", ",'' ")
        if line := contributors(w, info.person):
            item += rf" \\ {{\small {tex(line)}}}"
        lines.append(item)
    lines.append(r"\end{enumerate}")
    return "\n".join(lines) + "\n"


# ---- BibTeX -------------------------------------------------------------------------------------

BATCH = 50
BIB_ENTRY = re.compile(r"^@\w+\{.*?^\}", re.S | re.M)


def _inspire_entries(works):
    """INSPIRE's BibTeX for works with a record number, keyed by work pk; None if INSPIRE can't be reached."""
    with_recid = [w for w in works if w.inspire_recid]
    found = {}
    for i in range(0, len(with_recid), BATCH):
        batch = with_recid[i:i + BATCH]
        try:
            text = inspire.bibtex([w.inspire_recid for w in batch])
        except inspire.InspireUnavailable:
            return None
        entries = BIB_ENTRY.findall(text)
        for w in batch:
            # INSPIRE's BibTeX has no record number, so match on the arXiv ID or DOI it carries
            for e in entries:
                if any(value and re.search(rf'\b{field}\s*=\s*"{re.escape(value)}"', e, re.I)
                       for field, value in (("eprint", w.arxiv), ("doi", w.doi))):
                    found[w.pk] = e
                    break
    return found


def _bib_key(w):
    if w.doc_type == "cms_collab":
        prefix = "CMS"
    else:
        first = next(iter(w.contributions.all()), None)
        surname = first.person.name.split()[-1] if first else "FNAL"
        prefix = unicodedata.normalize("NFKD", surname).encode("ascii", "ignore").decode() or "FNAL"
    return f"{prefix}:{pub_year(w)}{w.code.lower()}"


def _local_bibtex(w):
    if w.doc_type == "cms_collab":
        author, extra = "{CMS Collaboration}", [("collaboration", "CMS")]
    else:
        names = [tex(c.person.name) for c in w.contributions.all()]
        author = " and ".join(names + (["others"] if w.has_other_authors and names else []))
        extra = []
    fields = [("author", author), *extra, ("title", "{" + tex(w.title) + "}"),
              ("eprint", w.arxiv), ("archivePrefix", "arXiv" if w.arxiv else ""), ("doi", w.doi),
              ("reportNumber", w.report_number), ("note", tex(w.journal_ref or status_note(w))),
              ("url", w.url if not (w.arxiv or w.doi) else ""), ("year", pub_year(w))]
    body = ",\n".join(f"    {k} = {{{v}}}" for k, v in fields if v)
    return f"@misc{{{_bib_key(w)},\n{body}\n}}"


def to_bibtex(works, info):
    from_inspire = _inspire_entries(works)
    lines = ["% " + line for line in _header_lines(works, info, "BibTeX")]
    if from_inspire is None:
        lines.append("% INSPIRE couldn't be reached, so every entry was written from the tracker's own data.")
        from_inspire = {}
    else:
        n, local = len(from_inspire), len(works) - len(from_inspire)
        lines.append(f"% {_entries(n)} {'comes' if n == 1 else 'come'} from INSPIRE; {_entries(local)} "
                     "written from the tracker's own data (marked below).")
    out = ["\n".join(lines)]
    for w in works:
        if w.pk in from_inspire:
            out.append(from_inspire[w.pk])
        else:
            out.append(f"% From the tracker ({w.code}), not INSPIRE\n{_local_bibtex(w)}")
    return "\n\n".join(out) + "\n"


FORMATS = {
    "csv": (to_csv, "text/csv"),
    "txt": (to_text, "text/plain"),
    "tex": (to_latex, "application/x-tex"),
    "bib": (to_bibtex, "application/x-bibtex"),
}
