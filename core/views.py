from datetime import timedelta
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField
from django.utils import timezone

from .models import (
    Product, StockIn, StockOut, Sale, SaleItem,
    Customer, NotificationPreference, SystemSetting,
)
from .forms import StockInForm, StockOutForm
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

User = get_user_model()


# ------------------------------------------------------------------
# ACCESS CONTROL HELPERS
# ------------------------------------------------------------------
def is_admin(user):
    return user.is_authenticated and (
        user.is_superuser or getattr(user, 'role', '').lower() == 'admin'
    )

def warehouse_required(user):
    if not user.is_authenticated:
        return False
    role = getattr(user, 'role', '').lower()
    return (
        getattr(user, 'is_warehouse_staff', False) or 
        role in ['main_store', 'admin'] or 
        user.is_superuser
    )

def cashier_required(user):
    if not user.is_authenticated:
        return False
    role = getattr(user, 'role', '').lower()
    return (
        getattr(user, 'is_cashier', False) or 
        role in ['shop', 'admin'] or 
        user.is_superuser
    )


# ------------------------------------------------------------------
# 1. DASHBOARD VIEW
# ------------------------------------------------------------------
@login_required
def dashboard_view(request):
    products = Product.objects.all().order_by('product_name')
    
    total_main_store_stock = sum(p.main_store_stock for p in products)
    total_shop_stock = sum(p.shop_stock for p in products)
    
    total_sales_amount = Sale.objects.filter(is_refunded=False).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    total_stock_sold = SaleItem.objects.filter(sale__is_refunded=False).aggregate(total=Sum('quantity'))['total'] or 0
    
    pending_sales = Sale.objects.filter(balance_due__gt=0, is_refunded=False).order_by('due_date')
    total_pending_credit = pending_sales.aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')
    overdue_count = pending_sales.filter(due_date__lt=timezone.now().date()).count()

    context = {
        'products': products,
        'total_main_store_stock': total_main_store_stock,
        'total_shop_stock': total_shop_stock,
        'total_sales_amount': total_sales_amount,
        'total_stock_sold': total_stock_sold,
        'total_pending_credit': total_pending_credit,
        'overdue_count': overdue_count,
        'pending_sales': pending_sales[:10],
    }
    return render(request, 'core/dashboard.html', context)


# ------------------------------------------------------------------
# 2. MAIN STORE VIEW (Warehouse Staff)
# ------------------------------------------------------------------
@user_passes_test(warehouse_required)
def main_store_view(request):
    stock_in_form = StockInForm(request.POST or None, prefix="stock_in")
    stock_out_form = StockOutForm(request.POST or None, prefix="stock_out")

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'stock_in' and stock_in_form.is_valid():
            with transaction.atomic():
                stock_in = stock_in_form.save(commit=False)
                stock_in.created_by = request.user
                stock_in.save()

            messages.success(request, f"Intake recorded: +{stock_in.quantity} units of {stock_in.product.product_name}.")
            return redirect('main_store')

        elif action == 'stock_out' and stock_out_form.is_valid():
            try:
                with transaction.atomic():
                    stock_out = stock_out_form.save(commit=False)
                    product = Product.objects.select_for_update().get(id=stock_out.product_id)

                    if product.main_store_stock < stock_out.quantity:
                        messages.error(request, f"Insufficient stock in Main Store! Only {product.main_store_stock} units available.")
                        return redirect('main_store')

                    stock_out.created_by = request.user
                    stock_out.save()

                    # Define the WebSocket broadcast function
                    def send_broadcast():
                        channel_layer = get_channel_layer()
                        async_to_sync(channel_layer.group_send)(
                            "stock_notifications",
                            {
                                "type": "stock_out_notification",
                                "product_name": product.product_name,
                                "quantity": stock_out.quantity,
                                "released_by": request.user.username,
                                "destination": "Shop",
                                "timestamp": timezone.now().strftime("%H:%M:%S")
                            }
                        )

                    # Trigger broadcast after transaction commits successfully
                    transaction.on_commit(send_broadcast)

                messages.success(request, f"Transfer recorded: -{stock_out.quantity} units of {product.product_name} transferred to Shop.")
                return redirect('main_store')
            except ValidationError as e:
                messages.error(request, getattr(e, 'message', "Validation error during transfer."))

    products = Product.objects.all().order_by('product_name')

    return render(request, 'core/mainstore.html', {
        'products': products,
        'stock_in_form': stock_in_form,
        'stock_out_form': stock_out_form,
    })
