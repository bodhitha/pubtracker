# Publication tracker (stages 1 to 3)

An internal tool for tracking the group's publications, from pre-approval to journal
publication. Stage 1 gives you:

- the old list imported as 156 structured records (76 people, 77 CADI numbers)
- a searchable list with filters by person, role, status, category, visibility and year
- pages for each publication and each person
- curator editing through the **Curate** pages, with every status change logged
- sign-in through your institution's single sign-on (OIDC, via oauth2-proxy), limited to the people you list

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
entries forward as they become preprints and journal papers (see step 7 below). Stage 4 will
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

## Deploy with Helm

The chart in `charts/pubtracker` uses only standard Kubernetes resources, so it runs on any
cluster, OpenShift/OKD included. You need `helm` (3.8+), `kubectl`, an image registry the
cluster can pull from, an OIDC client at your sign-in service, and, for automatic TLS,
[cert-manager](https://cert-manager.io). All commands run in your namespace
(`kubectl config set-context --current --namespace=<namespace>`) and assume the release is
called `pubtracker`.

**1. Build and push the image** (`docker` works the same way):

```bash
podman build -t registry.example.org/cms/pubtracker:1.0.0 .
podman push registry.example.org/cms/pubtracker:1.0.0
```

The chart's `appVersion` is the default tag; set `image.tag` for any other.

**2. Register an OIDC client** with your sign-in service, with the redirect URI
`https://<your host>/oauth2/callback`, and put its details in a Secret:

```bash
kubectl create secret generic pubtracker-oidc \
  --from-literal=client-id=<client id> \
  --from-literal=client-secret=<client secret> \
  --from-literal=cookie-secret="$(openssl rand -hex 16)"
```

**3. Write your values** in a file such as `my-values.yaml` (keep it out of git if it lists
people):

```yaml
global:
  pubtracker:
    host: pubtracker.example.org
image:
  repository: registry.example.org/cms/pubtracker
ingress:
  className: nginx                 # your cluster's ingress class
  tls:
    certManager:
      issuerRef:
        name: letsencrypt-prod     # a cert-manager ClusterIssuer
oauth2-proxy:
  config:
    existingSecret: pubtracker-oidc
  authenticatedEmailsFile:
    restricted_access: |-
      someone@example.org
      another@example.org
  extraArgs:
    oidc-issuer-url: https://sso.example.org/realms/example   # your sign-in service's issuer
```

**Who may sign in.** Members can edit every entry, so the chart refuses settings that would let
in anyone with an account at the sign-in service. Choose one of:

- a list of email addresses, as above;
- whole email domains: `oauth2-proxy.config.emailDomains: [example.org]` with
  `oauth2-proxy.authenticatedEmailsFile.enabled: false`;
- a group at the sign-in service: `emailDomains: ["*"]` with
  `oauth2-proxy.extraArgs.allowed-group: <group>` (the service must send a groups claim).

`charts/pubtracker/values.yaml` documents every setting. Without cert-manager, set
`ingress.tls.certManager.enabled: false` and `ingress.tls.secretName` to an existing TLS Secret.

**4. Install:**

```bash
helm dependency build charts/pubtracker
helm upgrade --install pubtracker charts/pubtracker -f my-values.yaml
kubectl rollout status deployment/pubtracker
```

**5. Load the data and name the curators.** The seed file contains internal CMS information,
so it is kept out of the image and streamed in directly. Curators are named by the email address
they sign in with:

```bash
kubectl exec -i deploy/pubtracker -- python manage.py import_seed /dev/stdin < seed/fnal_publications_seed.json
kubectl exec deploy/pubtracker -- python manage.py grant_curator someone@example.org
```

**6. Check whether auto-fill can work.**

```bash
kubectl exec deploy/pubtracker -- python manage.py check_inspire
```

If it can't reach INSPIRE, everything else still works; members just type the details in.
Ask your cluster admins to allow outbound HTTPS to `inspirehep.net` from the namespace, then run
the check again. No restart is needed.

**7. Turn on the nightly INSPIRE sync** once the check passes. Look at what it would change
first, then set `inspireSync.enabled: true` in your values and upgrade:

```bash
kubectl exec deploy/pubtracker -- python manage.py sync_inspire --dry-run
helm upgrade pubtracker charts/pubtracker -f my-values.yaml
```

Every night at 03:15 (after the database copy) it looks up each entry that has a CADI number,
arXiv ID, DOI or INSPIRE record number, fills in identifiers and journal references that are
still empty, and moves the status forward (PAS public, preprint, published). It never
overwrites what someone entered and never moves a status back. If INSPIRE contradicts an
entry, or gives an identifier that another entry already has, it changes nothing and marks
the entry "Needs review", with the details in its edit history. Changes appear in each entry's
history as "Nightly INSPIRE check". `kubectl get jobs` lists the runs and `kubectl logs job/<name>`
shows one; `sync_inspire W0012` checks a single entry. Schedules are in the cluster's time zone
(usually UTC) unless you set `cronJobs.timeZone`.

**Code updates:** build and push a new tag, then
`helm upgrade pubtracker charts/pubtracker -f my-values.yaml --set image.tag=<tag>`. The app
restarts on the new image and applies database migrations at start-up; existing data is kept.

**On OpenShift/OKD** the chart notices OpenShift's security API and leaves out the fixed
`fsGroup`, which OpenShift would refuse (it assigns UIDs and groups per namespace). When
rendering without cluster access (`helm template`, GitOps tools), set `openshift: true`. The
Ingress becomes a Route automatically; use the cluster's ingress class (often
`openshift-default`). The `Certificate` needs the cert-manager operator; otherwise turn it off
and supply a TLS Secret.

