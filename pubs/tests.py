from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings

from .models import Person, StatusChange, Work

PLAIN_STATIC = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
SEED = Path(__file__).resolve().parent.parent / "seed" / "example_seed.json"


@override_settings(ALLOWED_HOSTS=["testserver"], STORAGES=PLAIN_STATIC)
class SeedAndPagesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_seed", str(SEED), verbosity=0)
        cls.user = User.objects.create_user("member", password="pw")

    def test_import_counts(self):
        self.assertEqual(Work.objects.count(), 12)
        self.assertEqual(Person.objects.count(), 6)
        self.assertEqual(Work.objects.filter(visibility="internal").count(), 4)

    def test_login_required(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_person_role_filter(self):
        self.client.force_login(self.user)
        r = self.client.get("/", {"person": "alex-rivera", "role": "ccle"})
        self.assertContains(r, "4 of 12 publications")

    def test_detail_and_people_pages(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/works/W0004/"), "Submitted together with")
        self.assertContains(self.client.get("/people/"), "Alex Rivera")

    def test_status_change_is_logged(self):
        work = Work.objects.get(cadi="EXA-25-017")
        work.status = "approved"
        work.save(changed_by=self.user)
        latest = StatusChange.objects.filter(work=work).first()
        self.assertEqual((latest.old_status, latest.new_status, latest.changed_by), ("pre_approval", "approved", self.user))


PROXY_SECRET = "shared-with-oauth2-proxy-0123456789"


def _proxy_middleware():
    from django.conf import settings
    mw = [m for m in settings.MIDDLEWARE if m != "pubs.auth.ProxyAuthMiddleware"]
    mw.insert(mw.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1, "pubs.auth.ProxyAuthMiddleware")
    return mw


def _basic(user, password):
    import base64
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@override_settings(ALLOWED_HOSTS=["testserver"], STORAGES=PLAIN_STATIC, AUTH_PROXY_SECRET=PROXY_SECRET,
                   MIDDLEWARE=_proxy_middleware(),
                   AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.RemoteUserBackend"])
class ProxyAuthTest(TestCase):
    def test_proxy_vouched_user_is_signed_in(self):
        r = self.client.get("/", HTTP_AUTHORIZATION=_basic("Ada.Member@Example.org", PROXY_SECRET))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["user"].username, "ada.member@example.org")

    def test_forged_identities_are_refused(self):
        for headers in [{"HTTP_AUTHORIZATION": _basic("ada@example.org", "guessed-password")},
                        {"HTTP_X_FORWARDED_USER": "ada@example.org"},
                        {"HTTP_PUBTRACKER_PROXY_USER": "ada@example.org"},
                        {"HTTP_AUTHORIZATION": "Basic not-base64!"}]:
            self.assertEqual(self.client.get("/", **headers).status_code, 302, headers)
        self.assertFalse(User.objects.exists())

    def test_session_alone_is_not_enough(self):
        self.client.get("/", HTTP_AUTHORIZATION=_basic("ada@example.org", PROXY_SECRET))
        self.assertEqual(self.client.get("/").status_code, 302)   # same session, but not through the proxy


class BackupTest(TransactionTestCase):
    # not TestCase: SQLite's online backup waits while the connection has an open write transaction,
    # and the nightly job, like this test, runs outside one
    def test_backup_copies_database_and_keeps_newest(self):
        import sqlite3
        import tempfile
        Person.objects.create(slug="kept", name="Kept Person")
        with tempfile.TemporaryDirectory() as folder:
            for old in ("2020-01-01", "2020-01-02", "2020-01-03"):
                Path(folder, f"pubtracker-{old}.sqlite3").write_text("old")
            call_command("backup_db", folder, "--keep", "2", stdout=open("/dev/null", "w"))
            copies = sorted(p.name for p in Path(folder).glob("pubtracker-*.sqlite3"))
            self.assertEqual(len(copies), 2)
            self.assertEqual(copies[0], "pubtracker-2020-01-03.sqlite3")
            with sqlite3.connect(Path(folder, copies[-1])) as db:
                self.assertEqual(db.execute("select name from pubs_person").fetchall(), [("Kept Person",)])


class RosterImportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_seed", str(SEED), verbosity=0)

    def _run(self, text, *args):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(text)
        call_command("import_roster", f.name, *args, stdout=open("/dev/null", "w"), stderr=open("/dev/null", "w"))

    HEADER = "id,name,in_group (yes/no),inspire_id,orcid,member_from,member_to\n"

    def test_updates_person_and_memberships(self):
        self._run(self.HEADER + "alex-rivera,Alex Rivera,yes,A.Rivera.1,0000-0002-1825-0097,2015,\n"
                  "sam-okafor,Sam Okafor,no,,,,\n")
        kp = Person.objects.get(slug="alex-rivera")
        self.assertEqual((kp.in_group, kp.inspire_id, kp.orcid), (True, "A.Rivera.1", "0000-0002-1825-0097"))
        self.assertEqual(kp.memberships.get().end, None)
        self.assertFalse(Person.objects.get(slug="sam-okafor").in_group)

    def test_bad_orcid_saves_nothing(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self._run(self.HEADER + "alex-rivera,Alex Rivera,yes,,0000-0002-1825-0098,,\n")
        self.assertIsNone(Person.objects.get(slug="alex-rivera").in_group)

    def test_dry_run_saves_nothing(self):
        self._run(self.HEADER + "jo-lindqvist,Jo Lindqvist,yes,,,,\n", "--dry-run")
        self.assertIsNone(Person.objects.get(slug="jo-lindqvist").in_group)


# ---- stage 2 -----------------------------------------------------------------------------
from unittest import mock

from . import inspire
from .models import EditLog, Role

SAMPLE_RECORD = {"hits": {"hits": [{"metadata": {
    "titles": [{"title": "Search for  something new\n in proton-proton collisions"}],
    "arxiv_eprints": [{"value": "2609.01234"}],
    "dois": [{"value": "10.1007/JHEP09(2026)001"}],
    "report_numbers": [{"value": "CMS-EXO-25-099"}, {"value": "CERN-EP-2026-123"}],
    "publication_info": [{"journal_title": "JHEP", "journal_volume": "09", "year": 2026, "artid": "001"}],
    "collaborations": [{"value": "CMS"}], "document_type": ["article"], "control_number": 3000001}}]}}


class InspireParsingTest(TestCase):
    def test_classify(self):
        self.assertEqual(inspire.classify("EXO-23-002"), ("cadi", "EXO-23-002"))
        self.assertEqual(inspire.classify("CMS-PAS-exo-23-002"), ("cadi", "EXO-23-002"))
        self.assertEqual(inspire.classify("https://arxiv.org/abs/2403.05311v2"), ("arxiv", "2403.05311"))
        self.assertEqual(inspire.classify("arXiv:2403.05311"), ("arxiv", "2403.05311"))
        self.assertEqual(inspire.classify("https://doi.org/10.1103/X.1"), ("doi", "10.1103/X.1"))
        with self.assertRaises(inspire.NotRecognized):
            inspire.classify("my great paper")

    def test_record_to_fields(self):
        f = inspire.to_fields(SAMPLE_RECORD["hits"]["hits"][0]["metadata"])
        self.assertEqual(f["title"], "Search for something new in proton-proton collisions")
        self.assertEqual((f["cadi"], f["arxiv"], f["status"], f["doc_type"]),
                         ("EXO-25-099", "2609.01234", "published", "cms_collab"))
        self.assertEqual(f["journal_ref"], "JHEP 09 (2026) 001")


@override_settings(ALLOWED_HOSTS=["testserver"], STORAGES=PLAIN_STATIC)
class MemberEditingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_seed", str(SEED), verbosity=0)
        cls.user = User.objects.create_user("member2")

    def setUp(self):
        self.client.force_login(self.user)

    def _post_new(self, **overrides):
        data = {"title": "A new search", "category": "analysis", "doc_type": "cms_collab", "status": "pre_approval",
                "cadi": "EXO-26-900", "contributions-TOTAL_FORMS": "1", "contributions-INITIAL_FORMS": "0",
                "contributions-MIN_NUM_FORMS": "0", "contributions-MAX_NUM_FORMS": "1000",
                "contributions-0-person": str(Person.objects.get(slug="alex-rivera").pk),
                "contributions-0-roles": [str(Role.objects.get(code="analysis_contact").pk)]}
        data.update(overrides)
        return self.client.post("/works/new/", data)

    def test_member_adds_internal_paper(self):
        r = self._post_new()
        work = Work.objects.get(cadi="EXO-26-900")
        self.assertRedirects(r, work.get_absolute_url())
        self.assertEqual((work.visibility, work.created_by), ("internal", self.user))
        self.assertEqual(work.contributions.get().roles.get().code, "analysis_contact")
        self.assertEqual(EditLog.objects.get(work=work).action, "created")

    def test_duplicate_is_refused_with_a_link(self):
        r = self._post_new(cadi="", arxiv="arXiv:2403.00001")      # EXA-23-002 is already in the seed
        self.assertContains(r, "already in the tracker")
        self.assertFalse(Work.objects.filter(title="A new search").exists())

    def test_edit_is_logged_and_visibility_follows(self):
        work = Work.objects.get(cadi="EXA-25-017")
        data = {f: getattr(work, f) or "" for f in ["title", "category", "doc_type", "cadi", "notes"]}
        data.update({"status": "pas_public", "report_number": "CMS-PAS-EXA-25-017", "resolved_flags": ["working_title"],
                     "contributions-TOTAL_FORMS": "1", "contributions-INITIAL_FORMS": "1",
                     "contributions-MIN_NUM_FORMS": "0", "contributions-MAX_NUM_FORMS": "1000"})
        c = work.contributions.get()
        data.update({"contributions-0-id": str(c.pk), "contributions-0-person": str(c.person_id),
                     "contributions-0-roles": [str(r.pk) for r in c.roles.all()]})
        self.client.post(f"/works/{work.code}/edit/", data)
        work.refresh_from_db()
        self.assertEqual((work.status, work.visibility, work.review_flags), ("pas_public", "public", []))
        fields = {c["field"] for c in work.edit_log.get().changes}
        self.assertTrue({"Status", "Report number", "Review notes"} <= fields)

    def test_add_me_links_login_once(self):
        work = Work.objects.get(cadi="EXA-23-002")
        me = Person.objects.get(slug="alex-rivera")
        editor = Role.objects.get(code="editor")
        self.client.post(f"/works/{work.code}/add-me/", {"person": me.pk, "roles": [editor.pk]})
        me.refresh_from_db()
        self.assertEqual(me.user, self.user)
        self.assertEqual(list(work.contributions.get(person=me).roles.all()), [editor])
        # second time: no person question, just roles
        r = self.client.get(f"/works/{work.code}/add-me/")
        self.assertNotContains(r, "Which person are you?")

    @override_settings(INSPIRE_URL="http://127.0.0.1:9/nothing", INSPIRE_TIMEOUT=1)
    def test_lookup_offline_still_prefills_identifier(self):
        r = self.client.get("/works/new/", {"lookup": "2609.01234"})
        self.assertContains(r, "Couldn&#x27;t reach INSPIRE")
        self.assertContains(r, 'value="2609.01234"')

    def test_lookup_found_and_existing(self):
        with mock.patch.object(inspire, "_get", return_value=SAMPLE_RECORD):
            r = self.client.get("/works/new/", {"lookup": "2609.01234"})
        self.assertContains(r, "Filled in from INSPIRE")
        self.assertContains(r, "EXO-25-099")
        with mock.patch.object(inspire, "_get", return_value={"hits": {"hits": []}}):
            r = self.client.get("/works/new/", {"lookup": "EXA-23-002"})
        self.assertContains(r, "already in the tracker")


# ---- stage 3: nightly INSPIRE sync -----------------------------------------------------------
from datetime import date
from io import StringIO

from django.core.management.base import CommandError

from . import sync


def _record(**meta):
    base = {"titles": [{"title": "A measurement"}], "control_number": 3000002, "collaborations": [{"value": "CMS"}],
            "document_type": ["article"], "report_numbers": [{"value": "CMS-SMP-24-001"}],
            "arxiv_eprints": [{"value": "2501.00001"}]}
    return {**base, **meta}


def _fake_inspire(*records):
    """Stand-in for inspire._get: returns every record; inspire.find keeps only real matches."""
    return mock.patch.object(inspire, "_get", return_value={"hits": {"hits": [{"metadata": r} for r in records]}})


class InspireSyncTest(TestCase):
    def _work(self, **kw):
        fields = {"title": "A measurement", "category": "analysis", "doc_type": "cms_collab", "status": "submitted",
                  "last_update": date(2025, 3, 1)}
        w = Work(**{**fields, **kw})
        w.save()
        return w

    def _run(self, *args):
        out, err = StringIO(), StringIO()
        call_command("sync_inspire", *args, "--pause", "0", stdout=out, stderr=err)
        return out.getvalue()

    def test_preprint_moves_to_published_and_fills_blanks(self):
        w = self._work(cadi="SMP-24-001", arxiv="2501.00001",
                       review_flags=["missing_identifier", "possibly_stale", "working_title"])
        journal = [{"journal_title": "Phys. Lett. B", "journal_volume": "860", "year": 2026, "artid": "139100"}]
        with _fake_inspire(_record(dois=[{"value": "10.1016/j.physletb.2026.139100"}], publication_info=journal)):
            out = self._run()
        w.refresh_from_db()
        self.assertEqual((w.status, w.doi, w.journal_ref, w.inspire_recid),
                         ("published", "10.1016/j.physletb.2026.139100", "Phys. Lett. B 860 (2026) 139100", "3000002"))
        self.assertEqual((w.title, w.last_update, w.review_flags), ("A measurement", date.today(), ["working_title"]))
        change = w.status_history.first()
        self.assertEqual((change.old_status, change.new_status, change.source), ("submitted", "published", "inspire"))
        log = w.edit_log.get()
        self.assertEqual((log.action, log.user), ("inspire", None))
        self.assertIn("1 updated", out)

    def test_never_overwrites_or_moves_backwards(self):
        w = self._work(arxiv="2501.00001", status="accepted", report_number="FERMILAB-PUB-25-0001")
        with _fake_inspire(_record(report_numbers=[{"value": "CMS-PAS-SMP-24-001"}])):
            self._run()
        w.refresh_from_db()
        self.assertEqual((w.status, w.report_number, w.last_update), ("accepted", "FERMILAB-PUB-25-0001", date(2025, 3, 1)))
        self.assertEqual(w.cadi, "SMP-24-001")      # blank, so filled in
        self.assertFalse(w.status_history.filter(source="inspire").exists())

    def test_contradiction_changes_nothing_and_is_noted_once(self):
        w = self._work(cadi="SMP-24-001", arxiv="2412.99999")
        with _fake_inspire(_record()):
            self._run()
            self._run()
        w.refresh_from_db()
        self.assertEqual((w.status, w.inspire_recid, w.review_flags), ("submitted", "", ["inspire_mismatch"]))
        note = w.edit_log.get().changes
        self.assertEqual(note[0]["field"], "INSPIRE check")
        self.assertIn("2412.99999", note[0]["new"])

    def test_identifier_on_another_entry_is_not_copied(self):
        other = self._work(title="The paper", doi="10.1103/X.1")
        w = self._work(title="The PAS", cadi="SMP-24-001", status="pas_public")
        with _fake_inspire(_record(dois=[{"value": "10.1103/X.1"}])):
            out = self._run(w.code)
        w.refresh_from_db()
        self.assertEqual((w.doi, w.arxiv, w.status), ("", "2501.00001", "preprint"))
        self.assertIn(other.code, out)
        self.assertEqual(w.review_flags, ["inspire_mismatch"])

    def test_unrelated_search_results_are_ignored(self):
        w = self._work(arxiv="2501.00002")
        with _fake_inspire(_record()):
            self._run()
        w.refresh_from_db()
        self.assertEqual((w.status, w.inspire_recid, w.review_flags), ("submitted", "", []))
        self.assertFalse(w.edit_log.exists())

    def test_dry_run_saves_nothing(self):
        w = self._work(arxiv="2501.00001")
        with _fake_inspire(_record(publication_info=[{"journal_title": "JHEP", "journal_volume": "01", "year": 2026}])):
            out = self._run("--dry-run")
        w.refresh_from_db()
        self.assertEqual((w.status, w.journal_ref), ("submitted", ""))
        self.assertIn("1 would be updated", out)
        self.assertFalse(w.edit_log.exists())

    def test_finished_and_identifierless_entries_are_skipped(self):
        todo = self._work(arxiv="2501.00001")
        self._work(title="Done", arxiv="2501.00003", status="published", inspire_recid="1", journal_ref="JHEP 01 (2026) 1")
        self._work(title="No identifiers", status="preprint")
        self.assertEqual(list(sync.candidates()), [todo])

    @override_settings(INSPIRE_URL="http://127.0.0.1:9/nothing", INSPIRE_TIMEOUT=1)
    def test_offline_stops_after_three_and_changes_nothing(self):
        works = [self._work(title=f"Paper {i}", arxiv=f"2501.0001{i}") for i in range(5)]
        with self.assertRaisesMessage(CommandError, "three times in a row"):
            self._run()
        self.assertFalse(EditLog.objects.exists())
        self.assertEqual({w.status for w in Work.objects.filter(pk__in=[w.pk for w in works])}, {"submitted"})


# ---- CSV export ------------------------------------------------------------------------------
import csv


@override_settings(ALLOWED_HOSTS=["testserver"], STORAGES=PLAIN_STATIC)
class CsvExportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_seed", str(SEED), verbosity=0)
        cls.user = User.objects.create_user("member")

    def _rows(self, **params):
        self.client.force_login(self.user)
        r = self.client.get("/export.csv", params)
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", r["Content-Disposition"])
        text = r.content.decode("utf-8")
        self.assertTrue(text.startswith("﻿"))
        return list(csv.DictReader(text.removeprefix("﻿").splitlines()))

    def test_needs_login(self):
        self.assertEqual(self.client.get("/export.csv").status_code, 302)

    def test_follows_list_filters_and_order(self):
        self.assertEqual(len(self._rows()), 12)
        rows = self._rows(person="alex-rivera", role="ccle", sort="title")
        self.assertEqual(len(rows), 4)   # same count as the list page shows for this filter
        self.assertEqual([r["Title"] for r in rows], sorted(r["Title"] for r in rows))
        self.assertTrue(all("Alex Rivera (" in r["Group contributors"] for r in rows))
        self.assertEqual({r["Status"] for r in self._rows(status="published")}, {"Published"})

    def test_contents_and_formula_cells(self):
        w = Work.objects.get(cadi="EXA-23-002")
        w.title = "=HYPERLINK(\"http://example.org\")"
        w.save()
        row = next(r for r in self._rows(q="EXA-23-002"))
        self.assertEqual(row["Title"], "'=HYPERLINK(\"http://example.org\")")
        self.assertEqual((row["Entry"], row["CADI"], row["arXiv"]), (w.code, "EXA-23-002", "2403.00001"))
        self.assertEqual(row["Link"], f"http://testserver/works/{w.code}/")

    def test_accented_names_survive(self):
        rows = self._rows(q="Muñoz")
        self.assertTrue(rows and all("Inés Muñoz" in r["Group contributors"] for r in rows))

    def test_list_links_to_export_with_same_filters(self):
        self.client.force_login(self.user)
        r = self.client.get("/", {"status": "published", "page": "2"})
        for fmt in ("csv", "txt", "tex", "bib"):
            self.assertContains(r, f'href="/export.{fmt}?status=published"')
        self.assertContains(r, "<summary>Download these</summary>", html=False)
        self.assertEqual(self.client.get("/export.pdf").status_code, 404)


# ---- text, LaTeX and BibTeX exports ------------------------------------------------------------
from . import exports


class TexEscapeTest(TestCase):
    def test_symbols_and_specials(self):
        self.assertEqual(exports.tex("pp collisions at √s = 13 TeV"), r"pp collisions at $\sqrt{s}$ = 13 TeV")
        self.assertEqual(exports.tex("PbPb at √sNN = 5.02"), r"PbPb at $\sqrt{s_{NN}}$ = 5.02")
        self.assertEqual(exports.tex("HH→bbγγ"), r"HH$\to$bb$\gamma\gamma$")
        self.assertEqual(exports.tex("H→bb̄ with 138 fb⁻¹"), r"H$\to$b$\bar{b}$ with 138 fb$^{-1}$")
        self.assertEqual(exports.tex("R&D: 50% of $x_1 {#}"), r"R\&D: 50\% of \$x\_1 \{\#\}")


@override_settings(ALLOWED_HOSTS=["testserver"], STORAGES=PLAIN_STATIC)
class FormattedExportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_seed", str(SEED), verbosity=0)
        cls.user = User.objects.create_user("member")
        cls.paper = Work.objects.get(cadi="EXA-23-002")
        cls.paper.inspire_recid = "2766001"
        cls.paper.save()

    def _get(self, fmt, **params):
        self.client.force_login(self.user)
        r = self.client.get(f"/export.{fmt}", params)
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'.{fmt}"', r["Content-Disposition"])
        return r.content.decode("utf-8")

    def test_text_lists_group_and_warns_about_internal_entries(self):
        text = self._get("txt")
        self.assertEqual(sum(line[:1].isdigit() for line in text.splitlines()), 12)
        self.assertIn("Includes 4 entries still in CMS internal review", text)
        self.assertNotIn("..", text.replace("...", ""))
        self.assertIn("CMS Collaboration, “", text)
        self.assertIn("Group contributors: ", text)

    def test_person_filter_shows_only_their_roles(self):
        text = self._get("txt", person="alex-rivera", role="ccle")
        role_lines = [line.strip() for line in text.splitlines() if line.startswith("   ")]
        self.assertEqual(len(role_lines), 4)
        self.assertTrue(all(line.startswith("Alex Rivera: ") and "CCLE" in line for line in role_lines))

    def test_latex_is_one_enumerate_with_links(self):
        tex = self._get("tex", status="published")
        self.assertIn(r"\begin{enumerate}", tex)
        self.assertTrue(tex.rstrip().endswith(r"\end{enumerate}"))
        self.assertEqual(tex.count(r"\item "), Work.objects.filter(status="published").count())
        self.assertIn(r"\href{https://arxiv.org/abs/", tex)
        self.assertNotIn("√", tex)

    def test_bibtex_uses_inspire_where_it_can(self):
        inspire_entry = ('@article{CMS:2024abc,\n    author = "Hayrapetyan, Aram and others",\n'
                         '    title = "{From INSPIRE}",\n    eprint = "2403.00001",\n    year = "2024"\n}')
        with mock.patch.object(inspire, "bibtex", return_value=inspire_entry) as fetch:
            bib = self._get("bib", q="EXA-2")
        fetch.assert_called_once_with(["2766001"])     # only public record numbers are sent to INSPIRE
        self.assertIn(inspire_entry, bib)
        self.assertIn("1 entry comes from INSPIRE", bib)
        local = bib.count("% From the tracker")
        self.assertEqual(local + 1, bib.count("\n@"))
        self.assertIn("@misc{CMS:", bib)

    def test_bibtex_without_inspire_is_all_local(self):
        with mock.patch.object(inspire, "bibtex", side_effect=inspire.InspireUnavailable("offline")):
            bib = self._get("bib", q="EXA-23-002")
        self.assertIn("INSPIRE couldn't be reached", bib)
        self.assertIn("% From the tracker", bib)
        self.assertIn("eprint = {2403.00001}", bib)


# ---- People page sorting -----------------------------------------------------------------------
@override_settings(ALLOWED_HOSTS=["testserver"], STORAGES=PLAIN_STATIC)
class PeopleSortTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("member")
        for slug, name, in_group, inspire_id, entries in [
                ("ann", "Ann Ames", True, "A.Ames.1", 1), ("bo", "Bo Berg", None, "", 3),
                ("cy", "Cy Cole", False, "C.Cole.1", 0), ("di", "Di Dunn", True, "", 2)]:
            p = Person.objects.create(slug=slug, name=name, in_group=in_group, inspire_id=inspire_id)
            for i in range(entries):
                w = Work(title=f"{name} {i}", category="analysis", doc_type="cms_collab", status="preprint")
                w.save()
                w.contributions.create(person=p)

    def _names(self, **params):
        self.client.force_login(self.user)
        return [p.name for p in self.client.get("/people/", params).context["people"]]

    def test_default_is_most_entries_first(self):
        self.assertEqual(self._names(), ["Bo Berg", "Di Dunn", "Ann Ames", "Cy Cole"])
        self.assertEqual(self._names(sort="nonsense"), self._names())

    def test_each_column_both_ways(self):
        self.assertEqual(self._names(sort="name"), ["Ann Ames", "Bo Berg", "Cy Cole", "Di Dunn"])
        self.assertEqual(self._names(sort="-name"), ["Di Dunn", "Cy Cole", "Bo Berg", "Ann Ames"])
        self.assertEqual(self._names(sort="entries"), ["Cy Cole", "Ann Ames", "Di Dunn", "Bo Berg"])
        self.assertEqual(self._names(sort="group"), ["Ann Ames", "Di Dunn", "Cy Cole", "Bo Berg"])
        self.assertEqual(self._names(sort="-group"), ["Bo Berg", "Cy Cole", "Ann Ames", "Di Dunn"])

    def test_missing_inspire_ids_stay_last(self):
        self.assertEqual(self._names(sort="inspire"), ["Ann Ames", "Cy Cole", "Bo Berg", "Di Dunn"])
        self.assertEqual(self._names(sort="-inspire"), ["Cy Cole", "Ann Ames", "Bo Berg", "Di Dunn"])

    def test_headers_toggle_and_keep_the_filter(self):
        self.client.force_login(self.user)
        r = self.client.get("/people/", {"group": "unconfirmed", "sort": "name"})
        self.assertEqual([p.name for p in r.context["people"]], ["Bo Berg"])
        self.assertContains(r, 'aria-sort="ascending"><a href="?group=unconfirmed&amp;sort=-name">Name</a>')
        self.assertContains(r, 'href="?group=unconfirmed&amp;sort=-entries">Entries</a>')
        self.assertContains(r, 'href="?sort=name"')   # "everyone" keeps the sort
