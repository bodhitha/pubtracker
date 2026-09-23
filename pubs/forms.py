from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory
from django.urls import reverse
from django.utils.html import format_html

from .models import REVIEW_FLAG_HELP, Contribution, Person, Role, Work


class WorkForm(forms.ModelForm):
    resolved_flags = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple, label="Mark as resolved",
        help_text="Tick the review notes this edit takes care of.")

    class Meta:
        model = Work
        fields = ["title", "category", "doc_type", "status", "cadi", "arxiv", "doi", "cds", "report_number",
                  "inspire_recid", "url", "journal_ref", "venue", "has_other_authors", "notes"]
        widgets = {
            "title": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"doc_type": "Type", "cadi": "CADI number", "cds": "CDS record number",
                  "has_other_authors": "Has authors outside the group (et al.)", "url": "Other link",
                  "inspire_recid": "INSPIRE record number"}
        help_texts = {
            "cadi": "For CMS analyses, e.g. EXO-23-002. Used to match PAS, preprint and paper.",
            "arxiv": "e.g. 2403.05311", "doi": "e.g. 10.1103/PhysRevLett.133.191902",
            "report_number": "e.g. CMS-PAS-EXO-23-002 or FERMILAB-PUB-25-0611-PPD",
            "status": "Anything before PAS public stays internal to the group.",
            "notes": "Anything others should know: target journal, what's pending, links to related work.",
            "has_other_authors": "", "url": "For things without an arXiv ID or DOI, e.g. an Indico contribution.",
            "inspire_recid": "The number at the end of the paper's INSPIRE link.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cadi"].required = False
        flags = self.instance.review_flags if self.instance.pk else []
        if flags:
            self.fields["resolved_flags"].choices = [(f, REVIEW_FLAG_HELP.get(f, f)) for f in flags]
        else:
            del self.fields["resolved_flags"]

    def clean_cadi(self):
        return (self.cleaned_data.get("cadi") or "").strip().upper() or None

    def clean_arxiv(self):
        return (self.cleaned_data.get("arxiv") or "").strip().removeprefix("arXiv:").removeprefix("arxiv:")

    def clean_doi(self):
        doi = (self.cleaned_data.get("doi") or "").strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
            doi = doi.removeprefix(prefix)
        return doi

    def clean(self):
        data = super().clean()
        checks = [("cadi", data.get("cadi")), ("arxiv", data.get("arxiv")), ("doi", data.get("doi"))]
        q = Q()
        for field, value in checks:
            if value:
                q |= Q(**{f"{field}__iexact": value})
        if q:
            dupes = Work.objects.filter(q)
            if self.instance.pk:
                dupes = dupes.exclude(pk=self.instance.pk)
            dupe = dupes.first()
            if dupe:
                raise forms.ValidationError(format_html(
                    'This paper is already in the tracker as <a href="{}">{}</a>. '
                    'Open it and use "Add me to this paper" instead.',
                    reverse("work_detail", args=[dupe.code]), dupe))
        return data


class ContributionForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(queryset=Role.objects.all(), required=False,
                                           widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Contribution
        fields = ["person", "roles"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = Person.objects.exclude(in_group=False)
        self.fields["person"].empty_label = "Choose a person"


ContributionFormSet = inlineformset_factory(
    Work, Contribution, form=ContributionForm, extra=1, can_delete=True, min_num=0)
EditContributionFormSet = inlineformset_factory(
    Work, Contribution, form=ContributionForm, extra=0, can_delete=True, min_num=0)


class AddMeForm(forms.Form):
    person = forms.ModelChoiceField(queryset=Person.objects.none(), required=False, label="Which person are you?",
                                    help_text="You only need to answer this once.")
    roles = forms.ModelMultipleChoiceField(queryset=Role.objects.all(), required=False,
                                           widget=forms.CheckboxSelectMultiple, label="Your roles on this paper")

    def __init__(self, *args, needs_person=False, **kwargs):
        super().__init__(*args, **kwargs)
        if needs_person:
            self.fields["person"].queryset = Person.objects.filter(user=None).exclude(in_group=False)
            self.fields["person"].required = True
            self.fields["person"].empty_label = "Choose yourself"
        else:
            del self.fields["person"]


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["name", "in_group", "orcid", "inspire_id"]
        labels = {"in_group": "Member of the group"}
        widgets = {"in_group": forms.Select(choices=[(True, "Yes"), (False, "No, outside collaborator")])}
