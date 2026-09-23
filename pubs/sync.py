"""Nightly INSPIRE sync: fill in what an entry is missing and move its status forward.

It never overwrites a value someone entered and never moves a status backwards. When INSPIRE
contradicts an entry, or an identifier already belongs to another entry, it leaves a note instead.
"""
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.db.models import Q

from . import inspire
from .models import EditLog, Work, describe_changes

# finer than TRACK_STEP, so that a public PAS still moves on to preprint
STATUS_ORDER = ["unknown", "in_preparation", "pre_approval", "approved", "cwr", "final_reading",
                "pas_public", "preprint", "submitted", "accepted", "published"]
LOOKUP_ORDER = ["doi", "arxiv", "cadi", "inspire_recid"]
FILLABLE = ["cadi", "arxiv", "doi", "report_number", "inspire_recid", "journal_ref", "venue"]
UNIQUE_IDS = {"cadi": "CADI number", "arxiv": "arXiv ID", "doi": "DOI"}
PUBLIC_IDS = ["arxiv", "doi", "cds", "journal_ref"]
NOTE_FIELD = "INSPIRE check"


def candidates():
    """Entries INSPIRE could know about, minus published ones that already have everything."""
    has_id = Q(cadi__gt="") | Q(arxiv__gt="") | Q(doi__gt="") | Q(inspire_recid__gt="")
    done = Q(status="published", inspire_recid__gt="", journal_ref__gt="")
    return Work.objects.filter(has_id).exclude(done).order_by("code")


@dataclass
class Result:
    found: bool = False
    changes: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def find_record(work):
    for kind in LOOKUP_ORDER:
        value = getattr(work, kind)
        if value and (meta := inspire.find(kind, value)):
            return meta
    return None


def _contradictions(work, meta):
    ids, record = inspire.identifiers(meta), meta.get("control_number", "")
    return [f"INSPIRE record {record} has {label} {', '.join(sorted(ids[k]))}, but this entry has "
            f"{getattr(work, k)}. Nothing was filled in from it."
            for k, label in UNIQUE_IDS.items()
            if getattr(work, k) and ids[k] and not inspire.has_identifier(meta, k, getattr(work, k))]


def _fill(work, fields):
    notes = []
    is_paper = "status" in fields  # a preprint or journal paper, not a PAS-only record
    for k in FILLABLE:
        new = fields.get(k)
        if not new or getattr(work, k) or (k == "inspire_recid" and not is_paper):
            continue
        if k in UNIQUE_IDS:
            other = Work.objects.filter(**{f"{k}__iexact": new}).exclude(pk=work.pk).first()
            if other:
                notes.append(f"INSPIRE gives {UNIQUE_IDS[k]} {new}, which is already on {other.code} "
                             f"({other}). These may be the same paper.")
                continue
        setattr(work, k, new)
    new_status = fields.get("status")
    if new_status and STATUS_ORDER.index(new_status) > STATUS_ORDER.index(work.status):
        work.status = new_status
        work.last_update, work.last_update_precision = date.today(), "day"
    return notes


def sync_work(work, dry_run=False):
    """Check one entry against INSPIRE and save what's new, unless dry_run."""
    meta = find_record(work)
    if meta is None:
        return Result()
    before = work.snapshot()
    notes = _contradictions(work, meta) or _fill(work, inspire.to_fields(meta))
    noted = {c.get("new") for e in work.edit_log.filter(action="inspire") for c in e.changes
             if c.get("field") == NOTE_FIELD}
    result = Result(found=True, changes=describe_changes(before, work.snapshot()),
                    notes=[n for n in notes if n not in noted])

    if result.notes and "inspire_mismatch" not in work.review_flags:
        work.review_flags = [*work.review_flags, "inspire_mismatch"]
    settled = {"missing_identifier": any(getattr(work, k) and not before[k] for k in PUBLIC_IDS),
               "possibly_stale": work.status != before["status"]}
    resolved = [f for f in work.review_flags if settled.get(f)]
    if resolved:
        work.review_flags = [f for f in work.review_flags if f not in resolved]
        result.changes.append({"field": "Review notes", "old": f"{len(resolved)} resolved", "new": ""})

    if (result.changes or result.notes) and not dry_run:
        with transaction.atomic():
            work.save(change_source="inspire")
            EditLog.objects.create(work=work, user=None, action="inspire", changes=result.changes + [
                {"field": NOTE_FIELD, "old": "", "new": n} for n in result.notes])
    return result
