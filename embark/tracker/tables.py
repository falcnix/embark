import django_tables2 as tables

from django.utils.html import format_html
from django.urls import reverse

from uploader.models import Device


class SimpleDeviceTable(tables.Table):

    class Meta:
        model = Device
        orderable = True

    def render_id(self, value):
        return format_html(f"<a href=\"{reverse(viewname='embark-tracker-device', args=[value])}\">{value}</a>")