### Storage and backups

The database is one SQLite file on the `pubtracker-data` volume, which `helm uninstall` leaves
in place. It needs block storage with `ReadWriteOnce` access; don't use NFS or other network
filesystems, where SQLite's locking is unreliable. Only one app pod runs, and the nightly jobs
are scheduled on its node so they can share the volume.

Every night at 02:30 a job copies the database to the `pubtracker-backups` volume and keeps the
last 14 copies (`backup.*` in the values). Ask your admins whether volume snapshots cover these
volumes; either way, copy backups off the cluster now and then:

```bash
kubectl exec deploy/pubtracker -- python manage.py backup_db /tmp/copy --keep 1
kubectl cp "$(kubectl get pod -l app.kubernetes.io/name=pubtracker -o name | cut -d/ -f2)":/tmp/copy ./pubtracker-backup
```

To restore, scale the Deployment to 0, then from a pod that mounts `pubtracker-data` replace
`/data/pubtracker.sqlite3` with the copy and delete `pubtracker.sqlite3-wal` and
`pubtracker.sqlite3-shm` next to it; scale back to 1.

## Filling in the roster

Fill in `people_roster_review.csv` (any spreadsheet program; save as **CSV UTF-8** so names
like Cristián Peña keep their accents), then check and load it:

```bash
# locally
python manage.py import_roster people_roster_review.csv --dry-run
python manage.py import_roster people_roster_review.csv
# on the cluster
kubectl exec -i deploy/pubtracker -- python manage.py import_roster /dev/stdin --dry-run < people_roster_review.csv
kubectl exec -i deploy/pubtracker -- python manage.py import_roster /dev/stdin < people_roster_review.csv
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

## Things to check with your cluster admins

- **Sign-in service.** An OIDC client for the tool (redirect URI `https://<host>/oauth2/callback`),
  and, if you restrict sign-in by group, whether the service sends a groups claim.
- **Ingress and TLS.** The ingress class to use, and a cert-manager issuer (or a TLS Secret).
- **Storage.** A storage class with `ReadWriteOnce` block storage (not NFS) for the database.
- **NetworkPolicy.** Whether the cluster enforces it (most do); the chart uses one to let only
  oauth2-proxy reach the app.
- **Outbound network access.** Auto-fill and the nightly sync need HTTPS to `inspirehep.net`.
- **Images.** The app image comes from your registry (with `imagePullSecrets` if needed); its base
  image from `registry.access.redhat.com` and oauth2-proxy from `quay.io`. Some clusters require
  a mirror.

## How login works

The Ingress sends everything to **oauth2-proxy**, which signs people in with your sign-in
service (OIDC) and admits only the addresses, domains or groups you allowed. It then forwards
each request to the **app**, naming the person (their email address) in an HTTP Basic auth
header together with a password only the two of them know (`proxy-password` in the chart's
Secret). The app accepts a person only with that password, on every request, so a request that
didn't come through the proxy is anonymous. A NetworkPolicy also lets only oauth2-proxy reach
the app. New users are created on their first visit, named by their lowercased email address;
`grant_curator` gives someone access to the Curate pages. "Sign out" ends the oauth2-proxy
session.

## Layout

```
config/            Django settings (all read from environment variables)
pubs/models.py     people, memberships, works, contributions, roles, links, status history
pubs/views.py      list, detail and people pages; /healthz for probes
pubs/management/   import_seed, import_roster, export_roster, grant_curator, check_inspire, sync_inspire, backup_db
pubs/forms.py      the member-facing forms;  pubs/inspire.py  INSPIRE lookup;  pubs/sync.py  nightly sync
pubs/auth.py       accepts the user oauth2-proxy vouches for
pubs/templates/    page templates;  pubs/static/pubs/app.css  styles
seed/              example_seed.json (fictional, for tests); the real seed stays outside git and the image
tools/lookup_ids.py  suggests INSPIRE IDs and ORCIDs for the roster (run anywhere)
charts/pubtracker/ Helm chart: app, oauth2-proxy (subchart), Ingress, Certificate, NetworkPolicy,
                   nightly backup and INSPIRE sync
```