# ------------------------------------------------------------------
# 3. SHOP POS VIEW (Cashier)
# ------------------------------------------------------------------
@user_passes_test(cashier_required)
def shop_pos_view(request):
    if request.method == 'POST':
        product_id = request.POST.get('product')
        quantity = int(request.POST.get('quantity', 0))
        unit_price = Decimal(request.POST.get('unit_price', '0.00'))
        amount_paid = Decimal(request.POST.get('amount_paid', '0.00'))
        customer_name = request.POST.get('customer_name_raw')
        due_date = request.POST.get('due_date') or None

        with transaction.atomic():
            product = get_object_or_404(Product.objects.select_for_update(), id=product_id)

            if quantity > product.shop_stock:
                messages.error(request, f"Over-sales blocked! Only {product.shop_stock} units available in Shop.")
                return redirect('shop_pos')

            sale = Sale.objects.create(
                customer_name_raw=customer_name,
                total_amount=unit_price * quantity,
                amount_paid=amount_paid,
                due_date=due_date,
                cashier=request.user,
                is_committed=False,
            )
            SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=quantity,
                unit_price_at_sale=unit_price
            )

        messages.success(request, f"Sale completed! Receipt #{sale.receipt_number} generated.")
        return redirect('shop_pos')

    products = Product.objects.all()
    return render(request, 'core/shoppos.html', {'products': products})


# ------------------------------------------------------------------
# 4. HISTORY & REFUNDS
# ------------------------------------------------------------------
@login_required
def history_view(request):
    stock_in_history = StockIn.objects.select_related('product', 'created_by').order_by('-created_at')
    stock_out_history = StockOut.objects.select_related('product', 'created_by').order_by('-created_at')
    sales_history = Sale.objects.select_related('customer', 'cashier').prefetch_related('items__product').order_by('-created_at')

    return render(request, 'core/history.html', {
        'stock_in_history': stock_in_history,
        'stock_out_history': stock_out_history,
        'sales_history': sales_history,
    })


@login_required
def process_refund(request, sale_id):
    if request.method == 'POST':
        sale = get_object_or_404(Sale, id=sale_id)
        
        if sale.is_refunded:
            messages.warning(request, "This sale has already been refunded.")
            return redirect('history')
            
        with transaction.atomic():
            sale.is_refunded = True
            sale.refunded_at = timezone.now()
            sale.save()
            # Setting is_refunded = True automatically adds item quantities back to product.shop_stock!

        messages.success(request, f"Sale #{sale.receipt_number} refunded successfully. Items restored to Shop Stock.")
    return redirect('history')

@login_required
def record_payment_view(request, sale_id):
    if request.method == 'POST':
        sale = get_object_or_404(Sale, id=sale_id)
        payment_amount = Decimal(request.POST.get('payment_amount', '0.00'))

        if payment_amount <= 0:
            messages.error(request, "Please enter a valid payment amount.")
            return redirect('dashboard')

        sale.amount_paid += payment_amount
        sale.save()

        display_name = getattr(sale, 'display_customer_name', sale.customer_name_raw or "Walk-in")

        if getattr(sale, 'payment_status', None) == 'COMPLETED':
            messages.success(request, f"Debt cleared for {display_name}! Receipt #{sale.receipt_number} is now fully paid.")
        else:
            messages.info(request, f"Payment of UGX {payment_amount:,.2f} recorded. Remaining balance: UGX {sale.balance_due:,.2f}.")

    return redirect('dashboard')


# ------------------------------------------------------------------
# 5. EOD BATCH CLOSURE
# ------------------------------------------------------------------
@user_passes_test(cashier_required)
def close_shop_batch(request):
    if request.method == 'POST':
        uncommitted_sales = Sale.objects.filter(is_committed=False)
        count = uncommitted_sales.count()
        uncommitted_sales.update(is_committed=True)
        messages.success(request, f"Successfully committed {count} sales for today's batch closure.")
    return redirect('dashboard')


