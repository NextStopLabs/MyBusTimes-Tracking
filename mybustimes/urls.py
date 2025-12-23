from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from mybustimes import settings
import debug_toolbar
from django.conf import settings
from django.urls import include, path
from django.views.decorators.cache import cache_control
from django.views.generic.base import RedirectView

urlpatterns = [
    path('api/', include('api.urls')),  # Include your API app urls here
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_URL)

if settings.DEBUG:
    urlpatterns += [
        path('__debug__/', include(debug_toolbar.urls)),
    ]