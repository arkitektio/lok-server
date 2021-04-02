from pathlib import Path
from django.conf import settings
from importlib import import_module
import logging

logger = logging.getLogger(__name__)


class AutodiscoverApps:

    def __init__(self, glob, exclude=[]) -> None:
        self.apps = settings.INSTALLED_APPS
        self.base_path = settings.BASE_DIR
        self.glob = glob
        self.app_paths = []
        

    def __normalize_module_name(self, module):
        return '.'.join(module.parts).replace('.py', '')


    def __call__(self):
        for app in self.apps:
            try:
                paths = import_module(app).__path__ # Path can be actually in multiple directors so is a list
            except AttributeError as e:
                logger.error(f"Couldn't import app {app}: {e}")
                continue # We continue here and skip the rest

            for path in paths:
                logger.debug(f"Trying to import {path}")
                p = Path(path)
                matched_files = list(p.glob(self.glob))
                for file in matched_files:
                    module_name = self.__normalize_module_name(file.relative_to(self.base_path))
                    print(module_name)
                    import_module(module_name)

            

autodiscover = AutodiscoverApps(glob="**/balder.py")
