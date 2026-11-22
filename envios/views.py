import json
import math
import time
import urllib.request
import urllib.parse
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import F, Sum
from django.utils import timezone
from google import genai
from .models import Comision, Cliente


def normalizar_calle(direccion):
    """Limpia abreviaturas comunes para que OpenStreetMap encuentre la altura exacta."""
    texto = direccion.strip()
    reemplazos = {
        "Av.": "Avenida",
        "Av ": "Avenida ",
        "Pres.": "Presidente",
        "Pte.": "Presidente",
        "Bv.": "Boulevard",
        "Gral.": "General",
        "Sta.": "Santa",
    }
    for corto, largo in reemplazos.items():
        texto = texto.replace(corto, largo)
    return texto


def obtener_coordenadas_reales(direccion):
    """Consulta Nominatim (OpenStreetMap) para obtener latitud y longitud reales en Rosario."""
    calle_limpia = normalizar_calle(direccion)
    params = urllib.parse.urlencode({
        "q": f"{calle_limpia}, Rosario, Santa Fe, Argentina",
        "format": "json",
        "limit": 1,
        "countrycodes": "ar"
    })
    url = f"https://openstreetmap.org?{params}"
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'ComisionesFirmatRosario/2.0 (contacto@comisionesfirmat.com)'}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            datos = json.loads(resp.read().decode('utf-8'))
            if datos:
                return float(datos[0]['lat']), float(datos[0]['lon'])
    except Exception:
        pass
    return None


def distancia_km(coord1, coord2):
    """Calcula la distancia real en km entre dos puntos GPS."""
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
            # 1. Las que tienen hora límite van primero ordenadas por horario
            con_hora = sorted([p for p in pendientes if p.hora_limite], key=lambda x: x.hora_limite)
            sin_hora = [p for p in pendientes if not p.hora_limite]

            # 2. Punto de entrada desde Firmat: Av. Pte. Perón y Circunvalación (Ruta 33)
            punto_actual = (-32.9565, -60.7280)
            if con_hora:
                coord_urgente = obtener_coordenadas_reales(con_hora[-1].direccion_entrega)
                if coord_urgente:
                    punto_actual = coord_urgente

            # 3. Buscar coordenadas GPS reales de cada domicilio sin hora fija
            con_coords = []
            sin_coords = []
            for p in sin_hora:
                coord = obtener_coordenadas_reales(p.direccion_entrega)
                if coord:
                    con_coords.append((p, coord))
                else:
                    sin_coords.append(p)
                time.sleep(0.5)

            # 4. Ordenar por vecino más cercano real paso a paso
            ordenadas_geo = []
            while con_coords:
                mas_cercana = min(con_coords, key=lambda item: distancia_km(punto_actual, item[1]))
                ordenadas_geo.append(mas_cercana[0])
                punto_actual = mas_cercana[1]
                con_coords.remove(mas_cercana)

            ruta_final = con_hora + ordenadas_geo + sin_coords

            for posicion, comision in enumerate(ruta_final, start=1):
                Comision.objects.filter(id=comision.id).update(orden_ia=posicion)

            # 5. Generar recomendación corta con Gemini sobre la ruta ya ordenada
            recorrido_txt = " -> ".join([p.direccion_entrega for p in ruta_final])
            no_encontradas = [p.direccion_entrega for p in sin_coords]
            aviso_no_ubicadas = f" (No se ubicaron en el mapa: {', '.join(no_encontradas)})" if no_encontradas else ""
            consejo_texto = f"Orden calculado por GPS entrando desde Ruta 33.{aviso_no_ubicadas}"

            if settings.GEMINI_API_KEY:
                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                prompt = f"""
                Un comisionista entra a Rosario desde Firmat (Ruta 33 / Av. Pte. Perón) y tiene este recorrido ya ordenado por GPS:
                {recorrido_txt}
                Escribí un consejo práctico de máximo 2 oraciones sobre qué avenidas tomar para unir estas paradas sin dar vueltas. No uses asteriscos ni negritas.
                """
                for modelo in ['gemini-3.8-flash', 'gemini-3.8-flash-lite', 'gemini-2.5-flash-lite']:
                    try:
                        resp = client.models.generate_content(model=modelo, contents=prompt)
                        if resp.text:
                            consejo_texto = resp.text.replace('**', '').strip() + aviso_no_ubicadas
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
