from django.core.management.base import BaseCommand, CommandError

from pubs import inspire


class Command(BaseCommand):
    help = "Check whether this server can reach INSPIRE (needed for auto-fill and the nightly sync)."

    def handle(self, **opts):
        try:
            inspire.check()
        except inspire.InspireUnavailable as e:
            raise CommandError(f"Can't reach INSPIRE: {e}\nAsk your OKD admins to allow outbound HTTPS to "
                               "inspirehep.net from this namespace. The forms still work without it.")
        self.stdout.write(self.style.SUCCESS("INSPIRE is reachable. Auto-fill will work."))
