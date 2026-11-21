from django.contrib import admin
from .models import Zona, Cliente, Comision


@admin.register(Zona)
class ZonaAdmin(admin.ModelAdmin):
    list_display = ('orden_recorrido', 'nombre')
    list_editable = ('nombre',)
    ordering = ('orden_recorrido',)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'telefono', 'zona_habitual', 'direccion_frecuente')
    search_fields = ('nombre', 'direccion_frecuente')
    list_filter = ('zona_habitual',)


@admin.register(Comision)
class ComisionAdmin(admin.ModelAdmin):
    list_display = ('fecha_viaje', 'hora_limite', 'zona', 'cliente', 'direccion_entrega', 'estado', 'monto', 'pagado')
    list_filter = ('fecha_viaje', 'estado', 'zona', 'pagado', 'tipo')
    search_fields = ('cliente__nombre', 'direccion_entrega', 'notas')
    list_editable = ('estado', 'pagado')
    ordering = ('fecha_viaje', 'hora_limite', 'zona__orden_recorrido')
