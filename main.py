import requests
import datetime

from django import forms
from django.shortcuts import redirect, render
from django.views import View
from django.urls import reverse_lazy
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth import authenticate, login
from django.contrib.auth import views as auth_views

from foodcartapp.models import Product, Restaurant, FoodCart, RestaurantMenuItem
from places.models import Place

from geopy import distance
from operator import itemgetter
from star_burger.settings import YA_GEO_APIKEY


class Login(forms.Form):
    username = forms.CharField(
        label='Логин', max_length=75, required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Укажите имя пользователя'
        })
    )
    password = forms.CharField(
        label='Пароль', max_length=75, required=True,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Введите пароль'
        })
    )


class LoginView(View):
    def get(self, request, *args, **kwargs):
        form = Login()
        return render(request, "login.html", context={
            'form': form
        })

    def post(self, request):
        form = Login(request.POST)

        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']

            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                if user.is_staff:  # FIXME replace with specific permission
                    return redirect("restaurateur:RestaurantView")
                return redirect("start_page")

        return render(request, "login.html", context={
            'form': form,
            'ivalid': True,
        })


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy('restaurateur:login')


def is_manager(user):
    return user.is_staff  # FIXME replace with specific permission


@user_passes_test(is_manager, login_url='restaurateur:login')
def view_products(request):
    restaurants = list(Restaurant.objects.order_by('name'))
    products = list(Product.objects.prefetch_related('menu_items'))

    default_availability = {restaurant.id: False for restaurant in restaurants}
    products_with_restaurants = []
    for product in products:

        availability = {
            **default_availability,
            **{item.restaurant_id: item.availability for item in product.menu_items.all()},
        }
        orderer_availability = [availability[restaurant.id] for restaurant in restaurants]

        products_with_restaurants.append(
            (product, orderer_availability)
        )

    return render(request, template_name="products_list.html", context={
        'products_with_restaurants': products_with_restaurants,
        'restaurants': restaurants,
    })


@user_passes_test(is_manager, login_url='restaurateur:login')
def view_restaurants(request):
    return render(request, template_name="restaurants_list.html", context={
        'restaurants': Restaurant.objects.all(),
    })


def get_burger_availability():
    restaurantsmenuitems = list(RestaurantMenuItem.objects.select_related(
        'restaurant',
        'product'
        ).all())
    burger_availability = {}
    for item in restaurantsmenuitems:
        if item.product not in burger_availability:
            burger_availability[item.product] = []
        if item.availability:
            burger_availability[item.product].append(item.restaurant)
    return burger_availability


def get_suitable_restaurant(menuitems, ordered_items):
    restaurant_list = []
    for item in ordered_items:
        if item in menuitems.keys():
            restaurant_list.append(menuitems[item])
    return set.intersection(*[set(list) for list in restaurant_list])


def get_or_create_place(api_key, place, saved_places):
    
    for saved_place in saved_places:
        if saved_place['address'] == place.address:
            lat = saved_place['lat']
            lon = saved_place['lon']
            return lat, lon
        
    lon, lat = fetch_coordinates(api_key, place.address)
    Place.objects.create(
        address=place.address,
        lon=lon,
        lat=lat,
        date=datetime.datetime.now()
        )
    return lat, lon


def fetch_coordinates(apikey, place):
    base_url = "https://geocode-maps.yandex.ru/1.x"
    params = {"geocode": place, "apikey": apikey, "format": "json"}
    response = requests.get(base_url, params=params)
    response.raise_for_status()
    found_places = response.json()['response']['GeoObjectCollection']['featureMember']
    
    if not found_places:
        return None

    most_relevant = found_places[0]
    lon, lat = most_relevant['GeoObject']['Point']['pos'].split(" ")
    return lon, lat


@user_passes_test(is_manager, login_url='restaurateur:login')
def view_orders(request):

    orders = []
    saved_places = list(Place.objects.values())
    menuitems = get_burger_availability()

    if not menuitems:
        menuitems = []

    for order in list(FoodCart.objects.get_original_price().prefetch_related('entries')):
        products = order.entries.all().select_related('product')
        ordered_products_list = [product.product for product in products]
        order_restraurants = get_suitable_restaurant(
            menuitems,
            ordered_products_list
            )
        order_place_lat, order_place_lon = get_or_create_place(
            YA_GEO_APIKEY,
            order, saved_places
            )
        restaurant_distances = []

        for restaurant in order_restraurants:
            restaurant_lat, restaurant_lon = get_or_create_place(
                YA_GEO_APIKEY,
                restaurant,
                saved_places
                )
            distance_to_restaurant = distance.distance(
                (restaurant_lat, restaurant_lon), 
                (order_place_lat, order_place_lon)
                ).km
            restaurant_distances.append([restaurant.name, round(distance_to_restaurant, 1)])
        restaurant_distances = sorted(restaurant_distances, key=itemgetter(1))

        order = {
            'id': order.id,
            'price': order.price,
            'firstname': order.firstname,
            'lastname': order.lastname,
            'phonenumber': order.phonenumber,
            'address': order.address,
            'status': order.get_status_display(),
            'comment': order.comment,
            'payment_method': order.get_payment_method_display(),
            'restaurant': restaurant_distances
            }
        orders.append(order)

    return render(
        request,
        template_name='order_items.html',
        context={
            'order_items': orders}
        )
