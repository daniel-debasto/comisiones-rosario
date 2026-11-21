from django.db import models
from django.utils import timezone


class Cliente(models.Model):
    nombre = models.CharField(max_length=150)
    telefono = models.CharField(max_length=50, blank=True, null=True)
    direccion_frecuente = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.nombre


class Comision(models.Model):
    ESTADOS = [
        ('PENDIENTE', 'Pendiente'),
        ('ENTREGADO', 'Entregado'),
        ('REPROGRAMADO', 'Reprogramado'),
    ]

    TIPOS = [
        ('SOBRE', 'Sobre / Documentación'),
        ('BULTO', 'Paquete / Carga'),
        ('TRAMITE', 'Trámite / Gestión'),
    ]

    fecha_viaje = models.DateField(default=timezone.localdate)
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='comisiones')
    tipo = models.CharField(max_length=20, choices=TIPOS, default='SOBRE')
    direccion_entrega = models.CharField(max_length=255)
    hora_limite = models.TimeField(blank=True, null=True, help_text="Dejar vacío si no tiene horario fijo")
    estado = models.CharField(max_length=20, choices=ESTADOS, default='PENDIENTE')
    monto = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pagado = models.BooleanField(default=False)
    notas = models.TextField(blank=True, null=True)
    orden_ia = models.PositiveIntegerField(default=99)

    def __str__(self):
        return f"{self.cliente} - {self.direccion_entrega} ({self.estado})"
