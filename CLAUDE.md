# Publication tracker for the FNAL CMS group

Internal web tool that tracks the group's publications from CMS internal review (pre-approval,
CWR, ...) through PAS, preprint and journal publication. It replaces a hand-maintained document
listing papers with "significant participation" of group members. Most entries are CMS
collaboration papers; which ones count is decided by **self-reporting**: members claim the papers
they contributed to significantly, with their roles (analysis contact, CCLE, ARC chair, editor, ...).

## Status

- **Stage 1, done:** data model, import of the old list (156 works, 76 people), searchable list with
  facets, work and person pages, curator editing via Django admin ("Curate"), status history,
  deployment (now the Helm chart).
- **Stage 2, done:** members add papers (with INSPIRE auto-fill from a CADI number / arXiv ID / DOI),
  add themselves to papers ("Add me"), and edit any entry. Every save goes to `EditLog`.
- **Stage 3, done (not yet deployed):** nightly INSPIRE sync (`pubs/sync.py`, `manage.py sync_inspire`, chart
  CronJob `inspireSync.enabled`, off by default). Looks up works by DOI, arXiv, CADI, then record number; only fills
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
seed entries, run from a laptop, found 113 and parsed them without errors. **Login chain tested
locally only** (2026-09-25): oauth2-proxy 7.15.4 (htpasswd login standing in for OIDC) in front of
gunicorn in `AUTH_MODE=proxy`: the proxy replaced the client's Authorization header with
`email:proxy-password`, and forged or bypassing requests stayed anonymous. **Never tested:** the Helm
chart on a real cluster (only `helm lint`, `helm template` and kubeconform), a real OIDC provider,
and `tools/lookup_ids.py` against live INSPIRE/ORCID. Tests
use simulated responses. If INSPIRE's JSON differs from what `inspire.to_fields` / `lookup_ids.py`
expect, fix the parsing there first.

## Decisions made with the user (don't change without asking)

- Hosted **internal-only** on the institution's cluster (OpenShift/OKD); no public site for now. A
  public export may come later, which is why every work has a `visibility` field.
- Deployed with the **Helm chart** in `charts/pubtracker`, using **only standard Kubernetes resources**
  (no Routes, ImageStreams, BuildConfigs, OpenShift OAuth or serving-cert annotations), though it must
  keep running on OpenShift (decided 2026-09-25).
- **SQLite, not Postgres**, to keep the number of services down (decided 2026-09-25).
- Sign-in via the **oauth2-proxy subchart** (OIDC) with an explicit allow-list; TLS via an Ingress
  and an optional **cert-manager `Certificate`**.
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

Django 5.2, SQLite everywhere (WAL mode, `transaction_mode=IMMEDIATE`, 20 s busy timeout), gunicorn,
whitenoise. No JS framework; plain
templates with a little inline JS (the contributor formset's "Add another person").

```
config/settings.py      all settings from environment variables (see below)
pubs/models.py          Person, Membership, Role, Work, Contribution, WorkLink, StatusChange, EditLog
pubs/views.py           list/detail/person pages; stage 2 views at the bottom (new, edit, add-me)
pubs/forms.py           WorkForm, contributor formsets, AddMeForm, PersonForm
pubs/inspire.py         INSPIRE lookup (classify input, query, keep only records carrying the identifier, map to fields)
pubs/sync.py            nightly sync logic; run by management command sync_inspire
pubs/exports.py         CSV, plain text, LaTeX and BibTeX writers for the list downloads
pubs/auth.py            ProxyAuthMiddleware: accepts the user oauth2-proxy names only with AUTH_PROXY_SECRET
pubs/management/commands/  import_seed, import_roster, export_roster, grant_curator, check_inspire,
                        sync_inspire, backup_db
pubs/templates/pubs/    templates;  pubs/static/pubs/app.css  all styles
seed/                   example_seed.json: fictional, committed, used by the tests. The real
                        fnal_publications_seed.json lives here locally but is gitignored (never commit it)
tools/lookup_ids.py     standalone (stdlib only) INSPIRE/ORCID ID suggester for the roster CSV
charts/pubtracker/      Helm chart: app Deployment + PVC, oauth2-proxy subchart, Ingress, Certificate,
                        NetworkPolicy, CronJobs (backup 02:30, INSPIRE sync 03:15); ci/test-values.yaml
```

## Running and testing

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python manage.py migrate && python manage.py import_seed seed/example_seed.json   # or the real seed, if you have it
python manage.py createsuperuser
DJANGO_DEBUG=1 python manage.py runserver
python manage.py test pubs          # 43 tests; keep them passing and add tests for new features
```

Tests override `STORAGES` so they don't need `collectstatic`. Chart checks: `helm dependency build
charts/pubtracker && helm lint charts/pubtracker -f charts/pubtracker/ci/test-values.yaml` (the chart
refuses to render without host, image, OIDC issuer and a sign-in allow-list, hence the ci values). `import_roster --dry-run` shows changes
without saving and saves nothing if any row is invalid.

## Deployment constraints (easy to break)

- **Login safety.** oauth2-proxy runs as its own Deployment, so the app is reachable inside the cluster.
  It trusts a user only via HTTP Basic auth carrying `AUTH_PROXY_SECRET` (oauth2-proxy
  `--pass-basic-auth --prefer-email-to-user` plus `OAUTH2_PROXY_BASIC_AUTH_PASSWORD`, both from the
  chart Secret's `proxy-password`), checked on every request (not the persistent remote-user variant).
  Never go back to trusting `X-Forwarded-User`. The NetworkPolicy is a second layer, not the only one.
- **Sign-in allow-list.** Members can edit anything, so `pubtracker.validate` refuses `emailDomains: ["*"]`
  without `allowed-group` (oauth2-proxy admits list OR domain, so `*` admits everyone) and refuses
  configs where nobody could sign in.
- **SQLite on a ReadWriteOnce volume**: exactly one app replica (`Recreate`), block storage (no NFS),
  and the CronJobs use required podAffinity to land on the app pod's node to mount the same volume.
- **Subchart templating.** `pubtracker.fullname` and `pubtracker.secretName` use only `.Release.Name` and
  `.Values.global.pubtracker`, because the oauth2-proxy subchart calls them from its `tpl`'d values
  (`extraArgs.upstream`, `redirect-url`, `extraEnv`). Keep it that way.
- **OpenShift compatibility** without OpenShift resources: images must run under an arbitrary UID in
  group 0 (`chmod -R g=u`), the chart drops `fsGroup`/`runAsUser` when `security.openshift.io/v1` is
  served (`openshift: auto|true|false`), and the subchart's fixed UID 2000 is nulled out.
- Probes are `exec` probes (localhost is an allowed host; not affected by the NetworkPolicy).
  Migrations run in `entrypoint.sh`. Root filesystem is read-only; `/tmp` is an emptyDir.
- The **seed file is kept out of the image** (`.dockerignore`) because it contains internal CMS
  information; it is streamed in with `kubectl exec -i ... import_seed /dev/stdin`.
- Outbound access from the pod to inspirehep.net is **not yet confirmed**. Everything must keep
  working without it (the forms fall back to manual entry; `check_inspire` reports the problem).

Settings env vars: `DJANGO_SECRET_KEY`, `SQLITE_PATH`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_TRUSTED_ORIGINS`, `AUTH_MODE` (local|proxy), `AUTH_PROXY_SECRET`, `SITE_TITLE`,
`INSPIRE_URL`, `INSPIRE_TIMEOUT`, `GUNICORN_BIND`, `GUNICORN_WORKERS`, `FORWARDED_ALLOW_IPS`.

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
