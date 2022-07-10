from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.conf import settings
import os
from infos.models import ConfigurationGraph, ConfigurationElement
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

        directory = "fakts"

        # iterate over files in
        # that directory
        files = Path(directory).glob("*.yaml")
        for file in files:
            filename = os.path.basename(file).split(".")[0]

            with open(file, "r") as file:
                graph_dict = yaml.load(file, Loader=yaml.FullLoader)

            info = graph_dict["info"]
            services = graph_dict["services"]

            graph, _ = ConfigurationGraph.objects.get_or_create(
                name=info["name"], host=info["host"]
            )

            for key, value in services.items():
                el = ConfigurationElement.objects.update_or_create(
                    name=key, graph=graph, defaults={"values": value}
                )

            print(filename)
