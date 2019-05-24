"""
WSGI config for urban_piper project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/2.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DJANGO_SETTINGS_MODULE'] = env(
    "DJANGO_SETTINGS_MODULE",
    default="config.settings.local",
)

print(env("DJANGO_SETTINGS_MODULE"), "xxxxxxxxxxxxxxxx")
application = get_wsgi_application()
