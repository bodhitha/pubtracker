# Publication tracker for the FNAL CMS group

Internal web tool that tracks the group's publications from CMS internal review (pre-approval,
CWR, ...) through PAS, preprint and journal publication. It replaces a hand-maintained document
listing papers with "significant participation" of group members. Most entries are CMS
collaboration papers; which ones count is decided by **self-reporting**: members claim the papers
they contributed to significantly, with their roles (analysis contact, CCLE, ARC chair, editor, ...).

## Status

- **Stage 1, done:** data model, import of the old list (156 works, 76 people), searchable list with
  facets, work and person pages, curator editing via Django admin ("Curate"), status history, OKD
  deployment manifests.
- **Stage 2, done:** members add papers (with INSPIRE auto-fill from a CADI number / arXiv ID / DOI),
  add themselves to papers ("Add me"), and edit any entry. Every save goes to `EditLog`.
- **Stage 3, done (not yet deployed):** nightly INSPIRE sync (`pubs/sync.py`, `manage.py sync_inspire`,
  `openshift/inspire-sync.yaml`). Looks up works by DOI, arXiv, CADI, then record number; only fills
  blank fields and only moves status forward along `sync.STATUS_ORDER`; logs `StatusChange(source="inspire")`
  and `EditLog(action="inspire", user=None)`. Contradicting identifiers, or ones already on another
  work, change nothing and add the `inspire_mismatch` review flag plus a note (each note once).
  `last_update` moves only when status changes (it drives the Year filter). Still open: remind
  members of new INSPIRE papers they haven't claimed.
- **Stage 4, started:** exports done: `/export.<csv|txt|tex|bib>` (`views.work_export`, writers in
  `pubs/exports.py`) take the list page's current search, filters and sort via the shared `_search` /
  `_with_facets` / `_sort` helpers, so a download always matches what the list shows. CSV is UTF-8 with
  BOM for Excel, with formula-like cells prefixed with `'`; txt/tex/bib have no BOM (it breaks BibTeX).
  `exports.tex()` turns the Unicode in titles (√s, →, Greek, ⁻¹, b̄) into LaTeX math; the full seed
  export compiles with pdflatex. BibTeX fetches INSPIRE's entries by record number in batches of 50,
  matches them back by arXiv/DOI, and builds `@misc` entries locally for the rest or when INSPIRE is
  down. Still to do: charts (per year, per member, per role/category), a publication year for reports.

**Tested against live INSPIRE only once:** on 2026-09-23, a `sync_inspire --dry-run` of the 115 public
seed entries, run from a laptop, found 113 and parsed them without errors. **Never tested:**
`tools/lookup_ids.py` against live INSPIRE/ORCID, and the manifests on the actual OKD cluster. Tests
use simulated responses. If INSPIRE's JSON differs from what `inspire.to_fields` / `lookup_ids.py`
expect, fix the parsing there first.

## Decisions made with the user (don't change without asking)

- Hosted **internal-only** on the institution's **OKD** cluster; no public site for now. A public
  export may come later, which is why every work has a `visibility` field.
- **No approval step**: member entries are visible immediately.
- **Members can edit anything**, like curators. **Deleting is curator-only** (admin), because it's the
  one change the edit history can't undo.
- `visibility` is computed in `Work.compute_visibility()` on every save: internal while in CMS review
  (unless status is CWR with a public PAS number); public once any public identifier exists
  (arXiv, DOI, CDS, report number, URL, journal ref); otherwise "unverified". This rule reproduces
  the visibility of all 156 imported records; keep it that way.
- Large collaboration papers count only when a member claims them (self-reporting).
- CADI number is the key that merges PAS, preprint and paper into one record. Duplicate CADI /
  arXiv / DOI is refused in `WorkForm.clean()` with a link to the existing entry.
- Roles are a fixed vocabulary (`Role` table): author, lead_author, corresponding_author,
  analysis_contact, editor, ccle, arc_chair, arc_member, internal_reader, convener, developer,
  contributor.
- Everything the old document said that the import had to interpret is kept as `review_flags` on
  each work (see `REVIEW_FLAG_HELP`); members clear them from the edit form.

## Stack and layout

