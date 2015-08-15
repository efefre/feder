# -*- coding: utf-8 -*-
from django import forms
from .models import Institution
from atom.forms import SingleButtonMixin
from braces.forms import UserKwargModelFormMixin


class InstitutionForm(SingleButtonMixin, UserKwargModelFormMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(InstitutionForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Institution
        fields = ['name', 'address']