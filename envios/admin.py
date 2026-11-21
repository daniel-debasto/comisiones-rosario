from django.contrib import admin
from .models import Cliente, Comision


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'telefono', 'direccion_frecuente')
    search_fields = ('nombre', 'direccion_frecuente')


@admin.register(Comision)
class ComisionAdmin(admin.ModelAdmin):
    list_display = ('fecha_viaje', 'orden_ia', 'hora_limite', 'cliente', 'direccion_entrega', 'estado', 'monto', 'pagado')
    list_filter = ('fecha_viaje', 'estado', 'pagado', 'tipo')
    search_fields = ('cliente__nombre', 'direccion_entrega', 'notas')
    list_editable = ('estado', 'pagado')
    ordering = ('fecha_viaje', 'orden_ia', 'hora_limite')
