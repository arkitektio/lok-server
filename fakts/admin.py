from django.contrib import admin
from fakts import models

# Register your models here.
admin.site.register(models.App)
admin.site.register(models.Release)
admin.site.register(models.Service)
admin.site.register(models.ServiceInstance)
admin.site.register(models.Client)
admin.site.register(models.DeviceCode)
admin.site.register(models.ServiceInstanceMapping)
admin.site.register(models.Mandate)
admin.site.register(models.InstanceAlias)