# ------------------------------------------------------------------
# 6. REPORTS VIEW
# ------------------------------------------------------------------
@login_required
def reports_view(request):
    period = request.GET.get('period', 'day')
    now = timezone.now()

    if period == 'day':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start_date = now - timedelta(days=7)
    elif period == 'month':
        start_date = now - timedelta(days=30)
    elif period == '6months':
        start_date = now - timedelta(days=180)
    elif period == 'year':
        start_date = now - timedelta(days=365)
    else:
        start_date = None

    sales_qs = Sale.objects.filter(is_refunded=False)
    if start_date:
        sales_qs = sales_qs.filter(created_at__gte=start_date)

    total_revenue = sales_qs.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    total_collected = sales_qs.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    total_pending_debt = sales_qs.aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')
    total_sales_count = sales_qs.count()

    top_customers = (
        sales_qs.exclude(customer=None)
        .values('customer__name', 'customer__phone')
        .annotate(
            total_spent=Sum('total_amount'),
            total_orders=Count('id')
        )
        .order_by('-total_spent')[:10]
    )

    top_products = (
        SaleItem.objects.filter(sale__in=sales_qs)
        .values('product__product_name', 'product__product_code')
        .annotate(
            total_qty=Sum('quantity'),
            total_sales_val=Sum(
                ExpressionWrapper(F('quantity') * F('unit_price_at_sale'), output_field=DecimalField())
            )
        )
        .order_by('-total_qty')[:10]
    )

    return render(request, 'core/reports.html', {
        'period': period,
        'total_revenue': total_revenue,
        'total_collected': total_collected,
        'total_pending_debt': total_pending_debt,
        'total_sales_count': total_sales_count,
        'top_customers': top_customers,
        'top_products': top_products,
    })


# ------------------------------------------------------------------
# 7. SETTINGS & MANAGEMENT VIEWS
# ------------------------------------------------------------------
@login_required
@user_passes_test(is_admin)
def settings_view(request):
    products = Product.objects.all().order_by('product_name')
    site_users = User.objects.all().order_by('username')
    settings = SystemSetting.objects.first()

    context = {
        'products': products,
        'site_users': site_users,
        'settings': settings,
    }
    return render(request, 'core/settings.html', context)


@login_required
@user_passes_test(is_admin)
def add_product(request):
    if request.method == 'POST':
        product_code = request.POST.get('product_code', '').strip()
        product_name = request.POST.get('product_name', '').strip()
        category = request.POST.get('category', '').strip()
        min_stock_level = request.POST.get('min_stock_level') or 10
        unit_price = request.POST.get('default_unit_price') or 0.00

        if not product_code or not product_name:
            messages.error(request, "Product code and product name are required.")
            return redirect('settings')

        try:
            Product.objects.create(
                product_code=product_code,
                product_name=product_name,
                category=category,
                min_stock_level=int(min_stock_level),
                default_unit_price=unit_price,
                main_store_stock=0,
                shop_stock=0
            )
            messages.success(request, f"Product '{product_name}' ({product_code}) added successfully!")
        except IntegrityError:
            messages.error(request, f"Product code '{product_code}' already exists.")

    return redirect('settings')


@login_required
@user_passes_test(is_admin)
def add_user(request):
    if request.method == "POST":
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password')
        role = request.POST.get('role', 'shop')

        if not username or not password:
            messages.error(request, "Username and password are required.")
            return redirect('settings')

        if User.objects.filter(username=username).exists():
            messages.error(request, f"Username '{username}' is already taken.")
            return redirect('settings')

        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password
            )
            
            if hasattr(user, 'role'):
                user.role = role
            
            if role == 'admin':
                user.is_staff = True
            
            user.save()

            messages.success(request, f"User account '{username}' successfully created!")
        except Exception as e:
            messages.error(request, f"Failed to create user: {str(e)}")

    return redirect('settings')


@login_required
@user_passes_test(is_admin)
def update_user_role(request, user_id):
    user_item = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        new_role = request.POST.get('role', '').lower()

        if new_role in ['shop', 'main_store', 'admin']:
            user_item.role = new_role
            user_item.is_staff = (new_role == 'admin')
            user_item.save()
            
            display_role = user_item.get_role_display() if hasattr(user_item, 'get_role_display') else new_role
            messages.success(request, f"Role for user '{user_item.username}' updated to {display_role}.")
        else:
            messages.error(request, "Invalid role selected.")

    return redirect('settings')


