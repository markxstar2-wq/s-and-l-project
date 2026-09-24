from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    
    path('main-store/', views.main_store_view, name='main_store'),
    path('pos/', views.shop_pos_view, name='shop_pos'),
    path('sales-history/', views.history_view, name='sales_history'),
    path('sales/<int:sale_id>/refund/', views.process_refund, name='process_refund'),
    path('settings/', views.settings_view, name='settings'),
    path('close-shop/', views.close_shop_batch, name='close_shop'),
    path('history/', views.history_view, name='history'),
    path('record-payment/<int:sale_id>/', views.record_payment_view, name='record_payment'),
    path('settings/add-user/', views.add_user, name='add_user'),
    path('reports/', views.reports_view, name='reports'),
    
    # Settings main view
    path('settings/', views.settings_view, name='settings'),
    
    # Settings Action URLs
    path('settings/add-product/', views.add_product, name='add_product'),
    path('settings/add-user/', views.add_user, name='add_user'),
    path('settings/update-user-role/<int:user_id>/', views.update_user_role, name='update_user_role'),
    path('settings/save-notifications/', views.save_notification_settings, name='save_notification_settings'),
    path('settings/save-general/', views.save_general_settings, name='save_general_settings'),
    path('settings/user/edit/<int:user_id>/', views.edit_user, name='edit_user'),
    path('api/dashboard-stats/', views.dashboard_stats_api, name='dashboard_stats_api'),
    path('api/reports-stats/', views.reports_stats_api, name='reports_stats_api'),
    path('api/history-stats/', views.history_stats_api, name='history_stats_api'),
    path('stock-in/', views.stock_in_view, name='stock_in'),
    path('api/record-debt-payment/', views.record_debt_payment, name='record_debt_payment'),
    path('products/<int:product_id>/edit/', views.edit_product, name='edit_product'),
    path('sales/<int:sale_id>/print/', views.print_receipt_view, name='print_receipt'),
]