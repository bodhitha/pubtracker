from datetime import date

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Case, Count, F, Prefetch, Q, When
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from . import exports
from .models import Category, Contribution, Person, Role, Status, Visibility, Work

SORTS = {"updated": ["-last_update", "title"], "title": ["title"], "cadi": ["cadi", "title"]}


def site_context(request):
    return {"site_title": settings.SITE_TITLE, "auth_mode": settings.AUTH_MODE}


def _with_contributors(qs):
    return qs.prefetch_related(Prefetch(
        "contributions", queryset=Contribution.objects.select_related("person").prefetch_related("roles")))


def _counted(qs, field, choices):
    base = Work.objects.filter(pk__in=qs.values("pk")).order_by()  # no ordering, no join duplicates
    counts = dict(base.values_list(field).annotate(n=Count("id")))
    return [(value, label, counts.get(value, 0)) for value, label in choices]


def _search(g):
    """Works matching everything chosen on the list page except the category/status/visibility facets."""
    works = Work.objects.all()
    q = g.get("q", "").strip()
    if q:
        works = works.filter(Q(title__icontains=q) | Q(cadi__icontains=q) | Q(arxiv__icontains=q) |
                             Q(doi__icontains=q) | Q(report_number__icontains=q) | Q(notes__icontains=q) |
                             Q(contributions__person__name__icontains=q)).distinct()
    if g.get("person"):
        works = works.filter(contributions__person__slug=g["person"])
    if g.get("role"):
        works = works.filter(contributions__roles__code=g["role"])
        if g.get("person"):  # role must belong to that person
            works = works.filter(contributions__person__slug=g["person"], contributions__roles__code=g["role"])
    if g.get("year"):
        works = works.filter(last_update__year=g["year"])
    if g.get("review") == "1":
        works = works.exclude(review_flags=[])
    return works.distinct()


def _with_facets(works, g):
    for f in ("category", "status", "visibility"):
        if g.get(f):
            works = works.filter(**{f: g[f]})
    return works


def _sort(g):
    return g.get("sort") if g.get("sort") in SORTS else "updated"


def work_list(request):
    g = request.GET
    q = g.get("q", "").strip()
    works = _search(g)

    # facet counts reflect everything chosen except the facet itself
    facets = {}
    for field, choices in [("category", Category.choices), ("status", Status.choices),
                           ("visibility", Visibility.choices)]:
        others = works
        for f2 in ("category", "status", "visibility"):
            if f2 != field and g.get(f2):
                others = others.filter(**{f2: g[f2]})
        facets[field] = _counted(others, field, choices)
    works = _with_facets(works, g)

    sort = _sort(g)
    page = Paginator(_with_contributors(works.order_by(*SORTS[sort])), 50).get_page(g.get("page"))
    years = Work.objects.exclude(last_update=None).dates("last_update", "year", order="DESC")
    params = g.copy()
    params.pop("page", None)
    active = any(g.get(k) for k in ("q", "category", "status", "visibility", "person", "role", "year", "review"))
    return render(request, "pubs/work_list.html", {
        "page": page, "facets": facets, "q": q, "sort": sort, "years": [d.year for d in years],
        "people": Person.objects.filter(contributions__isnull=False).distinct(),
        "roles": Role.objects.all(), "g": g, "querystring": params.urlencode(), "filters_active": active,
        "total": Work.objects.count(),
        "export_formats": [("csv", "CSV", "Spreadsheet, all details"), ("txt", "Plain text", "Numbered list for reports"),
                           ("tex", "LaTeX", "List to \\input into a CV"), ("bib", "BibTeX", "Citations, from INSPIRE")],
    })


