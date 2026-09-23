"""Look up a paper on INSPIRE by CADI number, arXiv ID or DOI, and turn the record into form fields.

If INSPIRE can't be reached (no outbound access from the cluster, or INSPIRE is down), callers get
InspireUnavailable and the form is simply filled in by hand.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

CADI = re.compile(r"^(?:CMS-(?:PAS-)?)?([A-Z0-9]{3}-\d{2}-\d{3})$", re.I)
ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
ARXIV_OLD = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$", re.I)
DOI = re.compile(r"^10\.\d{4,9}/\S+$")
CMS_REPORT = re.compile(r"^CMS-(?:PAS-)?([A-Z0-9]{3}-\d{2}-\d{3})$")


class InspireUnavailable(Exception):
    """INSPIRE could not be reached or answered with an error."""


class NotRecognized(ValueError):
    """The text isn't a CADI number, arXiv ID or DOI."""


def classify(text):
    """Return (kind, normalized value) for what the person typed or pasted."""
    t = (text or "").strip()
    t = re.sub(r"^https?://(dx\.)?doi\.org/", "", t)
    t = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", t).removesuffix(".pdf")
    t = re.sub(r"^(arxiv|doi):\s*", "", t, flags=re.I)
    if m := CADI.match(t):
        return "cadi", m.group(1).upper()
    if m := ARXIV_NEW.match(t) or ARXIV_OLD.match(t):
        return "arxiv", m.group(1)
    if DOI.match(t):
        return "doi", t
    raise NotRecognized("Enter a CADI number (EXO-23-002), an arXiv ID (2403.05311) or a DOI (10.1103/...).")


def _query(kind, value):
    if kind == "cadi":
        return f'r "CMS-{value}" or r "CMS-PAS-{value}"'
    if kind == "inspire_recid":
        return f"control_number:{value}"
    return f'{kind}:"{value}"'


def _request(params, accept):
    base = getattr(settings, "INSPIRE_URL", "https://inspirehep.net/api/literature")
    url = f"{base}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "FNAL-group-publication-tracker"})
    try:
        with urllib.request.urlopen(req, timeout=getattr(settings, "INSPIRE_TIMEOUT", 8)) as r:
            return r.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise InspireUnavailable(str(e)) from e


def _get(query):
    body = _request({"q": query, "size": 5, "sort": "mostrecent"}, "application/json")
    try:
        return json.loads(body)
    except ValueError as e:
        raise InspireUnavailable(str(e)) from e


def bibtex(recids):
    """INSPIRE's BibTeX for these record numbers, as one text; records INSPIRE doesn't have are left out."""
    query = " or ".join(f"control_number:{r}" for r in recids)
    body = _request({"q": query, "size": len(recids), "format": "bibtex"}, "application/x-bibtex")
    return body.decode("utf-8", errors="replace")


def _journal_ref(pub):
    if not pub.get("journal_title"):
        return ""
    ref = pub["journal_title"]
    if pub.get("journal_volume"):
        ref += f" {pub['journal_volume']}"
    if pub.get("year"):
        ref += f" ({pub['year']})"
    page = pub.get("artid") or pub.get("page_start")
    return f"{ref} {page}" if page else ref


def _doc_type(meta):
    kinds = " ".join(meta.get("document_type") or []).lower()
    if any("CMS" in (c.get("value") or "") for c in meta.get("collaborations") or []):
        return "cms_collab"
    if "conference" in kinds or "proceedings" in kinds:
        return "proceedings"
    if "book" in kinds:
        return "book"
    if "report" in kinds or "note" in kinds:
        return "report"
    return "journal_article"


def to_fields(meta):
    """Map one INSPIRE literature record onto Work fields (only the ones INSPIRE knows)."""
    fields = {}
    titles = meta.get("titles") or []
    if titles and titles[0].get("title"):
        fields["title"] = re.sub(r"\s+", " ", titles[0]["title"]).strip()
    eprints = meta.get("arxiv_eprints") or []
    if eprints:
        fields["arxiv"] = eprints[0].get("value", "")
    dois = meta.get("dois") or []
    if dois:
        fields["doi"] = dois[0].get("value", "")
    reports = [r.get("value", "") for r in meta.get("report_numbers") or []]
    cms = [r for r in reports if CMS_REPORT.match(r)]
    if cms:
        fields["cadi"] = CMS_REPORT.match(cms[0]).group(1)
    pas = [r for r in reports if r.startswith("CMS-PAS-")]
    if pas:
        fields["report_number"] = pas[0]
    pubs = [p for p in meta.get("publication_info") or [] if p.get("journal_title")]
    if pubs:
        fields["journal_ref"] = _journal_ref(pubs[0])
        fields["venue"] = pubs[0]["journal_title"]
    if meta.get("control_number"):
        fields["inspire_recid"] = str(meta["control_number"])
    fields["doc_type"] = _doc_type(meta)
    if pubs and pubs[0].get("journal_volume"):
        fields["status"] = "published"
    elif eprints:
        fields["status"] = "preprint"
    return fields


def identifiers(meta):
    """Every CADI number, arXiv ID, DOI and record number on an INSPIRE record, normalized."""
    reports = ((r.get("value") or "").upper() for r in meta.get("report_numbers") or [])
    return {
        "cadi": {m.group(1) for r in reports if (m := CMS_REPORT.match(r))},
        "arxiv": {v.lower() for e in meta.get("arxiv_eprints") or [] if (v := e.get("value"))},
        "doi": {v.lower() for d in meta.get("dois") or [] if (v := d.get("value"))},
        "inspire_recid": {str(meta["control_number"])} if meta.get("control_number") else set(),
    }


def has_identifier(meta, kind, value):
    return (value.upper() if kind == "cadi" else value.lower()) in identifiers(meta)[kind]


def find(kind, value):
    """The best INSPIRE record carrying this identifier, or None."""
    data = _get(_query(kind, value))
    hits = [h.get("metadata") or {} for h in (data.get("hits") or {}).get("hits") or []]
    # a search can match more loosely than we want; keep only records that really carry the identifier
    hits = [m for m in hits if has_identifier(m, kind, value)]
    if not hits:
        return None
    # for a CADI number, prefer the journal paper over the PAS or conference records
    hits.sort(key=lambda m: (not m.get("publication_info"), not m.get("arxiv_eprints")))
    return hits[0]


def lookup(text):
    """Return (kind, value, fields or None). fields is None when INSPIRE has no record."""
    kind, value = classify(text)
    meta = find(kind, value)
    if meta is None:
        return kind, value, None
    fields = to_fields(meta)
    if kind == "cadi":
        fields["cadi"] = value
    return kind, value, fields


def check():
    """Used by `manage.py check_inspire`: raises InspireUnavailable if INSPIRE can't be reached."""
    _get('arxiv:"2403.05311"')
