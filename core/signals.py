from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import StockIn, StockOut, Sale, SaleItem, Product

def broadcast_live_updates(event_type, target_group, extra_data=None):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    payload = {
        "event": event_type,
    }
    if extra_data:
        payload.update(extra_data)

    async_to_sync(channel_layer.group_send)(
        target_group,
        {
            "type": f"{target_group}_update",
            "data": payload
        }
    )

# ------------------------------------------------------------------
# STOCK IN & STOCK OUT SIGNALS
# ------------------------------------------------------------------
@receiver(post_save, sender=StockIn)
@receiver(post_delete, sender=StockIn)
def on_stock_in_change(sender, instance, **kwargs):
    product = instance.product
    data = {
        "product_id": product.id,
        "product_code": product.product_code,
        "product_name": product.product_name,
        "main_store_stock": product.main_store_stock,
        "shop_stock": product.shop_stock,
    }
    broadcast_live_updates("STOCK_IN_CHANGED", "dashboard", data)
    broadcast_live_updates("STOCK_IN_CHANGED", "reports", data)
    broadcast_live_updates("STOCK_IN_CHANGED", "history", data)

@receiver(post_save, sender=StockOut)
@receiver(post_delete, sender=StockOut)
def on_stock_out_change(sender, instance, **kwargs):
    product = instance.product
    data = {
        "product_id": product.id,
        "product_code": product.product_code,
        "product_name": product.product_name,
        "main_store_stock": product.main_store_stock,
        "shop_stock": product.shop_stock,
    }
    broadcast_live_updates("STOCK_OUT_CHANGED", "dashboard", data)
    broadcast_live_updates("STOCK_OUT_CHANGED", "reports", data)
    broadcast_live_updates("STOCK_OUT_CHANGED", "history", data)

# ------------------------------------------------------------------
# SALE & SALE ITEM SIGNALS
# ------------------------------------------------------------------
@receiver(post_save, sender=Sale)
@receiver(post_delete, sender=Sale)
def on_sale_change(sender, instance, **kwargs):
    data = {
        "sale_id": instance.id,
        "receipt_number": instance.receipt_number,
        "customer": instance.display_customer_name,
        "total_amount": float(instance.total_amount),
        "amount_paid": float(instance.amount_paid),
        "balance_due": float(instance.balance_due),
        "payment_status": instance.payment_status,
        "is_refunded": instance.is_refunded,
    }
    broadcast_live_updates("SALE_CHANGED", "dashboard", data)
    broadcast_live_updates("SALE_CHANGED", "reports", data)
    broadcast_live_updates("SALE_CHANGED", "history", data)

@receiver(post_save, sender=SaleItem)
@receiver(post_delete, sender=SaleItem)
def on_sale_item_change(sender, instance, **kwargs):
    product = instance.product
    data = {
        "product_id": product.id,
        "product_code": product.product_code,
        "product_name": product.product_name,
        "main_store_stock": product.main_store_stock,
        "shop_stock": product.shop_stock,
    }
    broadcast_live_updates("SALE_ITEM_CHANGED", "dashboard", data)
    broadcast_live_updates("SALE_ITEM_CHANGED", "reports", data)

def send_system_notification(title, message, level="info"):
    """
    Sends a real-time notification payload to all connected clients.
    level options: 'info', 'success', 'warning', 'danger'
    """
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "live_updates",  # Matches your WebSocket group name
        {
            "type": "broadcast_notification",
            "notification": {
                "title": title,
                "message": message,
                "level": level,
                "timestamp": "Just now"
            }
        }
    )