@login_required
@user_passes_test(is_admin)
def save_notification_settings(request):
    if request.method == 'POST':
        notify_shop = 'notify_shop_on_main_stockout' in request.POST
        notify_main = 'notify_main_on_shop_low' in request.POST
        enable_debt = 'enable_overdue_debt_alerts' in request.POST

        pref, _ = NotificationPreference.objects.get_or_create(id=1)
        pref.notify_shop_on_main_stockout = notify_shop
        pref.notify_main_on_shop_low = notify_main
        pref.enable_overdue_debt_alerts = enable_debt
        pref.save()

        messages.success(request, "Notification preferences saved successfully!")

    return redirect('settings')


@login_required
@user_passes_test(is_admin)
def save_general_settings(request):
    if request.method == 'POST':
        shop_name = request.POST.get('shop_name', '').strip()
        shop_phone = request.POST.get('shop_phone', '').strip()
        shop_address = request.POST.get('shop_address', '').strip()
        receipt_footer = request.POST.get('receipt_footer', '').strip()

        settings, _ = SystemSetting.objects.get_or_create(id=1)
        settings.shop_name = shop_name
        settings.shop_phone = shop_phone
        settings.shop_address = shop_address
        settings.receipt_footer = receipt_footer
        settings.save()

        messages.success(request, "Business details updated successfully!")

    return redirect('settings')

from django.http import JsonResponse
from django.db.models import Sum
from .models import Product, Sale

def dashboard_stats_api(request):
    try:
        products = Product.objects.all()

        # Calculate totals in Python since main_store_stock & shop_stock are model properties
        total_main_store_stock = sum(getattr(p, 'main_store_stock', 0) for p in products)
        total_shop_stock = sum(getattr(p, 'shop_stock', 0) for p in products)

        # Committed & Non-refunded Sales Stock Sold
        total_stock_sold = Sale.objects.filter(
            is_refunded=False
        ).aggregate(total=Sum('items__quantity'))['total'] or 0

        # Pending Sales & Credit
        pending_sales_qs = Sale.objects.filter(
            payment_status='PENDING', 
            is_refunded=False
        ).select_related('customer')

        total_pending_credit = pending_sales_qs.aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')

        # Overdue Count
        today = timezone.now().date()
        overdue_count = sum(1 for sale in pending_sales_qs if getattr(sale, 'is_overdue', False))

        # Serialize Pending Sales
        pending_sales_data = []
        for sale in pending_sales_qs.order_by('due_date'):
            customer_name = "Walk-in Customer"
            if sale.customer:
                customer_name = sale.customer.name
            elif getattr(sale, 'customer_name_raw', None):
                customer_name = sale.customer_name_raw

            pending_sales_data.append({
                "id": sale.id,
                "receipt_number": sale.receipt_number,
                "customer_name": customer_name,
                "balance_due": f"{sale.balance_due:,.2f}",
                "due_date": sale.due_date.strftime("%d-%b-%Y") if sale.due_date else "N/A",
                "is_overdue": getattr(sale, 'is_overdue', False),
            })

        # Serialize Product Stock Details
        products_data = []
        for p in products:
            main_stock = getattr(p, 'main_store_stock', 0)
            shop_stock = getattr(p, 'shop_stock', 0)
            products_data.append({
                "id": p.id,
                "product_code": p.product_code or "N/A",
                "product_name": p.product_name,
                "main_store_stock": main_stock,
                "shop_stock": shop_stock,
            })

        return JsonResponse({
            "kpis": {
                "total_main_store_stock": total_main_store_stock,
                "total_shop_stock": total_shop_stock,
                "total_stock_sold": total_stock_sold,
                "total_pending_credit": f"{total_pending_credit:,.2f}",
                "overdue_count": overdue_count,
            },
            "pending_sales": pending_sales_data,
            "products": products_data
        })

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
def reports_stats_api(request):
    period = request.GET.get('period', 'day')
    now = timezone.now()

    # Time filtering setup
    if period == 'day':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start_date = now - timedelta(days=7)
    elif period == 'month':
        start_date = now - timedelta(days=30)
    elif period == '6months':
        start_date = now - timedelta(days=180)
    elif period == 'year':
        start_date = now - timedelta(days=365)
    else:  # 'all'
        start_date = None

    sales = Sale.objects.filter(is_committed=True, is_refunded=False)
    if start_date:
        sales = sales.filter(created_at__gte=start_date)

    # Calculate KPIs
    total_revenue = sales.aggregate(total=Sum('total_amount'))['total'] or 0.0
    total_collected = sales.aggregate(total=Sum('amount_paid'))['total'] or 0.0
    total_pending_debt = sales.aggregate(total=Sum('balance_due'))['total'] or 0.0
    total_sales_count = sales.count()

    # Top Customers
    top_customers_qs = sales.exclude(customer__isnull=True)\
        .values('customer__name', 'customer__phone')\
        .annotate(total_orders=Count('id'), total_spent=Sum('total_amount'))\
        .order_by('-total_spent')[:5]

    top_customers = [
        {
            "name": c['customer__name'],
            "phone": c['customer__phone'] or "",
            "orders": c['total_orders'],
            "spent": float(c['total_spent'] or 0)
        } for c in top_customers_qs
    ]

    # Top Products
    sale_items = SaleItem.objects.filter(sale__in=sales)
    top_products_qs = sale_items.values('product__product_name', 'product__product_code')\
        .annotate(total_qty=Sum('quantity'), total_sales_val=Sum('subtotal'))\
        .order_by('-total_qty')[:5]

    top_products = [
        {
            "name": p['product__product_name'],
            "code": p['product__product_code'] or "N/A",
            "qty": p['total_qty'],
            "value": float(p['total_sales_val'] or 0)
        } for p in top_products_qs
    ]

    return JsonResponse({
        "kpis": {
            "total_revenue": float(total_revenue),
            "total_collected": float(total_collected),
            "total_pending_debt": float(total_pending_debt),
            "total_sales_count": total_sales_count,
        },
        "top_customers": top_customers,
        "top_products": top_products
    })

