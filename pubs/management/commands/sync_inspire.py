import time

from django.core.management.base import BaseCommand, CommandError

from pubs import inspire, sync
from pubs.models import Work


class Command(BaseCommand):
    help = ("Check entries against INSPIRE: fill in missing identifiers and journal references, and move "
            "status forward (PAS, preprint, published). Values people entered are never overwritten.")

    def add_arguments(self, parser):
        parser.add_argument("codes", nargs="*", help="Only check these entries, e.g. W0012 W0040")
        parser.add_argument("--dry-run", action="store_true", help="List what would change without saving")
        parser.add_argument("--pause", type=float, default=0.5,
                            help="Seconds to wait between entries, to stay well under INSPIRE's rate limit")

    def handle(self, codes, dry_run, pause, **opts):
        works = list(Work.objects.filter(code__in=codes) if codes else sync.candidates())
        found = updated = flagged = failed = offline_in_a_row = 0
        for i, work in enumerate(works):
            if i and pause:
                time.sleep(pause)
            try:
                result = sync.sync_work(work, dry_run=dry_run)
                offline_in_a_row = 0
            except inspire.InspireUnavailable as e:
                failed += 1
                offline_in_a_row += 1
                self.stderr.write(f"{work.code}: couldn't reach INSPIRE ({e})")
                if offline_in_a_row >= 3:
                    raise CommandError("Stopped: INSPIRE couldn't be reached three times in a row. "
                                       "Run check_inspire to test the connection.")
                continue
            except Exception as e:  # INSPIRE's JSON is outside our control; one odd record shouldn't stop the run
                failed += 1
                self.stderr.write(f"{work.code}: {type(e).__name__}: {e}")
                continue
            found += result.found
            updated += bool(result.changes)
            flagged += bool(result.notes)
            for c in result.changes:
                self.stdout.write(f"{work.code} {work}: {c['field']}: {c['old'] or '(empty)'} -> {c['new'] or '(empty)'}")
            for n in result.notes:
                self.stdout.write(f"{work.code} {work}: needs a look: {n}")

        verb = "would be updated" if dry_run else "updated"
        self.stdout.write(f"Checked {len(works)} entries: {found} found on INSPIRE, {updated} {verb}, "
                          f"{flagged} need a look, {failed} failed.")
        if failed:
            raise CommandError(f"{failed} entries couldn't be checked; see the messages above.")
