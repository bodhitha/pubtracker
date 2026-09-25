import os
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Copy the SQLite database to a dated file in DIRECTORY, keeping the newest --keep copies."

    def add_arguments(self, parser):
        parser.add_argument("directory")
        parser.add_argument("--keep", type=int, default=14, help="How many daily copies to keep (default 14)")

    def handle(self, directory, keep, **opts):
        if connection.vendor != "sqlite":
            raise CommandError("backup_db only works with SQLite.")
        if keep < 1:
            raise CommandError("--keep must be at least 1.")
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"pubtracker-{date.today():%Y-%m-%d}.sqlite3"
        partial = target.with_name(target.name + ".partial")

        # SQLite's online backup gives a consistent copy even while the app is writing
        connection.ensure_connection()
        with closing(sqlite3.connect(partial)) as dest:
            connection.connection.backup(dest)
        os.replace(partial, target)

        copies = sorted(folder.glob("pubtracker-*.sqlite3"), reverse=True)
        for old in copies[keep:]:
            old.unlink()
        self.stdout.write(f"Saved {target} ({target.stat().st_size // 1024} KB); "
                          f"{min(len(copies), keep)} copies kept.")
