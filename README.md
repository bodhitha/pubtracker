# Publication tracker (stages 1 to 3)

An internal tool for tracking the group's publications, from pre-approval to journal
publication. Stage 1 gives you:

- the old list imported as 156 structured records (76 people, 77 CADI numbers)
- a searchable list with filters by person, role, status, category, visibility and year
- pages for each publication and each person
- curator editing through the **Curate** pages, with every status change logged
- login through your OKD cluster, with access controlled by ordinary OKD roles

Stage 2 lets every group member:

- **add a paper** (Add a paper, in the header): type a CADI number, arXiv ID or DOI to fill in the
  details from INSPIRE, or fill in the form by hand for work still in internal review
- **add themselves** to an existing paper with their roles ("Add me to this paper"); the first
  time, they pick their name from the roster and their login is linked to it
- **edit any entry**, including other people's roles and the review notes from the old list

Entries are visible to the group as soon as they're saved. Every save is recorded in the entry's
edit history (who, when, what changed), and visibility is worked out automatically: internal
while in CMS review, public once any public identifier exists. Deleting entries is left to
curators, on the Curate pages. A lookup that matches a paper already in the tracker offers
"Add me to this paper" instead of creating a duplicate, and saving a duplicate CADI number,
arXiv ID or DOI is refused.

The **Download** menu (next to "Sort by" on the publications list) saves exactly the entries the list is
showing, with the current search, filters and order. Filter by person for a CV, by status for
what's published, or clear the filters for everything. (The year filter is the year an entry was
last updated, not the publication year.)

- **CSV**: a spreadsheet with identifiers, status, journal reference, group contributors and
  their roles, review notes and a link back to each entry.
- **text**: a numbered list ("CMS Collaboration, "Title", JHEP 04 (2025) 109, arXiv:..."), ready
  to paste into a report, with who in the group did what under each entry. Filtered by person,
  it shows just that person's roles.
- **LaTeX**: the same list as an `enumerate` to `\input` into a CV or report; symbols such as √s
  and H→bb̄ become LaTeX math. The links need `\usepackage{hyperref}`.
- **BibTeX**: INSPIRE's own entries (with INSPIRE's citation keys) for everything with an INSPIRE
  record number; entries built from the tracker's data for the rest, marked in the file. If
  INSPIRE can't be reached, every entry is built from the tracker's data. Only INSPIRE record
  numbers are sent to INSPIRE.

Files that include CMS-internal entries say so at the top; filter by Visibility: Public before
sharing one outside the group.

Stage 3 adds a nightly INSPIRE sync that fills in identifiers and journal references and moves
entries forward as they become preprints and journal papers (see step 6b below). Stage 4 will
add charts and exports.

## Try it on your laptop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py import_seed seed/example_seed.json
python manage.py createsuperuser
DJANGO_DEBUG=1 python manage.py runserver
```

Open http://localhost:8000 and sign in. `python manage.py test pubs` runs the tests.

`seed/example_seed.json` is fictional (made-up people, analyses and identifiers) so the tool can
be tried and tested anywhere. **The real data is not in this repository.** The group's seed file
(`fnal_publications_seed.json`) and roster CSVs contain internal CMS information; keep them
outside git (`.gitignore` blocks everything in `seed/` except the example, and all `.csv` files).
To work with real data locally, put the seed at `seed/fnal_publications_seed.json` and import
that instead (`import_seed --replace` swaps out the example).

## Deploy on OKD

All commands run in your project's namespace (`oc project <namespace>`).

**1. Database.** If your institution offers managed PostgreSQL, ask for a database and use its
connection string below. Otherwise run one in the namespace:

```bash
oc create secret generic pubtracker-db \
  --from-literal=POSTGRESQL_USER=pubtracker \
  --from-literal=POSTGRESQL_PASSWORD="$(openssl rand -hex 24)" \
  --from-literal=POSTGRESQL_DATABASE=pubtracker
oc apply -f openshift/postgres.yaml
```

This also sets up a nightly `pg_dump` to a separate volume, keeping 14 days.

**2. Secrets for the app and the login proxy.**

```bash
DB_PASS=$(oc get secret pubtracker-db -o jsonpath='{.data.POSTGRESQL_PASSWORD}' | base64 -d)
oc create secret generic pubtracker-app \
  --from-literal=DJANGO_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=DATABASE_URL="postgres://pubtracker:${DB_PASS}@pubtracker-db:5432/pubtracker"
