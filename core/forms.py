# core/forms.py
from django import forms
from django.core.exceptions import ValidationError
from .models import Product, StockIn, StockOut, Sale, Customer

from django import forms
from .models import Product, StockIn, StockOut

class StockInForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        queryset=Product.objects.all().order_by('product_code'),
        widget=forms.Select(attrs={'class': 'form-select product-code-select', 'required': 'required'})
    )

    class Meta:
        model = StockIn
        fields = ['product', 'quantity']
        widgets = {
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'placeholder': 'Quantity Received'}),
        }


class StockOutForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        queryset=Product.objects.all().order_by('product_code'),
        widget=forms.Select(attrs={'class': 'form-select product-code-select', 'required': 'required'})
    )

    class Meta:
        model = StockOut
        fields = ['product', 'quantity']
        widgets = {
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'placeholder': 'Quantity Transferred'}),
        }
# 3. SHOP SALE CHECKOUT FORM
class SaleCheckoutForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ['customer', 'customer_name_raw', 'amount_paid', 'due_date']
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'customer_name_raw': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Or enter customer name'}),
            'amount_paid': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_amount_paid'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'id': 'id_due_date'}),
        }


# 4. SETTINGS DEFAULT PRICE UPDATE FORM
class ProductPriceForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['default_unit_price']
        widgets = {
            'default_unit_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }