from django.urls import path
from tracking.views import *
from django_ratelimit.decorators import ratelimit

urlpatterns = [
    path("trips/update_positions/", ratelimit(key='ip', method='POST', rate='2/m')(simulate_positions_view), name="update-trip-positions"),
]
