from django.contrib import admin
from .models import (Contribution, EditLog, Membership, Person, Role, StatusChange, Work, WorkLink,
                     contributor_summary, describe_changes)


class ContributionInline(admin.TabularInline):
    model = Contribution
    extra = 1
    autocomplete_fields = ["person"]
    filter_horizontal = ["roles"]


class LinkInline(admin.TabularInline):
    model = WorkLink
    fk_name = "from_work"
    extra = 0
    autocomplete_fields = ["to_work"]


class StatusChangeInline(admin.TabularInline):
    model = StatusChange
    extra = 0
    can_delete = False
    readonly_fields = ["old_status", "new_status", "changed_at", "changed_by", "source"]


@admin.register(Work)
class WorkAdmin(admin.ModelAdmin):
    list_display = ["code", "cadi", "title", "category", "status", "visibility", "last_update"]
    list_filter = ["category", "status", "visibility", "doc_type"]
    search_fields = ["title", "cadi", "arxiv", "doi", "report_number"]
    readonly_fields = ["code", "visibility", "created", "modified"]
    inlines = [ContributionInline, LinkInline, StatusChangeInline]
    fieldsets = [
        (None, {"fields": ["code", "title", "category", "doc_type", "status", "visibility"]}),
        ("Identifiers", {"fields": ["cadi", "arxiv", "doi", "cds", "report_number", "inspire_recid", "url"]}),
        ("Publication", {"fields": ["journal_ref", "venue", "has_other_authors"]}),
        ("Tracking", {"fields": ["last_update", "last_update_precision", "notes", "review_flags",
                                 "source_section", "created", "modified"]}),
    ]

    def save_model(self, request, obj, form, change):
        request._before = (Work.objects.get(pk=obj.pk).snapshot(), contributor_summary(obj)) if change else (None, [])
        obj.save(changed_by=request.user, change_source="manual")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        before, before_people = getattr(request, "_before", (None, []))
        changes = describe_changes(before, form.instance.snapshot(), before_people, contributor_summary(form.instance))
        if changes or not change:
            EditLog.objects.create(work=form.instance, user=request.user,
                                   action="edited" if change else "created", changes=changes)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 1


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["name", "in_group", "inspire_id", "orcid"]
    list_filter = ["in_group"]
    search_fields = ["name", "inspire_id", "orcid"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [MembershipInline]


admin.site.register(Role, list_display=["label", "code", "order"])
