# Generated by Django 2.2.1 on 2019-05-23 05:15

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0004_auto_20190522_1850'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='deliverystatetransition',
            unique_together={('task', 'state', 'by', 'at')},
        ),
    ]
