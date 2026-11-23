import json
import urllib.parse
from datetime import date, timedelta
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.db.models import F, Sum, Count
from django.utils import timezone
from google import genai
from google.genai import types
from .models import Comision, Cliente


def obtener_fecha_seleccionada(request):
    fecha_str = request.GET.get('fecha') or request.POST.get('fecha_viaje')
    if fecha_str:
        try:
            return date.fromisoformat(fecha_str)
        except ValueError:
            pass
    return timezone.localdate()


def redirigir_con_fecha(fecha_obj):
    return redirect(f"{reverse('hoja_de_ruta')}?fecha={fecha_obj.isoformat()}")

def construir_url_ruta_completa(pendientes):
    """Genera ruta multiparada con formato saddr/daddr +to: compatible con Android Google Maps."""
    lista = list(pendientes)
    if not lista:
        return None

    origen = urllib.parse.quote("Av. Presidente Perón y Circunvalación, Rosario, Santa Fe")
    destinos = [
        urllib.parse.quote(f"{p.direccion_entrega}, Rosario, Santa Fe")
        for p in lista
    ]

    dominio = "https://google.com"
    ruta = "/maps?saddr=" + origen + "&daddr=" + "+to:".join(destinos[:9])
    return dominio + ruta


def agregar_links_gps(pendientes):
    """Usa el esquema nativo geo: para que Android pase la dirección completa a Google Maps."""
    lista = list(pendientes)
    for item in lista:
        direccion_completa = f"{item.direccion_entrega}, Rosario, Santa Fe, Argentina"
        q = urllib.parse.quote(direccion_completa)
        item.url_maps = "geo:0,0?q=" + q
        item.url_waze = "https://waze.com" + "/ul?q=" + q + "&navigate=yes"
    return lista


def hoja_de_ruta(request):
    hoy = timezone.localdate()
    fecha_sel = obtener_fecha_seleccionada(request)
    ayer = fecha_sel - timedelta(days=1)
    manana = fecha_sel + timedelta(days=1)

    pendientes = Comision.objects.filter(
        fecha_viaje=fecha_sel,
        estado='PENDIENTE'
    ).order_by(
        'orden_ia',
        F('hora_limite').asc(nulls_last=True),
        'id'
    )

    entregadas = Comision.objects.filter(
        fecha_viaje=fecha_sel,
        estado='ENTREGADO'
    ).order_by('-id')

    total_cobrado = entregadas.filter(pagado=True).aggregate(Sum('monto'))['monto__sum'] or 0
    total_pendiente_cobro = Comision.objects.filter(
        fecha_viaje=fecha_sel,
        pagado=False
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    deudores_count = Comision.objects.filter(pagado=False, monto__gt=0).values('cliente').distinct().count()
    pendientes_lista = agregar_links_gps(pendientes)
    url_ruta_completa = construir_url_ruta_completa(pendientes_lista)
    consejo_ia = request.session.pop('consejo_ia', None)

    contexto = {
        'hoy': hoy,
        'ayer': ayer,
        'manana': manana,
        'fecha_sel': fecha_sel,
        'pendientes': pendientes_lista,
        'entregadas': entregadas,
        'total_cobrado': total_cobrado,
        'total_pendiente_cobro': total_pendiente_cobro,
        'deudores_count': deudores_count,
        'url_ruta_completa': url_ruta_completa,
        'consejo_ia': consejo_ia,
    }
    return render(request, 'envios/hoja_ruta.html', contexto)


def optimizar_ruta_ia(request):
    fecha_sel = obtener_fecha_seleccionada(request)
    if request.method == 'POST':
        pendientes = list(Comision.objects.filter(fecha_viaje=fecha_sel, estado='PENDIENTE'))
        indicacion = request.POST.get('indicacion', '').strip() or "Entrando por Av. Ovidio Lagos (Ruta A012)"

        if len(pendientes) > 1 and settings.GEMINI_API_KEY:
            lista_paradas = [
                {
                    "id": p.id,
                    "dir": p.direccion_entrega,
                    "cliente": p.cliente.nombre,
                    "hora": p.hora_limite.strftime("%H:%M") if p.hora_limite else None
                }
                for p in pendientes
            ]

            prompt = f"""Sos un asistente logístico experto en Rosario, Santa Fe, Argentina.
Indicación del chofer para hoy: "{indicacion}".
Ordená estas paradas en un recorrido lógico y continuo sin volver atrás, respetando:
1. Primero las paradas que tienen 'hora' límite (de más temprana a más tardía).
2. El resto según el punto de ingreso indicado por el chofer (ej: si entra por Ovidio Lagos / A012 viene desde el Sur/Suroeste hacia el Centro; si entra por Soldini / Ruta 14 viene por Av. Uriburu / Batlle y Ordóñez; si entra por Ruta 33 viene por Av. Pte. Perón desde el Oeste).
Paradas: {json.dumps(lista_paradas, ensure_ascii=False)}
Devolvé exclusivamente este JSON: {{"ids": [id1, id2], "consejo": "1 oración corta explicando el recorrido armado sin asteriscos."}}"""

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            exito = False

            for modelo in ['gemini-2.5-flash-lite', 'gemini-2.5-flash', 'gemini-3.5-flash-lite', 'gemini-3.8-flash']:
                try:
                    response = client.models.generate_content(
                        model=modelo,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.0,
                        ),
                    )
                    resultado = json.loads(response.text)
                    ids_ordenados = resultado.get("ids") or resultado.get("ids_ordenados") or []

                    if ids_ordenados:
                        mapa_pos = {int(cid): pos for pos, cid in enumerate(ids_ordenados, start=1)}
                        for p in pendientes:
                            if p.id in mapa_pos:
                                p.orden_ia = mapa_pos[p.id]
                        Comision.objects.bulk_update(pendientes, ['orden_ia'])

                        request.session['consejo_ia'] = resultado.get("consejo", "Ruta ordenada según tu indicación.").replace('**', '')
                        exito = True
                        break
                except Exception:
                    continue

            if not exito:
                import re
                def extraer_altura(dir_texto):
                    numeros = re.findall(r'\d+', dir_texto or '')
                    return int(numeros[0]) if numeros else 0

                pendientes_orden_local = sorted(
                    pendientes,
                    key=lambda p: (
                        0 if p.hora_limite else 1,
                        p.hora_limite or timezone.now().time().max,
                        -extraer_altura(p.direccion_entrega)
                    )
                )
                for pos, p in enumerate(pendientes_orden_local, start=1):
                    p.orden_ia = pos
                Comision.objects.bulk_update(pendientes_orden_local, ['orden_ia'])
                request.session['consejo_ia'] = "Ruta ordenada automáticamente por horarios y alturas."

    return redirigir_con_fecha(fecha_sel)


