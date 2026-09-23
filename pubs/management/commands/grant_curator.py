from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Give a user curator rights: access to the Curate (admin) pages and permission to edit entries."

    def add_arguments(self, parser):
        parser.add_argument("username", help="The username as your OKD login reports it")
        parser.add_argument("--revoke", action="store_true")

    def handle(self, username, revoke, **opts):
        group, _ = Group.objects.get_or_create(name="Curators")
        group.permissions.set(Permission.objects.filter(content_type__app_label="pubs"))
        user, created = User.objects.get_or_create(username=username)
        if revoke:
            user.groups.remove(group)
            user.is_staff = False
        else:
            user.groups.add(group)
            user.is_staff = True
        user.save()
        verb = "removed from" if revoke else "added to"
        self.stdout.write(self.style.SUCCESS(f"{username} {verb} Curators" + (" (new user)" if created else "")))
