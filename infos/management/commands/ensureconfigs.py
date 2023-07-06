from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.conf import settings
import os
from infos.models import ConfigurationGraph, ConfigurationElement, ConfigurationTemplate, Linker
from infos.forms import ConfigurationTemplateForm, FilterForm, LinkerForm
import yaml
# import required module
from pathlib import Path


# assign directory
directory = "files"

# iterate over files in
# that directory


class Command(BaseCommand):
    help = "Creates an admin user non-interactively if it doesn't exist"

    def handle(self, *args, **kwargs):

        # Ensuring templates


        # iterate over files in
        # that directory
        templates = Path(settings.GRAPH_TEMPLATES_DIR).glob("*.yaml")
        for file in templates:
            filename = os.path.basename(file).split(".")[0]

            with open(file, "r") as file:
                template = file.read()

            form = ConfigurationTemplateForm({"body": template, "name": filename})
            assert form.is_valid(), form.errors
            graph, _ = ConfigurationTemplate.objects.update_or_create(
                name=filename, defaults=dict(body=template)
            )
            print("Ensured template", filename)

        linkers = Path(settings.GRAPH_LINKERS_DIR).glob("*.yaml")
        for file in linkers:
            filename = os.path.basename(file).split(".")[0]

            with open(file, "r") as file:
                values = yaml.load(file, Loader=yaml.FullLoader)

            template = values.pop("template")
            filters = values.pop("filters")
            assert len(filters) >= 1, "At least one filter must be specified"



            try:
                x = ConfigurationTemplate.objects.get(name=template)
            except ConfigurationTemplate.DoesNotExist:
                raise Exception(f"Template {template} does not exist")
            

            link_name = values.pop("name")

            linker_form = LinkerForm({**values, "template": x})
            assert linker_form.is_valid(), linker_form.errors
            linker, _ = Linker.objects.update_or_create(
                name=link_name, defaults=linker_form.cleaned_data
            )

            linker.filters.all().delete()

            validated_filters = []

            for filter in filters:
                filter_form = FilterForm({"linker": linker, **filter })
                assert filter_form.is_valid(), filter_form.errors
                validated_filters.append(filter_form.save())

            

