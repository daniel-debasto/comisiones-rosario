from django.urls import path
from . import views

urlpatterns = [
    path('', views.hoja_de_ruta, name='hoja_de_ruta'),
    path('nueva/', views.nueva_comision, name='nueva_comision'),
    path('entregar/<int:comision_id>/', views.marcar_entregado, name='marcar_entregado'),
]