def history_stats_api(request):
    # 1. Stock In Records
    stock_in_qs = StockIn.objects.select_related('product', 'created_by').order_by('-created_at')[:50]
    stock_in_history = [
        {
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M"),
            "product_code": item.product.product_code or "N/A",
            "product_name": item.product.product_name,
            "quantity": item.quantity,
            "created_by": item.created_by.username if item.created_by else "System"
        }
        for item in stock_in_qs
    ]

    # 2. Stock Out Records
    stock_out_qs = StockOut.objects.select_related('product', 'created_by').order_by('-created_at')[:50]
    stock_out_history = [
        {
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M"),
            "product_code": item.product.product_code or "N/A",
            "product_name": item.product.product_name,
            "quantity": item.quantity,
            "created_by": item.created_by.username if item.created_by else "System"
        }
        for item in stock_out_qs
    ]

    # 3. Sales Records
    sales_qs = Sale.objects.select_related('customer', 'cashier').prefetch_related('items__product').order_by('-created_at')[:50]
    sales_history = []
    for sale in sales_qs:
        customer_name = (
            sale.customer.name if sale.customer
            else (sale.customer_name_raw or "Walk-in Customer")
        )

        items_bought = [
            {
                "product_name": item.product.product_name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price_at_sale)
            }
            for item in sale.items.all()
        ]

        sales_history.append({
            "id": sale.id,
            "created_at": sale.created_at.strftime("%Y-%m-%d %H:%M"),
            "receipt_number": sale.receipt_number,
            "customer_name": customer_name,
            "items": items_bought,
            "total_amount": float(sale.total_amount),
            "amount_paid": float(sale.amount_paid),
            "balance_due": float(sale.balance_due),
            "payment_status": sale.payment_status,
            "is_refunded": sale.is_refunded,
            "cashier": sale.cashier.username if sale.cashier else "System"
        })

    return JsonResponse({
        "stock_in_history": stock_in_history,
        "stock_out_history": stock_out_history,
        "sales_history": sales_history
    })

@user_passes_test(warehouse_required)
def stock_in_view(request):
    if request.method == 'POST':
        form = StockInForm(request.POST, prefix="stock_in")
        if form.is_valid():
            with transaction.atomic():
                stock_in = form.save(commit=False)
                stock_in.created_by = request.user
                stock_in.save()

            # Broadcast WebSocket updates
            channel_layer = get_channel_layer()

            # 1. Trigger dashboard tables & KPI refresh across connected clients
            async_to_sync(channel_layer.group_send)(
                "dashboard",
                {
                    "type": "dashboard_update",
                    "data": {}
                }
            )

            # 2. Broadcast live notification alert
            async_to_sync(channel_layer.group_send)(
                "notifications",
                {
                    "type": "send_notification",
                    "title": "Stock In Success",
                    "message": f"Added +{stock_in.quantity} units of {stock_in.product.product_name} to Main Store.",
                    "level": "success"
                }
            )

            messages.success(request, f"Intake recorded: +{stock_in.quantity} units of {stock_in.product.product_name}.")
            return redirect('main_store')
        else:
            messages.error(request, "Failed to record stock intake. Please check form entries.")
            
    return redirect('main_store')

