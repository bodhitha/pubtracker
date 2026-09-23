import csv
import sys

from django.core.management.base import BaseCommand

from pubs.models import Person


class Command(BaseCommand):
    help = "Write the current roster as CSV (to stdout), in the format import_roster reads."

    def handle(self, **opts):
        w = csv.writer(sys.stdout)
        w.writerow(["id", "name", "spellings_in_source", "entries", "in_group (yes/no)", "inspire_id", "orcid",
                    "member_from", "member_to"])
        for p in Person.objects.prefetch_related("memberships", "contributions"):
            group = "" if p.in_group is None else ("yes" if p.in_group else "no")
            periods = list(p.memberships.all()) or [None]
            for m in periods:
                w.writerow([p.slug, p.name, "; ".join(p.variants), p.contributions.count(), group, p.inspire_id,
                            p.orcid, (m.start.isoformat() if m and m.start else ""),
                            (m.end.isoformat() if m and m.end else "")])
