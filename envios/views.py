import json
import math
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import F, Sum
from django.utils import timezone
from google import genai
from google.genai import types
from .models import Comision, Cliente


def distancia_km(coord1, coord2):
    """Calcula distancia en km entre dos puntos (lat, lon)."""
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

        if len(pendientes) > 1 and settings.GEMINI_API_KEY:
            lista_paradas = [
                {"id": p.id, "direccion": p.direccion_entrega}
                for p in pendientes
            ]

            prompt = f"""
            Sos un sistema GPS de Rosario, Santa Fe, Argentina.
            Devolvé las coordenadas aproximadas (lat, lon) en Rosario para cada dirección de esta lista:
            {json.dumps(lista_paradas, ensure_ascii=False)}

            Respondé únicamente un JSON con este formato exacto:
            {{"coordenadas": [{{"id": 1, "lat": -32.95, "lon": -60.65}}]}}
            """

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            coords_por_id = {}

            for modelo in ['gemini-3.8-flash', 'gemini-3.8-flash-lite', 'gemini-2.5-flash-lite']:
                try:
                    resp = client.models.generate_content(
                        model=modelo,
                        contents=prompt,
                        config=types.GenerateContentConfig(response_mime_type="application/json"),
                    )
                    data = json.loads(resp.text)
                    for item in data.get("coordenadas", []):
                        coords_por_id[item["id"]] = (float(item["lat"]), float(item["lon"]))
                    if coords_por_id:
                        break
                except Exception:
                    continue

            # 1. Las que tienen hora límite van primero ordenadas por horario
            con_hora = sorted([p for p in pendientes if p.hora_limite], key=lambda x: x.hora_limite)
            sin_hora = [p for p in pendientes if not p.hora_limite]

            # 2. Punto de entrada desde Firmat: Av. Pte. Perón y Circunvalación (Ruta 33)
            punto_actual = (-32.9565, -60.7280)
            if con_hora and con_hora[-1].id in coords_por_id:
                punto_actual = coords_por_id[con_hora[-1].id]

            # 3. Ordenar por cercanía real paso a paso (Vecino más cercano)
            con_coords = [(p, coords_por_id[p.id]) for p in sin_hora if p.id in coords_por_id]
            sin_coords = [p for p in sin_hora if p.id not in coords_por_id]

            ordenadas_geo = []
            while con_coords:
                mas_cercana = min(con_coords, key=lambda item: distancia_km(punto_actual, item[1]))
                ordenadas_geo.append(mas_cercana[0])
                punto_actual = mas_cercana[1]
                con_coords.remove(mas_cercana)

            ruta_final = con_hora + ordenadas_geo + sin_coords

            for posicion, comision in enumerate(ruta_final, start=1):
                Comision.objects.filter(id=comision.id).update(orden_ia=posicion)

            # 4. Pedir consejo corto en texto plano (sin asteriscos Markdown)
            recorrido_txt = " -> ".join([p.direccion_entrega for p in ruta_final])
            consejo_texto = f"Orden sugerido desde Ruta 33: {recorrido_txt}."

            prompt_consejo = f"""
            Un comisionista entra a Rosario desde Firmat (Ruta 33 / Av. Pte. Perón) con este recorrido ya ordenado:
            {recorrido_txt}
            Escribí un consejo breve de 2 oraciones sobre qué avenidas tomar para hacerlo rápido. No uses asteriscos ni negritas.
            """
            for modelo in ['gemini-3.8-flash', 'gemini-3.8-flash-lite', 'gemini-2.5-flash-lite']:
                try:
                    resp2 = client.models.generate_content(model=modelo, contents=prompt_consejo)
                    if resp2.text:
                        consejo_texto = resp2.text.replace('**', '').strip()
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
