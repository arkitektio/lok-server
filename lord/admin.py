# Register your models here.
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import *

# Define a new User admin
class UserAdmin(BaseUserAdmin):
    pass


admin.site.register(HerreUser, UserAdmin)
admin.site.register(GroupProfile)
admin.site.register(Profile)
