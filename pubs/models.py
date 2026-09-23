from django.conf import settings
from django.db import models
from django.urls import reverse


class Category(models.TextChoices):
    ANALYSIS = "analysis", "CMS physics analysis"
    DETECTOR = "detector", "Detector, trigger & instrumentation"
    COMPUTING = "computing", "Software & computing"
    ML = "ml", "ML & statistical methods"
    PHENO = "pheno", "Phenomenology & theory"
    OUTREACH = "outreach", "Outreach & education"
    STRATEGY = "strategy", "Strategy, Snowmass & community"


class DocType(models.TextChoices):
    CMS = "cms_collab", "CMS collaboration publication"
    JOURNAL = "journal_article", "Journal article"
    PROCEEDINGS = "proceedings", "Conference proceedings"
    REPORT = "report", "Report or white paper"
    BOOK = "book", "Book"
    MAGAZINE = "magazine_article", "Magazine article"


class Status(models.TextChoices):
    IN_PREPARATION = "in_preparation", "In preparation"
    PRE_APPROVAL = "pre_approval", "Pre-approval"
    APPROVED = "approved", "Approved"
    CWR = "cwr", "CWR"
    FINAL_READING = "final_reading", "Final reading"
    PAS_PUBLIC = "pas_public", "PAS public"
    PREPRINT = "preprint", "Preprint"
    SUBMITTED = "submitted", "Submitted"
    ACCEPTED = "accepted", "Accepted"
    PUBLISHED = "published", "Published"
    UNKNOWN = "unknown", "Unknown"


INTERNAL_STATUSES = {"in_preparation", "pre_approval", "approved", "cwr", "final_reading"}
# Position on the five-step track shown in lists: internal, public result, submitted, accepted, published.
TRACK_STEP = {**{s: 1 for s in INTERNAL_STATUSES}, "pas_public": 2, "preprint": 2,
              "submitted": 3, "accepted": 4, "published": 5, "unknown": 0}
TRACK_LABELS = ["Internal review", "Public result", "Submitted", "Accepted", "Published"]


TRACKED_FIELDS = ["title", "category", "doc_type", "status", "cadi", "arxiv", "doi", "cds", "report_number",
                  "inspire_recid", "url", "journal_ref", "venue", "has_other_authors", "notes"]


class Visibility(models.TextChoices):
    PUBLIC = "public", "Public"
    INTERNAL = "internal", "Internal only"
    UNVERIFIED = "unverified", "Public, no identifier yet"


REVIEW_FLAG_HELP = {
    "merged_duplicate": "Appeared more than once in the old list; merged into one record.",
    "missing_identifier": "Reported as public, but no arXiv ID, DOI, CDS record or journal reference was given.",
    "status_inferred_from_cds": "Only a CDS link was given, so this was assumed to be a public PAS.",
    "pas_number_derived": "PAS report number built from the CADI number.",
    "possibly_superseded": "May have been folded into a later result.",
    "possibly_stale": "Not updated in over a year while still before publication.",
    "working_title": "Title looks descriptive rather than the official title.",
    "incomplete_name": "A contributor's name is incomplete.",
    "name_assumed": "An initial was matched to a full name by guess.",
    "no_date": "No update date given.",
    "no_status": "No status given.",
    "inspire_mismatch": "The nightly INSPIRE check found an identifier that contradicts this entry or belongs "
                        "to another entry. The edit history says what it found.",
}


class Person(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    variants = models.JSONField(default=list, blank=True, help_text="Other spellings of the name")
    in_group = models.BooleanField(null=True, blank=True, help_text="Unknown until confirmed")
    inspire_id = models.CharField("INSPIRE ID", max_length=100, blank=True, help_text="e.g. K.Pedro.1")
    orcid = models.CharField("ORCID", max_length=19, blank=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                help_text="Login account, once this person uses the tool")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "people"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("person_detail", args=[self.slug])


class Membership(models.Model):
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="memberships")
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True, help_text="Leave empty for current members")

    class Meta:
        ordering = ["start"]


class Role(models.Model):
    code = models.SlugField(unique=True)
    label = models.CharField(max_length=100)
    order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["order", "label"]

    def __str__(self):
        return self.label


