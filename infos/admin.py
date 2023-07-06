from django.contrib import admin
from .models import *

# Register your models here.
admin.site.register(App)
admin.site.register(Release)
admin.site.register(ConfigurationGraph)
admin.site.register(ConfigurationElement)
admin.site.register(ConfigurationTemplate)
admin.site.register(Linker)
admin.site.register(Filter)
admin.site.register(Member)
admin.site.register(Client)
admin.site.register(DeviceCode)
