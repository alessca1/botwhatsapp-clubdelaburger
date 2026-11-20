# Bot de WhatsApp — El Club de la Burger

Bot que responde consultas las 24 h y toma pedidos por WhatsApp para el número **264 317-9759** (`+54 9 264 317-9759`), con stock por noche de servicio y un panel para el local. Usa FastAPI, webhook de Zernio con firma HMAC, cola de eventos y bandeja de salida con un único worker, datos cifrados con Fernet, PostgreSQL en Render y DeepSeek para interpretar texto libre.

## Estado: cuestionario aplicado

La carta, los horarios y las respuestas del cuestionario `docs/Cuestionario bot WhatsApp - El Club de la Burger.docx` ya están cargados en `content/negocio.json` y `content/carta.json`:

- **Número del local:** 264 317-9759 (`5492643179759`).
- **Días:** miércoles a lunes. Martes cerrado. Abren feriados.
- **Horario:** 20:00 a 01:00. Último pedido 00:50.
- **Pedidos anticipados:** desde las 10:00. Primer retiro 20:00; primer delivery 20:30. También se puede pedir para otro día (hasta 30 días).
- **Carta:** 11 burgers con precio **simple, doble y triple**, papas, extras y dips. El medallón extra cuesta $2.000.
- **Combos:** solo viernes, sábado y domingo. Se elige cualquier variedad menos Crispy Chicken y el dip (mayo de ajo, barbacoa o mostaza miel). No incluyen bebida.
- **Delivery:** propio, hasta 10 km a la redonda. Costo por km; sin pedido mínimo y sin envío gratis. Demora habitual 15-20 min. Retiro en el salón, 10 min.
- **Pagos:** efectivo y transferencia. Alias `Mathias1998`, titular `Mathias De Dios Juarez Fa.`. Los pedidos anticipados se abonan antes. Llevan cambio. No hacen factura; tarjeta tiene recargo.
- **Cancelaciones:** el cliente puede cancelar sin costo dentro de los 10 minutos. Después pasa a una persona.
- **Atención humana:** Gabriel y Mathias desde las 19:00. Si alguien responde desde el celular o el panel, el bot se calla **5 minutos** y después retoma solo.
- **Salón:** 50 personas, con reserva por WhatsApp. Eventos y cumpleaños sí; catering fuera del local no.
- **Preguntas frecuentes:** sin TACC no; opción vegetariana sí (deriva para coordinar); Mexicana con picante opcional; se pueden cambiar ingredientes; no hay menú de chicos; juegos, tragos, happy hour, estacionamiento, mascotas y chicos hasta las 00:45.

Los precios y el stock se pueden editar desde el panel sin tocar el código.

## Qué hace

- **Consultas 24/7:** horarios, dirección, delivery/retiro, pagos, combos, carta con precios, salón, reservas, eventos y preguntas frecuentes. Lo que no está confirmado no lo inventa: deriva a una persona (🙋 en el panel).
- **Pedidos:** productos con tamaño (simple/doble/triple), aclaraciones ("2 American doble sin cebolla"), delivery o retiro, dirección, nombre, día, franja horaria y forma de pago → resumen → confirmación.
- **Pre-pedidos:** desde `intake_open` (10:00) para la noche actual o para otro día. El cliente elige retiro desde 20:00 o delivery desde 20:30. Durante el servicio ofrece "lo antes posible".
- **Noche de servicio en hora de San Juan:** 00:30 del jueves pertenece al servicio del miércoles. Martes cerrado.
- **Stock:** cada producto tiene una receta de insumos (medallones, panes, pollo, papas, nuggets, dips). Antes de confirmar se verifica y reserva en una sola transacción; si algo se agotó mientras el cliente elegía, se avisa y se saca del pedido. Cancelar devuelve el stock.
- **Combos** solo viernes, sábado y domingo (por fecha de servicio).
- **Productos sin precio no se venden.** Se puede pausar un producto desde el panel con "Disponible".
- **Avisos al cliente** cuando el local cambia el estado (confirmado con costo de envío, en cocina, listo, en camino, entregado, cancelado).
- **Intervención humana:** si alguien responde desde el celular (coexistencia) o desde el panel, el bot se calla `HUMAN_PAUSE_MINUTES` (5) y después retoma solo.
- **Sin DeepSeek** (o al pasar `DEEPSEEK_DAILY_CAP`) sigue funcionando con reglas y la carta numerada ("2 de la 8").

## Panel