@require_POST
def record_debt_payment(request):
    sale_id = request.POST.get('sale_id')
    amount_paid_raw = request.POST.get('amount_paid')

    if not sale_id or not amount_paid_raw:
        return JsonResponse({'success': False, 'error': 'Missing required fields.'}, status=400)

    try:
        sale = Sale.objects.get(id=sale_id)
        amount_paid = Decimal(str(amount_paid_raw))

        if amount_paid <= 0:
            return JsonResponse({'success': False, 'error': 'Payment amount must be greater than zero.'})

        # Update sale balances
        sale.amount_paid = (sale.amount_paid or Decimal('0.00')) + amount_paid
        sale.balance_due = max(Decimal('0.00'), sale.total_amount - sale.amount_paid)

        if sale.balance_due <= 0:
            sale.payment_status = 'PAID'
        else:
            sale.payment_status = 'PENDING'

        sale.save()

        # Optional: Record individual Payment log if model exists
        # Payment.objects.create(sale=sale, amount=amount_paid)

        # Broadcast live update via Channels Layer
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "live_updates",
            {
                "type": "broadcast_message",
                "message": {
                    "type": "payment_update",
                    "sale_id": sale.id
                }
            }
        )

        return JsonResponse({'success': True, 'balance_due': float(sale.balance_due)})

    except Sale.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Sale record not found.'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

def edit_product(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    if request.method == 'POST':
        product_code = request.POST.get('product_code', '').strip()
        product_name = request.POST.get('product_name', '').strip()
        category = request.POST.get('category', '').strip()
        min_stock_level = request.POST.get('min_stock_level')

        # 1. Basic validation
        if not product_code or not product_name:
            messages.error(request, "Product code and name cannot be empty.")
            return redirect(request.META.get('HTTP_REFERER', 'settings'))

        # 2. Check if product_code is already used by ANOTHER product
        existing_product = Product.objects.filter(product_code=product_code).exclude(id=product.id).first()
        if existing_product:
            messages.error(
                request, 
                f"The product code '{product_code}' is already assigned to '{existing_product.product_name}'."
            )
            return redirect(request.META.get('HTTP_REFERER', 'settings'))

        # 3. Update fields safely
        product.product_code = product_code
        product.product_name = product_name
        product.category = category if category else None
        
        try:
            product.min_stock_level = int(min_stock_level) if min_stock_level else 10
        except ValueError:
            product.min_stock_level = 10

        # 4. Save and handle unexpected database integrity errors as a backup
        try:
            product.save()
            messages.success(request, f"Product '{product.product_name}' updated successfully!")
        except IntegrityError:
            messages.error(request, f"Product code '{product_code}' is already in use.")
            
        return redirect(request.META.get('HTTP_REFERER', 'settings'))

    return redirect('settings')

def print_receipt_view(request, sale_id):
    # Fetch the sale or return 404
    sale = get_object_or_404(Sale, id=sale_id)
    
    # Optional: fetch business info if stored in a model/settings, 
    # or hardcode fallback details below in the context or template
    context = {
        'sale': sale,
        'printed_at': timezone.now(),
        # Add business info here if not stored in database:
        'business_name': 'My Store Name',
        'business_phone': '+256 700 000000',
        'business_location': 'Kampala, Uganda',
        'receipt_footer': 'Thank you for shopping with us! Items non-refundable.',
    }
    
    return render(request, 'core/print_receipt.html', context)

def view_receipt(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    # Fetch the business settings row configured in Tab 4
    settings = SystemSettings.objects.first() 
    
    return render(request, 'core/receipt.html', {
        'sale': sale,
        'settings': settings,
    })

@login_required
def edit_user(request, user_id):
    if not (request.user.is_superuser or getattr(request.user, 'role', '') == 'admin'):
        messages.error(request, "Permission denied.")
        return redirect('settings')
        
    user_obj = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        user_obj.username = request.POST.get('username')
        user_obj.email = request.POST.get('email')
        user_obj.role = request.POST.get('role')
        
        new_password = request.POST.get('password')
        if new_password and new_password.strip():
            user_obj.set_password(new_password)
            
        user_obj.save()
        messages.success(request, f"User {user_obj.username} updated successfully.")
    return redirect('settings')