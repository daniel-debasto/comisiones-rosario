from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import F, Sum
from django.utils import timezone
from .models import Comision, Cliente, Zona


def hoja_de_ruta(request):
    hoy = timezone.localdate()

    pendientes = Comision.objects.filter(
        fecha_viaje=hoy,
        estado='PENDIENTE'
    ).order_by(
        F('hora_limite').asc(nulls_last=True),
        'zona__orden_recorrido'
    )

    entregadas = Comision.objects.filter(
        fecha_viaje=hoy,
        estado='ENTREGADO'
    ).order_by('-id')

    total_cobrado = entregadas.filter(pagado=True).aggregate(Sum('monto'))['monto__sum'] or 0
    total_pendiente_cobro = Comision.objects.filter(
        fecha_viaje=hoy,
        pagado=False
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    contexto = {
        'hoy': hoy,
        'pendientes': pendientes,
        'entregadas': entregadas,
        'total_cobrado': total_cobrado,
        'total_pendiente_cobro': total_pendiente_cobro,
    }
    return render(request, 'envios/hoja_ruta.html', contexto)


def nueva_comision(request):
    zonas = Zona.objects.all()
    clientes = Cliente.objects.all().order_by('nombre')

    if request.method == 'POST':
        nombre_cliente = request.POST.get('cliente_nombre', '').strip()
        cliente_obj, _ = Cliente.objects.get_or_create(nombre=nombre_cliente)

        zona_id = request.POST.get('zona')
        zona_obj = Zona.objects.filter(id=zona_id).first() if zona_id else None

        hora = request.POST.get('hora_limite') or None
        monto = request.POST.get('monto') or 0

        Comision.objects.create(
            fecha_viaje=timezone.localdate(),
            cliente=cliente_obj,
            tipo=request.POST.get('tipo', 'SOBRE'),
            direccion_entrega=request.POST.get('direccion_entrega'),
            zona=zona_obj,
            hora_limite=hora,
            monto=monto,
            pagado=('pagado' in request.POST),
            notas=request.POST.get('notas', '')
        )
        return redirect('hoja_de_ruta')

    return render(request, 'envios/nueva_comision.html', {
        'zonas': zonas,
        'clientes': clientes,
    })


def marcar_entregado(request, comision_id):
    if request.method == 'POST':
        comision = get_object_or_404(Comision, id=comision_id)
        comision.estado = 'ENTREGADO'
        if 'cobrado' in request.POST:
            comision.pagado = True
        comision.save()
    return redirect('hoja_de_ruta')
