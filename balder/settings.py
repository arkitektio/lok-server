from django.conf import settings
from pydantic import BaseModel


class BalderSettings(BaseModel):
    SUBSCRIPTIONS: bool


def parse_settings():

   settings_dict = settings.BALDER
   return BalderSettings(**settings_dict)


BALDER_SETTINGS = None


def get_active_settings() -> BalderSettings:
    global BALDER_SETTINGS
    if BALDER_SETTINGS is None:
        BALDER_SETTINGS = parse_settings()
    return BALDER_SETTINGS