class Work(models.Model):
    code = models.CharField(max_length=10, unique=True, editable=False)
    title = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices)
    doc_type = models.CharField("type", max_length=20, choices=DocType.choices)
    status = models.CharField(max_length=20, choices=Status.choices)
    visibility = models.CharField(max_length=12, choices=Visibility.choices)

    cadi = models.CharField("CADI", max_length=20, unique=True, null=True, blank=True)
    arxiv = models.CharField("arXiv", max_length=30, blank=True)
    doi = models.CharField("DOI", max_length=200, blank=True)
    cds = models.CharField("CDS record", max_length=20, blank=True)
    report_number = models.CharField(max_length=60, blank=True)
    inspire_recid = models.CharField("INSPIRE record", max_length=20, blank=True)
    url = models.URLField(max_length=500, blank=True)
    journal_ref = models.CharField("journal reference", max_length=200, blank=True)
    venue = models.CharField(max_length=200, blank=True)

    has_other_authors = models.BooleanField(default=False, help_text="Source listed 'et al.'")
    last_update = models.DateField(null=True, blank=True)
    last_update_precision = models.CharField(max_length=5, default="day", choices=[("day", "Day"), ("month", "Month")])
    notes = models.TextField(blank=True)
    review_flags = models.JSONField(default=list, blank=True)
    source_section = models.CharField(max_length=20, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    modified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)

    class Meta:
        ordering = ["-last_update", "title"]

    def compute_visibility(self):
        """Internal while in CMS review (unless a PAS is already public); public once it has
        something anyone can look up; otherwise reported as public but not yet checkable."""
        if self.status in INTERNAL_STATUSES | {"unknown"} and not (self.status == "cwr" and self.report_number):
            return Visibility.INTERNAL
        if any([self.arxiv, self.doi, self.cds, self.report_number, self.url, self.journal_ref]):
            return Visibility.PUBLIC
        return Visibility.UNVERIFIED

    def snapshot(self):
        return {f: getattr(self, f) for f in TRACKED_FIELDS}

    def __str__(self):
        return self.cadi or self.title[:80]

    def get_absolute_url(self):
        return reverse("work_detail", args=[self.code])

    def save(self, *args, changed_by=None, change_source="manual", **kwargs):
        self.visibility = self.compute_visibility()
        if changed_by is not None:
            self.modified_by = changed_by
            if not self.pk:
                self.created_by = changed_by
        old = None
        if self.pk:
            old = Work.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if not self.code:
            last = Work.objects.order_by("-code").values_list("code", flat=True).first()
            self.code = f"W{int(last[1:]) + 1 if last else 1:04d}"
        super().save(*args, **kwargs)
        if old != self.status:
            StatusChange.objects.create(work=self, old_status=old or "", new_status=self.status,
                                        changed_by=changed_by, source=change_source)

    @property
    def track_step(self):
        return TRACK_STEP.get(self.status, 0)

    @property
    def is_internal_stage(self):
        return self.status in INTERNAL_STATUSES

    @property
    def track(self):
        return [{"label": lbl, "done": i < self.track_step} for i, lbl in enumerate(TRACK_LABELS)]

    @property
    def flag_help(self):
        return [(f, REVIEW_FLAG_HELP.get(f, f)) for f in self.review_flags]

    @property
    def primary_link(self):
        if self.doi: return f"https://doi.org/{self.doi}"
        if self.arxiv: return f"https://arxiv.org/abs/{self.arxiv}"
        if self.cds: return f"https://cds.cern.ch/record/{self.cds}"
        return self.url or ""

    @property
    def last_update_display(self):
        if not self.last_update: return ""
        fmt = "%b %Y" if self.last_update_precision == "month" else "%-d %b %Y"
        return self.last_update.strftime(fmt)


class Contribution(models.Model):
    work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="contributions")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="contributions")
    roles = models.ManyToManyField(Role, blank=True)
    membership_uncertain = models.BooleanField(default=False,
                                               help_text="Name was bracketed or incomplete in the old list")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        constraints = [models.UniqueConstraint(fields=["work", "person"], name="one_contribution_per_person")]

    def __str__(self):
        return f"{self.person} on {self.work}"


class WorkLink(models.Model):
    RELATIONS = [("to_be_combined", "To be combined with"), ("submitted_together", "Submitted together with"),
                 ("possibly_superseded_by", "Possibly superseded by"), ("superseded_by", "Superseded by")]
    from_work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="links_out")
    to_work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="links_in")
    relation = models.CharField(max_length=30, choices=RELATIONS)
    REVERSE = {"to_be_combined": "To be combined with", "submitted_together": "Submitted together with",
               "possibly_superseded_by": "May supersede", "superseded_by": "Supersedes"}

    def reverse_label(self):
        return self.REVERSE.get(self.relation, self.relation)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["from_work", "to_work", "relation"], name="unique_link")]


class StatusChange(models.Model):
    work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="status_history")
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    source = models.CharField(max_length=20, default="manual", help_text="import, manual or inspire")

    class Meta:
        ordering = ["-changed_at"]

    def new_label(self):
        return Status(self.new_status).label if self.new_status in Status.values else self.new_status


class EditLog(models.Model):
    """One line per save: who changed what. Members can edit anything, so this is the safety net."""
    work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="edit_log")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=20)       # created, edited, added_self
    changes = models.JSONField(default=list)        # [{"field": label, "old": ..., "new": ...}]

    class Meta:
        ordering = ["-at"]


def describe_changes(before, after, before_people=None, after_people=None):
    """Compare two snapshots (and contributor lists) and return readable change records."""
    out = []
    for f in TRACKED_FIELDS:
        old, new = (before or {}).get(f), after.get(f)
        if (old or "") != (new or ""):
            label = str(Work._meta.get_field(f).verbose_name)
            label = label if label[:1].isupper() or f == "arxiv" else label[:1].upper() + label[1:]
            if f == "status":
                old, new = Status(old).label if old else "", Status(new).label
            elif f in ("category", "doc_type"):
                choices = dict(Work._meta.get_field(f).choices)
                old, new = choices.get(old, old or ""), choices.get(new, new)
            out.append({"field": label, "old": old or "", "new": new or ""})
    if before_people is not None and after_people is not None and before_people != after_people:
        gone = sorted(set(before_people) - set(after_people))
        came = sorted(set(after_people) - set(before_people))
        if gone or came:
            out.append({"field": "Contributors", "old": "; ".join(gone), "new": "; ".join(came)})
    return out


def contributor_summary(work):
    """'Name (Role, Role)' strings for comparing contributor lists before and after an edit."""
    return [f"{c.person.name} ({', '.join(r.label for r in c.roles.all()) or 'Contributor'})"
            for c in work.contributions.select_related("person").prefetch_related("roles")]
