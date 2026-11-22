import json
from datetime import date, timedelta
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.db.models import F, Sum
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


def hoja_de_ruta(request):
    hoy = timezone.localdate()
    manana = hoy + timedelta(days=1)
    fecha_sel = obtener_fecha_seleccionada(request)

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

    consejo_ia = request.session.pop('consejo_ia', None)

    contexto = {
        'hoy': hoy,
        'manana': manana,
        'fecha_sel': fecha_sel,
        'pendientes': pendientes,
        'entregadas': entregadas,
        'total_cobrado': total_cobrado,
        'total_pendiente_cobro': total_pendiente_cobro,
        'consejo_ia': consejo_ia,
    }
    return render(request, 'envios/hoja_ruta.html', contexto)


def optimizar_ruta_ia(request):
    fecha_sel = obtener_fecha_seleccionada(request)
    if request.method == 'POST':
        pendientes = list(Comision.objects.filter(fecha_viaje=fecha_sel, estado='PENDIENTE'))

        if len(pendientes) > 1 and settings.GEMINI_API_KEY:
            lista_paradas = []
            for p in pendientes:
                lista_paradas.append({
                    "id": p.id,
                    "direccion": p.direccion_entrega,
                    "hora_limite": p.hora_limite.strftime("%H:%M") if p.hora_limite else "Libre",
                    "notas": p.notas or ""
                })

            prompt = f"""
            Sos un despachante experto en las calles y alturas de Rosario, Santa Fe, Argentina.
            Un comisionista viene desde Firmat entrando a Rosario por Ruta 33 (Av. Presidente Peron y Circunvalacion, altura 6900 al Oeste).

            REGLAS GEOGRAFICAS OBLIGATORIAS DE ROSARIO (100 numeros = 1 cuadra):
            1. Numeracion Este-Oeste (ej: Cordoba, Pellegrini, Av. Pte. Peron): crece hacia el OESTE desde el rio.
               - Altura 6000-7000 = Extremo Oeste / Circunvalacion / Ruta 33 (ej: Av. Pres. Peron 6970 es la PRIMERA parada al entrar desde Firmat).
               - Altura 3000-4500 = Oeste / Echesortu / Parque Independencia (ej: Pellegrini 3053, San Nicolas 1450).
               - Altura 100-2000 = Centro y Macrocentro (ej: Cordoba 1852, Juan Manuel de Rosas 957).
            2. Numeracion Norte-Sur (ej: Espana, Corrientes, San Martin, San Nicolas, Juan Manuel de Rosas): crece hacia el SUR desde el rio.
               - Altura 100-1200 = Centro / Rio (ej: Corrientes 999, Juan Manuel de Rosas 957).
               - Altura 1300-2000 = Macrocentro (ej: Espana 1715).
               - Altura 2100-3500 = Zona Sur / Abasto (ej: Espana 2730, Av. San Martin 2150).

            ORDEN DE PRIORIDAD:
            - Primero van obligatoriamente las entregas que tengan 'hora_limite' distinta de 'Libre' (ordenadas de mas temprana a mas tardia).
            - Luego ordena el resto haciendo un recorrido continuo sin volver atras: entra por Oeste (alturas 6000 -> 3000), baja a Zona Sur (alturas 2000-3000 Norte-Sur), sube por Macrocentro (1500-2000) y cierra en Centro/Rio (800-1200).

            Entregas del dia:
            {json.dumps(lista_paradas, ensure_ascii=False)}

            Devolve un JSON con este formato exacto:
            {{
                "ids_ordenados": [id1, id2, ...],
                "consejo": "Explicacion corta de 2 oraciones sin asteriscos ni negritas sobre el recorrido."
            }}
            """

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            exito = False

            for modelo in ['gemini-3.8-flash', 'gemini-3.8-flash-lite', 'gemini-2.5-flash-lite']:
                try:
                    response = client.models.generate_content(
                        model=modelo,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1,
                        ),
                    )
                    resultado = json.loads(response.text)
                    ids_ordenados = resultado.get("ids_ordenados", [])

                    if ids_ordenados:
                        for posicion, comision_id in enumerate(ids_ordenados, start=1):
                            Comision.objects.filter(id=int(comision_id)).update(orden_ia=posicion)

                        consejo_limpio = resultado.get("consejo", "Ruta ordenada por zonas geograficas reales.").replace('**', '')
                        request.session['consejo_ia'] = consejo_limpio
                        exito = True
                        break
                except Exception:
                    continue

            if not exito:
                request.session['consejo_ia'] = "No se pudo conectar con Gemini en este momento. Intenta de nuevo en unos segundos."

    return redirigir_con_fecha(fecha_sel)


def nueva_comision(request):
    clientes = Cliente.objects.all().order_by('nombre')
    fecha_sel = obtener_fecha_seleccionada(request)

    if request.method == 'POST':
        nombre_cliente = request.POST.get('cliente_nombre', '').strip()
        cliente_obj, _ = Cliente.objects.get_or_create(nombre=nombre_cliente)

        hora = request.POST.get('hora_limite') or None
        monto = request.POST.get('monto') or 0

        Comision.objects.create(
            fecha_viaje=fecha_sel,
            cliente=cliente_obj,
            tipo=request.POST.get('tipo', 'SOBRE'),
            direccion_entrega=request.POST.get('direccion_entrega'),
            hora_limite=hora,
            monto=monto,
            pagado=('pagado' in request.POST),
            notas=request.POST.get('notas', '')
        )
        return redirigir_con_fecha(fecha_sel)

    return render(request, 'envios/nueva_comision.html', {
        'clientes': clientes,
        'fecha_sel': fecha_sel,
        'editando': False,
    })


def editar_comision(request, comision_id):
    comision = get_object_or_404(Comision, id=comision_id)
    clientes = Cliente.objects.all().order_by('nombre')

    if request.method == 'POST':
        nombre_cliente = request.POST.get('cliente_nombre', '').strip()
        cliente_obj, _ = Cliente.objects.get_or_create(nombre=nombre_cliente)

        comision.fecha_viaje = obtener_fecha_seleccionada(request)
        comision.cliente = cliente_obj
        comision.tipo = request.POST.get('tipo', 'SOBRE')
        comision.direccion_entrega = request.POST.get('direccion_entrega')
        comision.hora_limite = request.POST.get('hora_limite') or None
        comision.monto = request.POST.get('monto') or 0
        comision.pagado = ('pagado' in request.POST)
        comision.notas = request.POST.get('notas', '')
        comision.save()
        return redirigir_con_fecha(comision.fecha_viaje)

    return render(request, 'envios/nueva_comision.html', {
        'clientes': clientes,
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
        request.session['consejo_ia'] = f"Parada en {comision.direccion_entrega} reprogramada para el {comision.fecha_viaje.strftime('%d/%m')}."
    return redirigir_con_fecha(fecha_anterior)

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
