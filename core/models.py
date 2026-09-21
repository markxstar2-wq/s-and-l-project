from datetime import date
import uuid
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError

from salesproject import settings

# ------------------------------------------------------------------
# 1. CUSTOM USER MODEL
# ------------------------------------------------------------------
class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Administrator'),
        ('main_store', 'Main Store Manager'),
        ('shop', 'Shop Cashier'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='shop')

    @property
    def is_warehouse_staff(self):
        return self.is_superuser or self.role in ['main_store', 'admin']

    @property
    def is_cashier(self):
        return self.is_superuser or self.role in ['shop', 'admin']


# ------------------------------------------------------------------
# 2. PRODUCT CATALOG
# ------------------------------------------------------------------
class Product(models.Model):
    product_code = models.CharField(max_length=50, unique=True)
    product_name = models.CharField(max_length=150)
    category = models.CharField(max_length=100, blank=True, null=True)
    min_stock_level = models.PositiveIntegerField(default=10)
    default_unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product_name} ({self.product_code})"

    @property
    def main_store_stock(self):
        total_in = self.stockin_set.aggregate(total=models.Sum('quantity'))['total'] or 0
        total_out = self.stockout_set.aggregate(total=models.Sum('quantity'))['total'] or 0
        return total_in - total_out

    @property
    def shop_stock(self):
        total_transferred = self.stockout_set.aggregate(total=models.Sum('quantity'))['total'] or 0
        total_sold = SaleItem.objects.filter(
            product=self, 
            sale__is_refunded=False
        ).aggregate(total=models.Sum('quantity'))['total'] or 0
        return total_transferred - total_sold

# ------------------------------------------------------------------
# 3. MAIN STORE (STOCK IN & STOCK OUT)
# ------------------------------------------------------------------
class StockIn(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"+{self.quantity} {self.product.product_name} (Factory Intake)"


class StockOut(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    def clean(self):
        if self.product_id and self.quantity > self.product.main_store_stock:
            raise ValidationError(
                f"Transfer blocked: Cannot move {self.quantity} units. Only {self.product.main_store_stock} available in Main Store."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"-{self.quantity} {self.product.product_name} (Transfer to Shop)"


# ------------------------------------------------------------------
# 4. CUSTOMER DEBT TRACKING
# ------------------------------------------------------------------
class Customer(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


# ------------------------------------------------------------------
# 5. SHOP SALES & SALE ITEMS
# ------------------------------------------------------------------
class Sale(models.Model):
    STATUS_CHOICES = (
        ('COMPLETED', 'Completed'),
        ('PENDING', 'Pending Payment'),
        ('REFUNDED', 'Refunded'),
    )
    receipt_number = models.CharField(max_length=50, unique=True, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    customer_name_raw = models.CharField(max_length=150, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    balance_due = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    payment_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='COMPLETED')
    due_date = models.DateField(null=True, blank=True)
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    is_committed = models.BooleanField(default=False)
    is_refunded = models.BooleanField(default=False)
    refunded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = f"REC-{uuid.uuid4().hex[:8].upper()}"
        
        if self.is_refunded:
            self.payment_status = 'REFUNDED'
            self.balance_due = Decimal('0.00')
        else:
            self.balance_due = max(Decimal('0.00'), self.total_amount - self.amount_paid)
            self.payment_status = 'PENDING' if self.balance_due > 0 else 'COMPLETED'
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.receipt_number} - {self.customer_name_raw or self.customer}"

    @property
    def display_customer_name(self):
        if self.customer:
            return self.customer.name
        return self.customer_name_raw or "Walk-in Customer"

    @property
    def is_overdue(self):
        if self.payment_status == 'PENDING' and self.due_date and not self.is_refunded:
            return date.today() >= self.due_date
        return False


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    unit_price_at_sale = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2, editable=False)

    def save(self, *args, **kwargs):
        self.total_price = Decimal(self.quantity) * self.unit_price_at_sale
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity}x {self.product.product_name} on {self.sale.receipt_number}"

class NotificationPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    email_notifications = models.BooleanField(default=True)
    # Add any other fields needed...

    def __str__(self):
        return f"{self.user.username}'s Preferences"

class SystemSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.key