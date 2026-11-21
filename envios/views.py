import json
import math
import urllib.request
import urllib.parse
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import F, Sum
from django.utils import timezone
from google import genai
from .models import Comision, Cliente


def geocodificar_rosario(direccion):
    """Obtiene (lat, lon) reales de una calle en Rosario usando OpenStreetMap gratis."""
    query = urllib.parse.quote(f"{direccion}, Rosario, Santa Fe, Argentina")
    url = f"https://openstreetmap.org{query}&format=json&limit=1"
    req = urllib.request.Request(url, headers={'User-Agent': 'ComisionesFirmatApp/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
    except Exception:
        pass
    return None


def distancia_km(coord1, coord2):
    """Calcula distancia aproximada en km entre dos puntos (lat, lon)."""
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    return math.hypot((lat2 - lat1) * 111.0, (lon2 - lon1) * 95.0)


def hoja_de_ruta(request):
    hoy = timezone.localdate()

    pendientes = Comision.objects.filter(
        fecha_viaje=hoy,
        estado='PENDIENTE'
    ).order_by(
        'orden_ia',
        F('hora_limite').asc(nulls_last=True),
        'id'
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

    consejo_ia = request.session.pop('consejo_ia', None)

    contexto = {
        'hoy': hoy,
        'pendientes': pendientes,
        'entregadas': entregadas,
        'total_cobrado': total_cobrado,
        'total_pendiente_cobro': total_pendiente_cobro,
        'consejo_ia': consejo_ia,
    }
    return render(request, 'envios/hoja_ruta.html', contexto)


def optimizar_ruta_ia(request):
    if request.method == 'POST':
        hoy = timezone.localdate()
        pendientes = list(Comision.objects.filter(fecha_viaje=hoy, estado='PENDIENTE'))

        if len(pendientes) > 1:
            # 1. Separar las que tienen hora límite (van primero ordenadas por horario)
            con_hora = sorted([p for p in pendientes if p.hora_limite], key=lambda x: x.hora_limite)
            sin_hora = [p for p in pendientes if not p.hora_limite]

            # 2. Punto de partida: Entrada desde Firmat (Ruta 33 / Av. Pte. Perón y Circunvalación)
            punto_actual = (-32.9565, -60.7280)
            if con_hora:
                coord_ultima_urgente = geocodificar_rosario(con_hora[-1].direccion_entrega)
                if coord_ultima_urgente:
                    punto_actual = coord_ultima_urgente

            # 3. Geocodificar y ordenar por cercanía real (Vecino más cercano)
            con_coords = []
            sin_coords = []
            for p in sin_hora:
                c = geocodificar_rosario(p.direccion_entrega)
                if c:
                    con_coords.append((p, c))
                else:
                    sin_coords.append(p)

            ordenadas_geo = []
            while con_coords:
                mas_cercana = min(con_coords, key=lambda item: distancia_km(punto_actual, item[1]))
                ordenadas_geo.append(mas_cercana[0])
                punto_actual = mas_cercana[1]
                con_coords.remove(mas_cercana)

            ruta_final = con_hora + ordenadas_geo + sin_coords

            for posicion, comision in enumerate(ruta_final, start=1):
                Comision.objects.filter(id=comision.id).update(orden_ia=posicion)

            # 4. Pedir a Gemini un resumen corto basado en la ruta ya calculada
            resumen_direcciones = " -> ".join([p.direccion_entrega for p in ruta_final])
            consejo_texto = f"Ruta ordenada por cercanía real entrando desde Ruta 33: {resumen_direcciones}."

            if settings.GEMINI_API_KEY:
                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                prompt = f"""
                Sos un asistente logístico en Rosario, Santa Fe. Un comisionista que viene desde Firmat (Ruta 33 / Av. Pte. Perón) tiene este recorrido ya ordenado por GPS:
                {resumen_direcciones}
                Escribí un consejo práctico de máximo 2 oraciones cortas indicando por qué arterias principales conviene moverse para hacer estas paradas rápido.
                """
                for modelo in ['gemini-3.8-flash', 'gemini-3.8-flash-lite', 'gemini-2.5-flash-lite']:
                    try:
                        resp = client.models.generate_content(model=modelo, contents=prompt)
                        if resp.text:
                            consejo_texto = resp.text.strip()
                            break
                    except Exception:
                        continue

            request.session['consejo_ia'] = consejo_texto

    return redirect('hoja_de_ruta')


def nueva_comision(request):
    clientes = Cliente.objects.all().order_by('nombre')

    if request.method == 'POST':
        nombre_cliente = request.POST.get('cliente_nombre', '').strip()
        cliente_obj, _ = Cliente.objects.get_or_create(nombre=nombre_cliente)

        hora = request.POST.get('hora_limite') or None
        monto = request.POST.get('monto') or 0

        Comision.objects.create(
            fecha_viaje=timezone.localdate(),
            cliente=cliente_obj,
            tipo=request.POST.get('tipo', 'SOBRE'),
            direccion_entrega=request.POST.get('direccion_entrega'),
            hora_limite=hora,
            monto=monto,
            pagado=('pagado' in request.POST),
            notas=request.POST.get('notas', '')
        )
        return redirect('hoja_de_ruta')

    return render(request, 'envios/nueva_comision.html', {
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
