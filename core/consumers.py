import json
from channels.generic.websocket import AsyncWebsocketConsumer

class LiveUpdateConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Channel groups to subscribe to
        self.groups_to_join = ['dashboard', 'history', 'reports', 'notifications', 'live_updates']

        for group in self.groups_to_join:
            await self.channel_layer.group_add(group, self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        for group in self.groups_to_join:
            await self.channel_layer.group_discard(group, self.channel_name)

    # Handler for 'type': 'dashboard_update'
    async def dashboard_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'dashboard_update',
            'data': event.get('data', {})
        }))

    # Handler for 'type': 'history_update'
    async def history_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'history_update',
            'data': event.get('data', {})
        }))

    # Handler for 'type': 'reports_update'
    async def reports_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'reports_update',
            'data': event.get('data', {})
        }))

    # Handler for 'type': 'send_notification'
    async def send_notification(self, event):
        await self.send(text_data=json.dumps({
            'type': 'notification',
            'message': event.get('message', ''),
            'level': event.get('level', 'info')
        }))

    # Handler for 'type': 'broadcast_notification'
    async def broadcast_notification(self, event):
        await self.send(text_data=json.dumps({
            'type': 'notification',
            'data': event.get('notification', {})
        }))