oc create secret generic pubtracker-proxy --from-literal=session_secret="$(openssl rand -hex 16)"
```

**3. Build the image** from this directory (no Git credentials needed):

```bash
oc new-build --strategy=docker --binary --name=pubtracker
oc set image-lookup pubtracker
oc start-build pubtracker --from-dir=. --follow
```

**4. Deploy.** Apply once to create the Route, read its hostname, put that hostname into the
ConfigMap, and apply again:

```bash
oc apply -f openshift/app.yaml
oc get route pubtracker -o jsonpath='{.spec.host}'
# edit DJANGO_ALLOWED_HOSTS and DJANGO_CSRF_TRUSTED_ORIGINS in openshift/app.yaml, then:
oc apply -f openshift/app.yaml && oc rollout restart deployment/pubtracker
```

**5. Load the data and name the curators.** The seed file contains internal CMS information,
so it is kept out of the image and streamed in directly:

```bash
oc exec -i deploy/pubtracker -c app -- python manage.py import_seed /dev/stdin < seed/fnal_publications_seed.json
oc exec deploy/pubtracker -c app -- python manage.py grant_curator <your-okd-username>
```

**6. Check whether auto-fill can work.**

```bash
oc exec deploy/pubtracker -c app -- python manage.py check_inspire
```

If it can't reach INSPIRE, everything else still works; members just type the details in.
Ask your OKD admins to allow outbound HTTPS to `inspirehep.net` from the namespace, then run
the check again. No restart is needed.

**6b. Turn on the nightly INSPIRE sync** once the check passes. Look at what it would change
first, then schedule it:

```bash
oc exec deploy/pubtracker -c app -- python manage.py sync_inspire --dry-run
oc apply -f openshift/inspire-sync.yaml
```

Every night at 03:15 (after the database dump) it looks up each entry that has a CADI number,
arXiv ID, DOI or INSPIRE record number, fills in identifiers and journal references that are
still empty, and moves the status forward (PAS public, preprint, published). It never
overwrites what someone entered and never moves a status back. If INSPIRE contradicts an
entry, or gives an identifier that another entry already has, it changes nothing and marks
the entry "Needs review", with the details in its edit history. Changes appear in each entry's
history as "Nightly INSPIRE check". `oc logs job/<name>` shows a run's output
(`oc get jobs` lists them); `sync_inspire W0012` checks a single entry.

**7. Let group members in.** Anyone with the `view` role on the namespace can open the tool:

```bash
oc adm policy add-role-to-user view <username>
# or, for a whole group defined in OKD:
oc adm policy add-role-to-group view <groupname>
```

Code updates: `oc start-build pubtracker --from-dir=. --follow`. The deployment restarts on
the new image and applies database migrations at start-up; existing data is kept.

## Filling in the roster

Fill in `people_roster_review.csv` (any spreadsheet program; save as **CSV UTF-8** so names
like Cristián Peña keep their accents), then check and load it:

```bash
# locally
python manage.py import_roster people_roster_review.csv --dry-run
python manage.py import_roster people_roster_review.csv
# on OKD
oc exec -i deploy/pubtracker -c app -- python manage.py import_roster /dev/stdin --dry-run < people_roster_review.csv
oc exec -i deploy/pubtracker -c app -- python manage.py import_roster /dev/stdin < people_roster_review.csv
```

`--dry-run` lists every change without saving. If any row has a problem (a mistyped ORCID,
an unknown id, a date in the wrong form), nothing is saved and each problem is listed by row.
Blank cells leave existing values alone, so the same file can be loaded again as it fills up.
To record two separate periods in the group, repeat the person's row with the second dates.
Leave `id` empty to add someone new. `python manage.py export_roster > roster.csv` writes the
current roster in the same format.

### Looking up INSPIRE IDs and ORCIDs

`tools/lookup_ids.py` searches INSPIRE for each group member and suggests IDs. Run it on any
computer that can reach inspirehep.net; it needs only Python 3, no packages:

```bash
python3 tools/lookup_ids.py people_roster_review.csv --only kevin-pedro -o test.csv   # quick check
python3 tools/lookup_ids.py people_roster_review.csv -o roster_with_ids.csv --contact you@fnal.gov
```

It adds columns to a copy of the roster: `match_confidence`, the suggested IDs, a link to the
INSPIRE profile, the person's Fermilab positions on INSPIRE, and a note on anything uncertain.
Only `high` matches (a matching name and the only such profile with a Fermilab position) are
copied into `inspire_id` and `orcid`, and only where those were empty. Sort by
`match_confidence`, open the profile links for anything `medium`, `low` or `none`, then load
the file with `import_roster` as above; it ignores the extra columns.

## Things to check with your OKD admins

- **oauth-proxy image tag.** `openshift/app.yaml` uses `quay.io/openshift/origin-oauth-proxy:4.16`;
  use the tag matching your cluster. If your admins prefer that apps log in directly with the
  lab's single sign-on (OIDC), the app only needs a different login backend, and the rest stays.
- **Base images.** The Dockerfile pulls from `registry.access.redhat.com` and the database from
  `quay.io`. Some clusters require a mirror.
- **Outbound network access.** Auto-fill and the nightly sync need HTTPS to `inspirehep.net`.
  Some clusters block this by default.
- **Backups.** Ask whether volume snapshots already cover the database; either way, copy the
  nightly dumps somewhere off the cluster.

## How login works

The pod runs two containers. The **oauth-proxy** container is the only thing the Route can
reach: it sends people through the cluster's login page and checks that they have access to
the namespace. It then forwards the request, with the username in `X-Forwarded-User`, to the
**app** container, which listens only on `127.0.0.1` inside the pod and so cannot be reached
around the proxy. New users are created automatically on their first visit.
`grant_curator` gives someone access to the Curate pages.

## Layout

```
config/            Django settings (all read from environment variables)
pubs/models.py     people, memberships, works, contributions, roles, links, status history
pubs/views.py      list, detail and people pages; /healthz for probes
pubs/management/   import_seed, import_roster, export_roster, grant_curator, check_inspire, sync_inspire
pubs/forms.py      the member-facing forms;  pubs/inspire.py  INSPIRE lookup;  pubs/sync.py  nightly sync
pubs/templates/    page templates;  pubs/static/pubs/app.css  styles
seed/              example_seed.json (fictional, for tests); the real seed stays outside git and the image
tools/lookup_ids.py  suggests INSPIRE IDs and ORCIDs for the roster (run anywhere)
openshift/         app.yaml (app, proxy, Service, Route), postgres.yaml (database and backups),
                   inspire-sync.yaml (nightly INSPIRE sync)
```