def nueva_comision(request):
    clientes = Cliente.objects.all().order_by('nombre')
    fecha_sel = obtener_fecha_seleccionada(request)

    if request.method == 'POST':
        nombre_cliente = request.POST.get('cliente_nombre', '').strip()
        direccion = request.POST.get('direccion_entrega', '').replace(' .', '.').strip()
        cliente_obj, _ = Cliente.objects.get_or_create(nombre=nombre_cliente)

        # Guarda la ultima direccion usada como direccion habitual del cliente
        if direccion and cliente_obj.direccion_frecuente != direccion:
            cliente_obj.direccion_frecuente = direccion
            cliente_obj.save()

        hora = request.POST.get('hora_limite') or None
        monto = request.POST.get('monto') or 0

        Comision.objects.create(
          fecha_viaje=fecha_sel,
          cliente=cliente_obj,
          destinatario_rosario=request.POST.get('destinatario_rosario', '').strip(),
          tipo=request.POST.get('tipo', 'SOBRE'),
          direccion_entrega=direccion,
          hora_limite=hora,
          monto=monto,
          pagado=('pagado' in request.POST),
          notas=request.POST.get('notas', '')
      )
        return redirigir_con_fecha(fecha_sel)

    clientes_dirs = {c.nombre: (c.direccion_frecuente or '') for c in clientes}

    return render(request, 'envios/nueva_comision.html', {
        'clientes': clientes,
        'clientes_dirs_json': json.dumps(clientes_dirs, ensure_ascii=False),
        'fecha_sel': fecha_sel,
        'editando': False,
    })


