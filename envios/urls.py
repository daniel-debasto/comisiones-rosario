from django.urls import path
from . import views

urlpatterns = [
    path('', views.hoja_de_ruta, name='hoja_de_ruta'),
    path('nueva/', views.nueva_comision, name='nueva_comision'),
    path('editar/<int:comision_id>/', views.editar_comision, name='editar_comision'),
    path('eliminar/<int:comision_id>/', views.eliminar_comision, name='eliminar_comision'),
    path('posponer/<int:comision_id>/', views.posponer_manana, name='posponer_manana'),
    path('devolver/<int:comision_id>/', views.devolver_hoy, name='devolver_hoy'),
    path('optimizar-ia/', views.optimizar_ruta_ia, name='optimizar_ruta_ia'),
    path('entregar/<int:comision_id>/', views.marcar_entregado, name='marcar_entregado'),
    path('cuentas/', views.cuentas_corrientes, name='cuentas_corrientes'),
    path('saldar-cliente/<int:cliente_id>/', views.saldar_cliente, name='saldar_cliente'),
]