`https://<servicio>.onrender.com/panel` — entrar con `ADMIN_TOKEN`.
Pestañas: **Pedidos** (tablero por estado, aviso sonoro, cargar costo de envío), **Stock** (inicial, usado, quedan, "Agotado", valor por defecto de cada noche), **Carta** (precios simple/doble/triple y disponibilidad), **Chats** (responder, pausar/reactivar bot, marcar resuelto).

## Despliegue actual (20/11/2026)

- **Servicio Render:** `clubdelaburger-whatsapp`
- **URL:** https://clubdelaburger-whatsapp.onrender.com
- **Panel:** https://clubdelaburger-whatsapp.onrender.com/panel (la clave está en Render → servicio → Environment → `ADMIN_TOKEN`)
- **Base:** `clubdelaburger-db` (Postgres, Virginia)
- **Zernio:** cuenta `6a8e6dfb77555aae018cfef0` — WhatsApp **+54 9 264 317-9759**
- **Webhook Zernio:** `6b005471045434d0c3a89322` → `https://clubdelaburger-whatsapp.onrender.com/webhooks/zernio`
- **Repositorio:** https://github.com/alessca1/botwhatsapp-clubdelaburger

## Despliegue en Render + Zernio (número 264 317-9759)

1. Subir este proyecto a un repo nuevo y crear el Blueprint con `render.yaml` (servicio `clubdelaburger-whatsapp` + base `clubdelaburger-db`).
2. Cargar en Render las variables secretas:
   - `DATA_ENCRYPTION_KEY` (Fernet)
   - `DEEPSEEK_API_KEY`
   - `ZERNIO_API_KEY`
   - `ZERNIO_ACCOUNT_ID` — cuenta de Zernio del WhatsApp **264 317-9759**
   - `ZERNIO_WEBHOOK_SECRET`
   - `ADMIN_TOKEN` lo genera Render.
3. En Zernio, conectar el número **264 317-9759** y crear el webhook:
   - URL: `https://<servicio>.onrender.com/webhooks/zernio`
   - Eventos: `message.received`, `message.sent`, `message.failed`, `conversation.control_changed`
4. Verificar que la cuenta de Zernio sea la del número:
   ```powershell
   .venv\Scripts\python -m app.preflight
   ```
   Debe devolver `connected: true` y el `account_id` igual a `ZERNIO_ACCOUNT_ID`.
5. Dejar `BOT_ENABLED=true` y probar un pedido completo desde otro número. `/admin/status` tiene que devolver `bot_enabled: true`, `whatsapp_configured: true` y `business_number: 5492643179759`.

Si falta `ZERNIO_API_KEY`, `ZERNIO_ACCOUNT_ID` o `ZERNIO_WEBHOOK_SECRET`, el bot no puede enviar ni recibir mensajes: la aplicación queda levantada pero `BOT_ENABLED=true` exige esas credenciales al arrancar.

## Desarrollo

```powershell
$env:PYTHONHOME = $null
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
```

Las 43 pruebas cubren: ventana de pedidos (00:30, 00:50, 00:51, 09:59, 10:00, martes), combos solo fin de semana, combos para otro día, cambio de día con el carrito armado, variantes simple/doble/triple, productos sin precio, pedido completo por webhook firmado, stock que se agota a mitad del pedido, cancelación dentro y después de 10 minutos, pedido anticipado para otro día, pausa y reanudación por respuesta humana, envío a Zernio y ventana de 24 h.

El contenido se importa una sola vez con el marcador `seed:2026-11-20`; después se edita desde el panel o `PUT /admin/content`. Si se cambia `content/negocio.json` o `content/carta.json`, hay que subir el marcador de versión o editar desde el panel, para no pisar cambios hechos por el local.

## Límites conocidos

- WhatsApp solo permite escribirle al cliente hasta 24 h después de su último mensaje. Los pedidos para otro día se confirman en el momento; los avisos de estado muy posteriores pueden quedar vencidos.
- Los pedidos de PedidosYa y del salón no pasan por el bot: si comparten stock hay que descontarlos en el panel.
- El bot no lee comprobantes: marca "comprobante recibido" y lo verifica una persona.
- Las bebidas no tienen precio cargado en la carta: el bot consulta con una persona antes de venderlas.
- El cuestionario cargó **10 porciones de papas por noche**; cada burger descuenta 1. Si en realidad son 10 bolsas/kilos, ajustar el stock inicial desde el panel para que no se agote después de 10 burgers.
- No hay plantilla de WhatsApp para reabrir conversaciones vencidas.
