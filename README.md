# Bot de WhatsApp — El Club de la Burger

Bot que responde consultas las 24 h y toma pedidos por WhatsApp para el número **264 317-9759** (`+54 9 264 317-9759`), con stock por noche de servicio y un panel para el local. Usa FastAPI, webhook de Zernio con firma HMAC, cola de eventos y bandeja de salida con un único worker, datos cifrados con Fernet, PostgreSQL en Render y DeepSeek para interpretar texto libre.

## Estado: cuestionario aplicado

La carta, los horarios y las respuestas del cuestionario `docs/Cuestionario bot WhatsApp - El Club de la Burger.docx` ya están cargados en `content/negocio.json` y `content/carta.json`:

- **Número del local:** 264 317-9759 (`5492643179759`).
- **Días:** miércoles a lunes. Martes cerrado. Abren feriados.
- **Horario:** 20:00 a 01:00. Último pedido 00:50.
- **Pedidos anticipados:** desde las 10:00. Primer retiro 20:00; primer delivery 20:30. También se puede pedir para otro día (hasta 30 días).
- **Carta:** 11 burgers con precio **simple, doble y triple**, papas, extras, dips y bebidas. El medallón extra cuesta $2.000. Las bebidas están creadas en la carta pero **sin precio**: se activan cargándoles el precio desde el panel.
- **Combos:** solo viernes, sábado y domingo. Se elige cualquier variedad menos Crispy Chicken y el dip (mayo de ajo, barbacoa o mostaza miel). No incluyen bebida.
- **Ticket promedio:** al agregar una burger, el bot y el carrito ofrecen completar el combo con bebida y dip (o con los dips disponibles si todavía no hay bebidas con precio).
- **Combo sugerido:** el carrito detecta 1 triple, 2 dobles o 4 o más dobles y muestra el combo que más ahorra, comparado contra las burgers que ya están cargadas. Al aceptar, reemplaza esas burgers por el combo y agrega los sabores y dips elegidos al mensaje de WhatsApp.
- **Promos con vigencia:** cada promo tiene combo, descuento, inicio, fin y estado. Se muestra primera en `/carta` con cronómetro; al vencer desaparece y el bot deja de aplicarla. La promo activa también permite pedir el combo fuera de sus días normales.
- **Delivery:** propio, hasta 10 km a la redonda. Costo por km; sin pedido mínimo y sin envío gratis. Demora habitual 15-20 min. Retiro en el salón, 10 min.
- **Pagos:** efectivo y transferencia. Alias `Mathias1998`, titular `Mathias De Dios Juarez Fa.`. Los pedidos anticipados se abonan antes. Llevan cambio. No hacen factura; tarjeta tiene recargo. Si se configuran las credenciales de Mercado Pago, el bot suma el link de pago automático y verifica la acreditación por webhook.
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

## Carta pública

https://clubdelaburger-whatsapp.onrender.com/carta

El bot manda este link cuando piden la carta (`carta`, `menú`, `precios`) y desde el botón "Ver carta" del saludo. La página lee precios, variantes, disponibilidad y combos desde la base (`/public/menu`), así que se actualiza sola cuando cambia el panel. El tema visual vive en `static/carta-nocturna.css`: Bricolage Grotesque para los títulos, Chivo para el texto y Chivo Mono para precios y datos.

- **Fotos por burger:** Panel → Carta → **Foto de la carta**. Se puede subir JPG, PNG o WebP; el panel la comprime y la guarda en Postgres. Se publica al instante en `/carta`.
- Mientras no haya foto, la página muestra una ilustración editorial de los ingredientes con la etiqueta "foto pendiente". Cuando subís la foto real, la reemplaza.
- Las fotos se sirven en `/menu/image/{producto}` y sobreviven a los deploys porque están en la base, no en el disco de Render.
- **Carrito:** elegís tamaño, extras con costo, ingredientes para sacar y cantidad. El carrito queda guardado en el navegador y el botón de WhatsApp manda el pedido armado en un formato que el bot entiende (`2 x Burger American Doble, sin cebolla`, `2 x Extra medallón`). El bot vuelve a calcular precios y stock; el total de la página es de referencia.
- **Upsell:** debajo del carrito aparece **Sumá algo más** con bebidas, dips y extras disponibles. Se suma con un toque, sin volver a la carta. En la personalización de cada burger también aparecen bebidas y dips.
- **Combo sugerido:** con las burgers cargadas, el carrito calcula el combo que más ahorra y muestra el precio, el ahorro y los selectores de dips. Al aceptar, el mensaje lleva las elecciones del combo separadas para que el bot no las tome como burgers extra.
- **Promos:** el banner muestra el precio con descuento, el precio anterior tachado, un cronómetro grande y una barra de tiempo. A los 30 minutos cambia a estado urgente; a los 10 minutos pasa a **Últimos minutos** con pulso y borde tomate. El contador se actualiza cada segundo y la promo se retira sola al vencer.
- **Promo primero:** si hay una promo activa, aparece arriba de todo antes del hero, con precio anterior, precio promo y cronómetro en vivo. Si hay varias, se ordenan por prioridad y se pueden deslizar.
- **Promo en el carrito:** el combo sugerido toma el precio promo mientras esté vigente; si la promo vence, la línea de promo se quita del carrito y el bot ya no la aplica.

