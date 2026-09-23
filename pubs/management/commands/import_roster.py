import calendar
import csv
import re
from collections import defaultdict
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from pubs.models import Membership, Person

YES, NO = {"yes", "y", "true", "1"}, {"no", "n", "false", "0"}
BAI = re.compile(r"^[A-Za-z][\w.'\-]*\.\d+$")          # e.g. K.Pedro.1
INSPIRE_NUM = re.compile(r"^INSPIRE-\d{8}$")          # e.g. INSPIRE-00123456
ORCID = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


def orcid_checksum_ok(orcid):
    digits = orcid.replace("-", "")
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")   # what Excel writes in the US: 8/1/2025


def parse_date(text, end=False):
    """Accepts 2019, 2019-09, 2019-09-01 or 9/1/2019 (US month/day/year).
    A bare year or month means its first day (start) or last day (end)."""
    us = US_DATE.match(text)
    if us:
        return date(int(us.group(3)), int(us.group(1)), int(us.group(2)))
    parts = [int(p) for p in text.split("-")]
    if len(parts) == 1:
        return date(parts[0], 12, 31) if end else date(parts[0], 1, 1)
    if len(parts) == 2:
        last = calendar.monthrange(parts[0], parts[1])[1]
        return date(parts[0], parts[1], last if end else 1)
    return date(*parts)


class Command(BaseCommand):
    help = ("Update people from the roster spreadsheet (CSV). Blank cells leave existing values alone. "
            "Repeat a person's row to record more than one membership period.")

    def add_arguments(self, parser):
        parser.add_argument("path", help="CSV file, or /dev/stdin")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving")

    def handle(self, path, dry_run, **opts):
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows or "name" not in rows[0]:
            raise CommandError("Expected the roster CSV with at least a 'name' column.")
        col = {k.split(" ")[0].strip().lower(): k for k in rows[0]}   # "in_group (yes/no)" -> in_group

        def cell(row, key):
            return (row.get(col.get(key, key)) or "").strip()

        errors, changes, periods = [], [], defaultdict(list)
        plan = {}
        for n, row in enumerate(rows, start=2):   # row 1 is the header
            pid, name = cell(row, "id"), cell(row, "name")
            if not pid and not name:
                continue
            person = Person.objects.filter(slug=pid).first() if pid else None
            if pid and not person:
                errors.append(f"Row {n}: no person with id '{pid}'. Leave id empty to add a new person.")
                continue
            key = pid or slugify(name)
            entry = plan.setdefault(key, {"person": person, "fields": {}, "row": n})
            fields = entry["fields"]
            if not person and name:
                fields["name"] = name
            elif person and name and name != person.name:
                fields["name"] = name

            g = cell(row, "in_group").lower()
            if g in YES: fields["in_group"] = True
            elif g in NO: fields["in_group"] = False
            elif g: errors.append(f"Row {n}: in_group should be yes or no, not '{g}'.")

            inspire = cell(row, "inspire_id")
            if inspire:
                if BAI.match(inspire) or INSPIRE_NUM.match(inspire):
                    fields["inspire_id"] = inspire
                else:
                    errors.append(f"Row {n}: '{inspire}' doesn't look like an INSPIRE ID (e.g. K.Pedro.1 or INSPIRE-00123456).")

            orcid = cell(row, "orcid").replace("https://orcid.org/", "").upper()
            if orcid:
                if ORCID.match(orcid) and orcid_checksum_ok(orcid):
                    fields["orcid"] = orcid
                else:
                    errors.append(f"Row {n}: '{orcid}' is not a valid ORCID (check for a typo).")

            start, end = cell(row, "member_from"), cell(row, "member_to")
            if start or end:
                try:
                    s = parse_date(start) if start else None
                    e = parse_date(end, end=True) if end else None
                except ValueError:
                    errors.append(f"Row {n}: dates should look like 2019, 2019-09, 2019-09-01 or 9/1/2019.")
                    continue
                if s and e and e < s:
                    errors.append(f"Row {n}: member_to is before member_from.")
                    continue
                periods[key].append((s, e))

        seen_orcid = defaultdict(list)
        for key, entry in plan.items():
            o = entry["fields"].get("orcid") or (entry["person"].orcid if entry["person"] else "")
            if o: seen_orcid[o].append(key)
        errors += [f"ORCID {o} is given for more than one person: {', '.join(k)}" for o, k in seen_orcid.items() if len(k) > 1]

        if errors:
            for e in errors:
                self.stderr.write(self.style.ERROR(e))
            raise CommandError(f"{len(errors)} problem(s) found; nothing was saved. Fix the file and run again.")

        with transaction.atomic():
            for key, entry in plan.items():
                person, fields = entry["person"], entry["fields"]
                if person is None:
                    person = Person(slug=key, name=fields.pop("name"))
                    changes.append(f"add {person.name}")
                diffs = [f"{k}: {getattr(person, k)!r} -> {v!r}" for k, v in fields.items() if getattr(person, k) != v]
                if person.pk and "name" in fields and fields["name"] != person.name:
                    person.variants = sorted(set(person.variants) | {person.name})
                for k, v in fields.items():
                    setattr(person, k, v)
                person.save()
                if key in periods:
                    old = [(m.start, m.end) for m in person.memberships.all()]
                    if old != periods[key]:
                        person.memberships.all().delete()
                        for s, e in periods[key]:
                            Membership.objects.create(person=person, start=s, end=e)
                        diffs.append("membership: " + "; ".join(f"{s or '…'} to {e or 'now'}" for s, e in periods[key]))
                if diffs:
                    changes.append(f"{person.name}: " + ", ".join(diffs))
            for c in changes:
                self.stdout.write(c)
            if dry_run:
                transaction.set_rollback(True)
        verb = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(changes)} people." + (" Nothing saved (dry run)." if dry_run else "")))