def editar_comision(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    clientes = Cliente.objects.all().order_by('nombre')

    if request.method == 'POST':
        nombre_cliente = request.POST.get('cliente_nombre', '').strip()
        direccion = request.POST.get('direccion_entrega', '').replace(' .', '.').strip()
        cliente_obj, _ = Cliente.objects.get_or_create(nombre=nombre_cliente)

        if direccion and cliente_obj.direccion_frecuente != direccion:
            cliente_obj.direccion_frecuente = direccion
            cliente_obj.save()

        comision.fecha_viaje = obtener_fecha_seleccionada(request)
        comision.cliente = cliente_obj
        comision.tipo = request.POST.get('tipo', 'SOBRE')
        comision.direccion_entrega = direccion
        comision.hora_limite = request.POST.get('hora_limite') or None
        comision.monto = request.POST.get('monto') or 0
        comision.pagado = ('pagado' in request.POST)
        comision.notas = request.POST.get('notas', '')
        comision.destinatario_rosario = request.POST.get('destinatario_rosario', '').strip()
        comision.save()
        return redirigir_con_fecha(comision.fecha_viaje)

    clientes_dirs = {c.nombre: (c.direccion_frecuente or '') for c in clientes}

    return render(request, 'envios/nueva_comision.html', {
        'clientes': clientes,
        'clientes_dirs_json': json.dumps(clientes_dirs, ensure_ascii=False),
        'comision': comision,
        'fecha_sel': comision.fecha_viaje,
        'editando': True,
    })


def posponer_manana(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    fecha_anterior = comision.fecha_viaje
    if request.method == 'POST':
        comision.fecha_viaje = fecha_anterior + timedelta(days=1)
        comision.orden_ia = 99
        comision.save()
        request.session['consejo_ia'] = f"Parada en {comision.direccion_entrega} pasada al {comision.fecha_viaje.strftime('%d/%m')}."
    return redirigir_con_fecha(fecha_anterior)

def devolver_hoy(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    fecha_actual_vista = obtener_fecha_seleccionada(request)
    if request.method == 'POST':
        comision.fecha_viaje = timezone.localdate()
        comision.orden_ia = 99
        comision.save()
        request.session['consejo_ia'] = f"Parada en {comision.direccion_entrega} devuelta a la ruta de hoy."
    return redirigir_con_fecha(fecha_actual_vista)

def eliminar_comision(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    fecha_viaje = comision.fecha_viaje
    if request.method == 'POST':
        comision.delete()
    return redirigir_con_fecha(fecha_viaje)

def marcar_entregado(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    if request.method == 'POST':
        comision.estado = 'ENTREGADO'
        if 'cobrado' in request.POST:
            comision.pagado = True
        comision.save()
    return redirigir_con_fecha(comision.fecha_viaje)

def devolver_hoy(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    fecha_actual_vista = obtener_fecha_seleccionada(request)
    if request.method == 'POST':
        comision.fecha_viaje = timezone.localdate()
        comision.orden_ia = 99
        comision.save()
        request.session['consejo_ia'] = f"Parada en {comision.direccion_entrega} devuelta a la ruta de hoy."
    return redirigir_con_fecha(fecha_actual_vista)


def eliminar_comision(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    fecha_viaje = comision.fecha_viaje
    if request.method == 'POST':
        comision.delete()
    return redirigir_con_fecha(fecha_viaje)


def marcar_entregado(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    if request.method == 'POST':
        comision.estado = 'ENTREGADO'
        if 'cobrado' in request.POST:
            comision.pagado = True
        comision.save()
    return redirigir_con_fecha(comision.fecha_viaje)


def cuentas_corrientes(request):
    impagas = Comision.objects.filter(pagado=False, monto__gt=0).select_related('cliente').order_by('cliente__nombre', 'fecha_viaje')
    agrupado = {}
    total_general = 0
    for c in impagas:
        cid = c.cliente.id
        if cid not in agrupado:
            agrupado[cid] = {
                'cliente': c.cliente,
                'total_deuda': 0,
                'cantidad': 0,
                'comisiones': []
            }
        agrupado[cid]['total_deuda'] += c.monto
        agrupado[cid]['cantidad'] += 1
        agrupado[cid]['comisiones'].append(c)
        total_general += c.monto

    deudores = sorted(agrupado.values(), key=lambda x: x['total_deuda'], reverse=True)
    return render(request, 'envios/cuentas_corrientes.html', {
        'deudores': deudores,
        'total_general': total_general,
    })

def saldar_cliente(request, cliente_id):
    if request.method == 'POST':
        Comision.objects.filter(cliente_id=cliente_id, pagado=False).update(pagado=True)
    return redirect('cuentas_corrientes')

def mapa_ruta_completa(request):
    fecha_sel = obtener_fecha_seleccionada(request)
    pendientes = list(Comision.objects.filter(
        fecha_viaje=fecha_sel,
        estado='PENDIENTE'
    ).order_by('orden_ia', F('hora_limite').asc(nulls_last=True), 'id'))

    puntos = []
    if pendientes and settings.GEMINI_API_KEY:
        lista_dirs = [{"orden": i + 1, "direccion": p.direccion_entrega, "cliente": p.cliente.nombre} for i, p in enumerate(pendientes)]
        prompt = f"""
        Devolve un JSON con las coordenadas aproximadas (lat, lng) en Rosario, Santa Fe, Argentina para estas direcciones:
        {json.dumps(lista_dirs, ensure_ascii=False)}
        Formato exacto:
        {{"puntos": [{{"orden": 1, "direccion": "...", "cliente": "...", "lat": -32.95, "lng": -60.65}}]}}
        """
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        for modelo in ['gemini-3.8-flash-lite', 'gemini-2.5-flash-lite']:
            try:
                resp = client.models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
                )
                puntos = json.loads(resp.text).get("puntos", [])
                if puntos:
                    break
            except Exception:
                continue

    return render(request, 'envios/mapa_ruta.html', {
        'fecha_sel': fecha_sel,
        'puntos_json': json.dumps(puntos, ensure_ascii=False),
        'pendientes': pendientes,
    })

def historial_cobrado(request):
    cobradas = Comision.objects.filter(pagado=True, monto__gt=0).select_related('cliente').order_by('-fecha_viaje', '-id')

    dias_dict = {}
    total_general = 0

    for c in cobradas:
        fecha_key = c.fecha_viaje
        if fecha_key not in dias_dict:
            dias_dict[fecha_key] = {
                'fecha': fecha_key,
                'subtotal': 0,
                'items': []
            }
        dias_dict[fecha_key]['subtotal'] += c.monto
        dias_dict[fecha_key]['items'].append(c)
        total_general += c.monto

    historial_dias = list(dias_dict.values())

    return render(request, 'envios/cobrado.html', {
        'historial_dias': historial_dias,
        'total_general': total_general,
    })