## Panel

`https://<servicio>.onrender.com/panel` — entrar con `ADMIN_TOKEN`.
Pestañas: **Pedidos** (tablero por estado, aviso sonoro, cargar costo de envío), **Stock** (ledger por noche, inicial, usado, quedan, "Agotado", valor por defecto), **Carta** (promos con vigencia, alta/edición/baja de productos, precios simple/doble/triple, disponibilidad y **foto de cada producto** en tarjetas), **Chats** (bandeja con avatares, burbujas, responder, pausar/reactivar bot, marcar resuelto y **borrar el chat del panel**).

Los productos nuevos se crean con un precio único, categoría, días, receta de insumos y palabras clave. Los productos con variantes siguen editando simple/doble/triple desde la tarjeta. Las bajas quedan registradas y no se recrean al reiniciar el servicio.

Las promos se crean desde **Carta → Promociones con contador**: nombre, combo, descuento, inicio, fin y activa/pausada. Todo se puede editar o borrar.

## Pagos automáticos con Mercado Pago

El bot puede generar un **link de pago de Mercado Pago Checkout Pro** por cada pedido y mandarlo solo por WhatsApp. Cuando el cliente paga (transferencia bancaria dentro del checkout, saldo de Mercado Pago o tarjeta si se habilitan), Mercado Pago llama al webhook, el bot verifica el pago y lo marca como **Pagado** sin que nadie revise un comprobante.

- Se activa solo si están cargados `MP_ACCESS_TOKEN` y `MP_WEBHOOK_SECRET`. Si faltan, el bot sigue ofreciendo efectivo y transferencia al alias como hasta ahora.
- El método **Mercado Pago** aparece como tercera opción de pago. Al confirmar el pedido, el bot crea la preferencia con el total exacto y `external_reference` = ID del pedido, y manda el `init_point`.
- Por defecto `MP_EXCLUDE_CARDS=true`: el checkout muestra transferencia y saldo de Mercado Pago, no tarjetas, porque el local cobra recargo por tarjeta. Si se quieren aceptar tarjetas, poner `MP_EXCLUDE_CARDS=false` y definir cómo se traslada el recargo.
- El link vence a los `MP_LINK_EXPIRES_MINUTES` minutos (60 por defecto). Desde el panel se puede **Reenviar link** para generar uno nuevo.
- Si el pedido es delivery y todavía no se cargó el costo de envío, el link se genera automáticamente cuando el local carga el envío desde el panel.
- El webhook `POST /webhooks/mercadopago` valida `x-signature`, consulta el pago en la API (`GET /v1/payments/{id}`), compara monto y moneda, y es idempotente: un pago repetido no vuelve a marcar ni a avisar.
- Estados que se muestran en el panel: **Pagado**, **Pago pendiente**, **A cobrar**, **Pago rechazado**, **Reembolsado** y **Revisar pago** (monto o moneda que no coinciden, pago duplicado o pago sobre un pedido cancelado).
- Las transferencias al alias siguen siendo manuales: el cliente manda el comprobante, el panel muestra **Comprobante** y el local usa **Marcar pagado**.

Para conectarlo:

