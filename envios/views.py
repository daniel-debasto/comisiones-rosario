import json
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import F, Sum
from django.utils import timezone
from google import genai
from google.genai import types
from .models import Comision, Cliente, Zona


def hoja_de_ruta(request):
    hoy = timezone.localdate()

    pendientes = Comision.objects.filter(
        fecha_viaje=hoy,
        estado='PENDIENTE'
    ).order_by(
        'orden_ia',
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
            lista_paradas = []
            for p in pendientes:
                lista_paradas.append({
                    "id": p.id,
                    "direccion": p.direccion_entrega,
                    "zona": p.zona.nombre if p.zona else "Sin zona",
                    "hora_limite": p.hora_limite.strftime("%H:%M") if p.hora_limite else "Sin horario fijo",
                    "notas": p.notas or ""
                })

            prompt = f"""
            Sos un experto en logística y calles de Rosario, Santa Fe, Argentina.
            Un comisionista viene desde Firmat (ingresando por Ruta 33 / Pérez o por AO12 / Av. Ovidio Lagos) y tiene estas entregas pendientes hoy:
            {json.dumps(lista_paradas, ensure_ascii=False)}

            Ordená los IDs de las entregas para minimizar kilómetros y evitar cruzar la ciudad dos veces, respetando estrictamente las entregas que tienen 'hora_limite' más temprana.
            Devolvé únicamente un JSON con dos claves:
            - "ids_ordenados": lista de enteros con los IDs en el orden óptimo.
            - "consejo": un resumen muy corto (máximo 2 oraciones) explicando por dónde conviene entrar y el orden sugerido.
            """

            try:
                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                resultado = json.loads(response.text)
                ids_ordenados = resultado.get("ids_ordenados", [])

                for posicion, comision_id in enumerate(ids_ordenados, start=1):
                    Comision.objects.filter(id=comision_id).update(orden_ia=posicion)

                request.session['consejo_ia'] = resultado.get("consejo", "Ruta optimizada por cercanía y horarios.")
            except Exception as e:
                request.session['consejo_ia'] = f"No se pudo conectar con la IA en este momento: {str(e)}"

    return redirect('hoja_de_ruta')


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