def work_export(request, fmt):
    """The works currently shown on the list page (same search, filters and order) as a file."""
    if fmt not in exports.FORMATS:
        raise Http404
    g = request.GET
    works = list(_with_contributors(_with_facets(_search(g), g).order_by(*SORTS[_sort(g)])))
    params = g.copy()
    params.pop("page", None)
    info = exports.ExportInfo(
        site_title=settings.SITE_TITLE,
        source_url=request.build_absolute_uri(reverse("work_list")) + (f"?{params.urlencode()}" if params else ""),
        link=lambda w: request.build_absolute_uri(w.get_absolute_url()),
        person=Person.objects.filter(slug=g["person"]).first() if g.get("person") else None)
    writer, content_type = exports.FORMATS[fmt]
    response = HttpResponse(writer(works, info), content_type=f"{content_type}; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="publications-{date.today():%Y-%m-%d}.{fmt}"'
    return response


def work_detail(request, code):
    work = get_object_or_404(_with_contributors(Work.objects.all()), code=code)
    links = [(l.get_relation_display(), l.to_work) for l in work.links_out.select_related("to_work")]
    links += [(l.reverse_label(), l.from_work) for l in work.links_in.select_related("from_work")]
    me = getattr(request.user, "person", None)
    return render(request, "pubs/work_detail.html", {
        "work": work, "links": links, "history": work.status_history.select_related("changed_by"),
        "edits": work.edit_log.select_related("user")[:30],
        "i_am_on_it": bool(me and work.contributions.filter(person=me).exists())})


# (column key, label, field it sorts by, direction of the first click: "" ascending, "-" descending)
PERSON_COLUMNS = [("name", "Name", "name", ""), ("group", "In group", "group_rank", ""),
                  ("inspire", "INSPIRE ID", "inspire_id", ""), ("entries", "Entries", "n", "-")]
PERSON_DEFAULT_SORT = "-entries"


def person_list(request):
    people = Person.objects.annotate(
        n=Count("contributions"),
        group_rank=Case(When(in_group=True, then=0), When(in_group=False, then=1), default=2),
        no_inspire_id=Case(When(inspire_id="", then=1), default=0))
    if request.GET.get("group") == "unconfirmed":
        people = people.filter(in_group=None)

    fields = {key: field for key, _, field, _ in PERSON_COLUMNS}
    sort = request.GET.get("sort", PERSON_DEFAULT_SORT)
    if sort.lstrip("-") not in fields:
        sort = PERSON_DEFAULT_SORT
    key, descending = sort.lstrip("-"), sort.startswith("-")
    field = F(fields[key]).desc() if descending else F(fields[key]).asc()
    # people without an INSPIRE ID stay at the bottom whichever way the column is sorted
    people = people.order_by(*(["no_inspire_id"] if key == "inspire" else []), field, "name")

    columns = [{"label": label, "numeric": k == "entries",
                "aria_sort": ("descending" if descending else "ascending") if k == key else "",
                "next": (k if descending else f"-{k}") if k == key else f"{first}{k}"}
               for k, label, _, first in PERSON_COLUMNS]
    return render(request, "pubs/person_list.html",
                  {"people": people, "group": request.GET.get("group", ""), "columns": columns})


def person_detail(request, slug):
    person = get_object_or_404(Person, slug=slug)
    contribs = (person.contributions.select_related("work").prefetch_related("roles")
                .order_by("-work__last_update"))
    role_counts = (Role.objects.filter(contribution__person=person)
                   .annotate(n=Count("contribution")).order_by("-n"))
    return render(request, "pubs/person_detail.html",
                  {"person": person, "contribs": contribs, "role_counts": role_counts})


@login_not_required
def healthz(request):
    connection.ensure_connection()
    return HttpResponse("ok", content_type="text/plain")


# ---- stage 2: members add and edit entries --------------------------------------------------
from django.contrib import messages
from django.db import transaction
from django.db.models import Q as _Q
from django.shortcuts import redirect
from django.utils.text import slugify

from . import inspire
from .forms import AddMeForm, ContributionFormSet, EditContributionFormSet, PersonForm, WorkForm
from .models import EditLog, contributor_summary, describe_changes


def _existing_for(kind, value, fields):
    """An entry already in the tracker for this CADI number, arXiv ID or DOI, if any."""
    q = _Q(**{f"{kind}__iexact": value})
    for f in ("cadi", "arxiv", "doi"):
        if fields and fields.get(f):
            q |= _Q(**{f"{f}__iexact": fields[f]})
    return Work.objects.filter(q).first()


def _save_work(request, form, formset, action):
    before = form.initial_snapshot
    before_people = form.initial_people
    with transaction.atomic():
        work = form.save(commit=False)
        resolved = set(form.cleaned_data.get("resolved_flags") or [])
        work.review_flags = [f for f in work.review_flags if f not in resolved]
        work.last_update = date.today()
        work.last_update_precision = "day"
        work.save(changed_by=request.user, change_source="manual")
        formset.instance = work
        formset.save()
        changes = describe_changes(before, work.snapshot(), before_people, contributor_summary(work))
        if resolved:
            changes.append({"field": "Review notes", "old": f"{len(resolved)} resolved", "new": ""})
        if changes or action == "created":
            EditLog.objects.create(work=work, user=request.user, action=action, changes=changes)
    return work


def work_new(request):
    initial, lookup_msg, existing, lookup_text = {}, None, None, request.GET.get("lookup", "").strip()
    if lookup_text and request.method == "GET":
        try:
            kind, value, fields = inspire.lookup(lookup_text)
            existing = _existing_for(kind, value, fields)
            if existing:
                lookup_msg = ("exists", None)
            elif fields:
                initial = fields
                lookup_msg = ("found", "Filled in from INSPIRE. Check everything, then add the group contributors.")
            else:
                initial = {kind: value}
                lookup_msg = ("missing", "INSPIRE has no record for this yet (PAS-only results usually aren't "
                                         "on INSPIRE). Fill in the details below.")
        except inspire.NotRecognized as e:
            lookup_msg = ("error", str(e))
        except inspire.InspireUnavailable:
            try:
                kind, value = inspire.classify(lookup_text)
                initial = {kind: value}
                existing = _existing_for(kind, value, None)
            except inspire.NotRecognized:
                pass
            lookup_msg = ("exists", None) if existing else (
                "offline", "Couldn't reach INSPIRE, so nothing was filled in. Enter the details below.")

    if request.method == "POST":
        form = WorkForm(request.POST)
        formset = ContributionFormSet(request.POST, instance=Work())
        form.initial_snapshot, form.initial_people = None, []
        if form.is_valid() and formset.is_valid():
            work = _save_work(request, form, formset, "created")
            messages.success(request, "Paper added.")
            return redirect(work.get_absolute_url())
    else:
        me = getattr(request.user, "person", None)
        form = WorkForm(initial=initial)
        formset = ContributionFormSet(instance=Work(), initial=[{"person": me.pk}] if me else None)
    return render(request, "pubs/work_form.html", {
        "form": form, "formset": formset, "is_new": True, "lookup_text": lookup_text,
        "lookup_msg": lookup_msg, "existing": existing})


def work_edit(request, code):
    work = get_object_or_404(Work, code=code)
    if request.method == "POST":
        form = WorkForm(request.POST, instance=work)
        form.initial_snapshot = Work.objects.get(pk=work.pk).snapshot()
        form.initial_people = contributor_summary(work)
        formset = EditContributionFormSet(request.POST, instance=work)
        if form.is_valid() and formset.is_valid():
            _save_work(request, form, formset, "edited")
            messages.success(request, "Changes saved.")
            return redirect(work.get_absolute_url())
    else:
        form = WorkForm(instance=work)
        formset = (EditContributionFormSet if work.contributions.exists() else ContributionFormSet)(instance=work)
    return render(request, "pubs/work_form.html", {"form": form, "formset": formset, "work": work, "is_new": False})


def work_add_me(request, code):
    work = get_object_or_404(Work, code=code)
    me = getattr(request.user, "person", None)
    existing = work.contributions.filter(person=me).first() if me else None
    if request.method == "POST":
        form = AddMeForm(request.POST, needs_person=me is None)
        if form.is_valid():
            with transaction.atomic():
                if me is None:
                    me = form.cleaned_data["person"]
                    me.user = request.user
                    me.save()
                before = contributor_summary(work)
                contrib, _ = work.contributions.get_or_create(
                    person=me, defaults={"order": work.contributions.count()})
                contrib.roles.set(form.cleaned_data["roles"])
                work.last_update = date.today()
                work.save(changed_by=request.user)
                changes = describe_changes(work.snapshot(), work.snapshot(), before, contributor_summary(work))
                EditLog.objects.create(work=work, user=request.user, action="added_self", changes=changes)
            messages.success(request, "Your roles are saved." if existing else "You're added to this paper.")
            return redirect(work.get_absolute_url())
    else:
        form = AddMeForm(needs_person=me is None,
                         initial={"roles": existing.roles.all() if existing else []})
    return render(request, "pubs/add_me.html", {"work": work, "form": form, "me": me, "existing": existing})


def person_new(request):
    if request.method == "POST":
        form = PersonForm(request.POST)
        if form.is_valid():
            person = form.save(commit=False)
            base = slugify(person.name) or "person"
            slug, n = base, 2
            while Person.objects.filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            person.slug = slug
            person.save()
            messages.success(request, f"{person.name} added. You can now choose them as a contributor.")
            return redirect(request.GET.get("next") or person.get_absolute_url())
    else:
        form = PersonForm(initial={"in_group": True})
    return render(request, "pubs/person_form.html", {"form": form})