1. Crear una aplicación en [Tus integraciones de Mercado Pago](https://www.mercadopago.com.ar/developers/panel/app) y obtener el **Access Token** de producción.
2. En **Webhooks**, configurar la URL `https://clubdelaburger-whatsapp.onrender.com/webhooks/mercadopago` y el evento **Pagos**. Copiar la **clave secreta**.
3. Cargar en Render: `MP_ACCESS_TOKEN`, `MP_WEBHOOK_SECRET` y `PUBLIC_BASE_URL` (si el dominio cambia).
4. Probar un pedido real de bajo monto con transferencia y verificar que el pedido pase a **Pagado** y que el cliente reciba el aviso.

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
   - `MP_ACCESS_TOKEN` y `MP_WEBHOOK_SECRET` (opcionales; activan el link de pago automático)
   - `PUBLIC_BASE_URL` — por defecto `https://clubdelaburger-whatsapp.onrender.com`
   - `ADMIN_TOKEN` lo genera Render.
3. En Zernio, conectar el número **264 317-9759** y crear el webhook:
   - URL: `https://<servicio>.onrender.com/webhooks/zernio`
   - Eventos: `message.received`, `message.sent`, `message.failed`, `conversation.control_changed`
4. Si se usa Mercado Pago, en **Tus integraciones → Webhooks** configurar:
   - URL: `https://<servicio>.onrender.com/webhooks/mercadopago`
   - Evento: **Pagos**
   - Copiar la clave secreta a `MP_WEBHOOK_SECRET`.
5. Verificar que la cuenta de Zernio sea la del número:
   ```powershell
   .venv\Scripts\python -m app.preflight
   ```
   Debe devolver `connected: true` y el `account_id` igual a `ZERNIO_ACCOUNT_ID`.
6. Dejar `BOT_ENABLED=true` y probar un pedido completo desde otro número. `/admin/status` tiene que devolver `bot_enabled: true`, `whatsapp_configured: true` y `business_number: 5492643179759`.

Si falta `ZERNIO_API_KEY`, `ZERNIO_ACCOUNT_ID` o `ZERNIO_WEBHOOK_SECRET`, el bot no puede enviar ni recibir mensajes: la aplicación queda levantada pero `BOT_ENABLED=true` exige esas credenciales al arrancar.

## Desarrollo

```powershell
$env:PYTHONHOME = $null
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
```

Las pruebas cubren: ventana de pedidos (00:30, 00:50, 00:51, 09:59, 10:00, martes), combos solo fin de semana, combos para otro día, cambio de día con el carrito armado, variantes simple/doble/triple, productos sin precio, bebida con precio publicada como `drink`, respuesta de bebidas con precios, upsell de bebida + dip al agregar una burger, alta/edición/baja de productos desde el panel, baja permanente de productos sembrados entre reinicios, alta/edición/baja de promos, promo activa en carta pública, combo fuera de sus días con promo, promo vencida que no aplica, pedido con elecciones de combo sin burgers extra, varias elecciones de combo en orden, pedido completo por webhook firmado, stock que se agota a mitad del pedido, cancelación dentro y después de 10 minutos, pedido anticipado para otro día, pausa y reanudación por respuesta humana, envío a Zernio, carta pública, link de carta desde el bot, mensaje de carrito compatible con el parser y pedido con extras, subida y borrado de fotos en la base, borrado de chats sin borrar el pedido, ventana de 24 h, link de Mercado Pago automático, webhook firmado e idempotente, monto incorrecto, envío pendiente hasta cargar la tarifa y marcado manual de una transferencia al alias.

El contenido se importa una sola vez con el marcador `seed:2026-11-20-carta-v1`; después se edita desde el panel o `PUT /admin/content`. Si se cambia `content/negocio.json` o `content/carta.json`, hay que subir el marcador de versión o editar desde el panel, para no pisar cambios hechos por el local.

## Límites conocidos

- WhatsApp solo permite escribirle al cliente hasta 24 h después de su último mensaje. Los pedidos para otro día se confirman en el momento; los avisos de estado muy posteriores pueden quedar vencidos.
- Los pedidos de PedidosYa y del salón no pasan por el bot: si comparten stock hay que descontarlos en el panel.
- El bot no lee comprobantes de transferencia al alias: marca "comprobante recibido" y lo verifica una persona. Los pagos con link de Mercado Pago sí se verifican solos por webhook.
- Las bebidas están creadas en la carta pero sin precio: hasta cargarlas desde el panel, el bot consulta con una persona antes de venderlas y el carrito muestra solo dips/extras en el upsell.
- El combo sugerido aparece siempre que haya burgers compatibles. Si el día actual no es día de combo, la tarjeta aclara los días válidos (viernes a domingo) para que el cliente lo pida para uno de esos días.
- La promo se aplica a pedidos hechos dentro de su ventana de inicio/fin, aunque sean para otro día. Si la promo vence antes de confirmar el pedido, el bot ya no la aplica.
- El cuestionario cargó **10 porciones de papas por noche**; cada burger descuenta 1. Si en realidad son 10 bolsas/kilos, ajustar el stock inicial desde el panel para que no se agote después de 10 burgers.
- No hay plantilla de WhatsApp para reabrir conversaciones vencidas.