Django 5.2, PostgreSQL in production (SQLite locally), gunicorn, whitenoise. No JS framework; plain
templates with a little inline JS (the contributor formset's "Add another person").

```
config/settings.py      all settings from environment variables (see below)
pubs/models.py          Person, Membership, Role, Work, Contribution, WorkLink, StatusChange, EditLog
pubs/views.py           list/detail/person pages; stage 2 views at the bottom (new, edit, add-me)
pubs/forms.py           WorkForm, contributor formsets, AddMeForm, PersonForm
pubs/inspire.py         INSPIRE lookup (classify input, query, keep only records carrying the identifier, map to fields)
pubs/sync.py            nightly sync logic; run by management command sync_inspire
pubs/exports.py         CSV, plain text, LaTeX and BibTeX writers for the list downloads
pubs/auth.py            trusts X-Forwarded-User from the oauth-proxy sidecar (AUTH_MODE=header)
pubs/management/commands/  import_seed, import_roster, export_roster, grant_curator, check_inspire, sync_inspire
pubs/templates/pubs/    templates;  pubs/static/pubs/app.css  all styles
seed/                   example_seed.json: fictional, committed, used by the tests. The real
                        fnal_publications_seed.json lives here locally but is gitignored (never commit it)
tools/lookup_ids.py     standalone (stdlib only) INSPIRE/ORCID ID suggester for the roster CSV
openshift/              app.yaml (app + oauth-proxy, Service, Route), postgres.yaml (DB + nightly dump),
                        inspire-sync.yaml (CronJob, 03:15, after the dump)
```

## Running and testing

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python manage.py migrate && python manage.py import_seed seed/example_seed.json   # or the real seed, if you have it
python manage.py createsuperuser
DJANGO_DEBUG=1 python manage.py runserver
python manage.py test pubs          # 40 tests; keep them passing and add tests for new features
```

Tests override `STORAGES` so they don't need `collectstatic`. `import_roster --dry-run` shows changes
without saving and saves nothing if any row is invalid.

## OKD constraints (easy to break)

- The container runs under a **random non-root UID in group 0**: files must be group-writable
  (`chmod -R g=u`), no root, ports above 1024. Base image is UBI Python 3.12.
- Login is an **oauth-proxy sidecar**. gunicorn binds to **127.0.0.1:8080** (`GUNICORN_BIND`) so the
  proxy can't be bypassed; that's why the probes are `exec` probes, not HTTP probes.
  `ProxyHeaderMiddleware` is only safe because of this binding.
- Access is OKD RBAC: anyone with `view` on the namespace gets in (`--openshift-sar`).
  `grant_curator <user>` gives Curate access.
- Migrations run in `entrypoint.sh` at start-up; the Deployment uses `Recreate` with one replica.
- The **seed file is kept out of the image** (`.dockerignore`) because it contains internal CMS
  information; it is streamed in with `oc exec -i ... import_seed /dev/stdin`.
- Outbound access from the pod to inspirehep.net is **not yet confirmed**. Everything must keep
  working without it (the forms fall back to manual entry; `check_inspire` reports the problem).

Settings env vars: `DJANGO_SECRET_KEY`, `DATABASE_URL`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_TRUSTED_ORIGINS`, `AUTH_MODE` (local|header), `AUTH_HEADER`, `SITE_TITLE`,
`INSPIRE_URL`, `INSPIRE_TIMEOUT`, `GUNICORN_BIND`, `GUNICORN_WORKERS`.

## Data sensitivity

The seed, the roster CSVs and the database contain **internal CMS information** (analyses before
public release, ARC roles). Keep the repository private and never put this data anywhere public.
The GitHub copy (github.com/bodhitha/pubtracker) must never contain the data: the real seed was removed
from all git history before anything was pushed. Tests use the fictional `seed/example_seed.json`;
never add real CADI numbers, people's roles or other seed facts to tests, docs or commits.

## UI conventions

Quiet, dense working tool. System font stack (the internal network may not reach font CDNs). Colors
are CSS variables in `app.css` (`--accent` blue, `--internal` amber for CMS-internal stages, `--done`
green for published, `--flag` red for review notes) with a dark-mode variant. The five-step status
track (`_track.html`) is the signature element; reuse it rather than inventing new status displays.
Plain, specific wording in the interface; describe what things do, not how they're built.
