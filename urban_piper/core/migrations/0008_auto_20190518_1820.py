# Generated by Django 2.2.1 on 2019-05-18 18:20

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_auto_20190518_1819'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='deliverytaskstate',
            unique_together={('state', 'task')},
        ),
    ]