# core/utils.py

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

def broadcast_live_update(group_name, event_type, data):
    """
    Helper function to trigger WebSocket broadcasts.
    Examples:
    broadcast_live_update('dashboard', 'dashboard_update', {'total_sales': 1500, ...})
    broadcast_live_update('notifications', 'send_notification', {'message': 'Stock out in main store!'})
    """
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            'type': event_type,
            'data': data if event_type != 'send_notification' else None,
            'message': data.get('message') if isinstance(data, dict) else data,
            'level': data.get('level', 'info') if isinstance(data, dict) else 'info'
        }
    )