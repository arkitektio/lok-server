from django.conf.urls.static import static
from django.urls import include, path
from django.conf import settings



def xpath(string, *args, **kwargs):
    return path(string, *args, **kwargs)

def xstatic(string, *args, **kwargs):
    return static(string, *args, **kwargs)