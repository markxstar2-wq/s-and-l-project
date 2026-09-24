# core/routing.py
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/live-updates/$', consumers.LiveUpdateConsumer.as_asgi()),
]