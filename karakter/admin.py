from django.contrib import admin

from karakter import models

# Register your models here.
admin.site.register(models.User)
admin.site.register(models.Profile)
admin.site.register(models.ComChannel)
admin.site.register(models.GroupProfile)
admin.site.register(models.SystemMessage)
