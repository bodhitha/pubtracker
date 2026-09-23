import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from pubs.models import Contribution, Person, Role, StatusChange, Work, WorkLink

ROLE_LABELS = {
    "author": "Author", "lead_author": "Lead author", "corresponding_author": "Corresponding author",
    "analysis_contact": "Analysis contact", "editor": "Editor", "ccle": "CCLE", "arc_chair": "ARC chair",
    "arc_member": "ARC member", "internal_reader": "Internal reader", "convener": "Convener",
    "developer": "Developer", "contributor": "Contributor",
}
ID_FIELDS = ["cadi", "arxiv", "doi", "cds", "report_number", "url"]


def parse_date(text):
    if not text:
        return None, "day"
    parts = [int(p) for p in text.split("-")]
    if len(parts) == 2:
        return date(parts[0], parts[1], 1), "month"
    return date(*parts), "day"


class Command(BaseCommand):
    help = "Import the structured seed file converted from the old publication list."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--replace", action="store_true",
                            help="Delete all existing works and people first")

    @transaction.atomic
    def handle(self, path, replace, **opts):
        data = json.load(open(path, encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise CommandError("Unsupported seed schema version")
        if replace:
            Work.objects.all().delete()
            Person.objects.all().delete()
        elif Work.objects.exists():
            raise CommandError("Works already exist. Use --replace to start over.")

        roles = {}
        for i, code in enumerate(data["vocabularies"]["roles"]):
            roles[code], _ = Role.objects.update_or_create(
                code=code, defaults={"label": ROLE_LABELS.get(code, code), "order": i})

        people = {}
        for p in data["people"]:
            people[p["id"]] = Person.objects.create(
                slug=p["id"], name=p["name"], variants=p["variants"], in_group=p["in_group"],
                inspire_id=p["inspire_id"] or "", orcid=p["orcid"] or "")

        works = {}
        for w in data["works"]:
            ids = w["identifiers"]
            day, precision = parse_date(w["last_update"])
            work = Work(code=w["id"], title=w["title"], category=w["category"], doc_type=w["doc_type"],
                        status=w["status"], visibility=w["visibility"], journal_ref=w["journal_ref"] or "",
                        venue=w["venue"] or "", has_other_authors=w["has_other_authors"], last_update=day,
                        last_update_precision=precision, notes=w["notes"] or "", review_flags=w["review_flags"],
                        source_section=w["source_section"],
                        **{f: ids.get(f) or ("" if f != "cadi" else None) for f in ID_FIELDS})
            work.save(change_source="import")
            works[w["id"]] = work
            for order, c in enumerate(w["contributors"]):
                contrib = Contribution.objects.create(
                    work=work, person=people[c["person"]], order=order,
                    membership_uncertain=c.get("membership_uncertain", False))
                contrib.roles.set([roles[r] for r in c["roles"]])

        links = 0
        for w in data["works"]:
            for link in w["links"]:
                rel = link["relation"].split(" ")[0]
                a, b = works[w["id"]], works[link["work"]]
                if WorkLink.objects.filter(from_work=b, to_work=a, relation=rel).exists():
                    continue  # the seed lists each link from both ends
                _, made = WorkLink.objects.get_or_create(from_work=a, to_work=b,
                                                         relation=rel)
                links += made
        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(works)} works, {len(people)} people, {links} links, "
            f"{StatusChange.objects.count()} status entries."))
