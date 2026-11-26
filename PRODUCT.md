# El Club de la Burger — PRODUCT

## Qué es

Hamburguesería de noche en San Juan, Argentina. Av. Libertador 1525 (Oeste). Abre miércoles a lunes de 20:00 a 01:00; martes cerrado. El bot de WhatsApp toma pedidos y consultas 24 h.

## Para quién

Personas de San Juan que piden hamburguesas de noche, en el local o por delivery. Entran desde WhatsApp, casi siempre desde el celular, con hambre y sin ganas de leer formularios.

## Trabajo de la página

Que puedan ver la carta completa, entender los tamaños y precios, y abrir WhatsApp con el pedido ya empezado. La acción principal es **pedir por WhatsApp**. La segunda es **ver la carta**.

## Verdad del producto

- 11 burgers, con variantes simple, doble y triple.
- Precios directos por WhatsApp, no los de PedidosYa.
- Combos solo viernes, sábado y domingo; no incluyen bebida.
- El carrito sugiere el combo que más ahorra según las burgers cargadas, con el ahorro calculado contra esas burgers.
- Las promos tienen combo, descuento, fecha de inicio y fin. Se muestran primeras en `/carta` con cronómetro y el bot las aplica mientras estén vigentes.
- Bebidas cargadas en la carta, con precio a completar desde el panel.
- Delivery propio hasta 10 km, a cotizar por km. Retiro en el salón.
- Efectivo o transferencia. Alias Mathias1998. Link de pago de Mercado Pago opcional, con acreditación automática por webhook.
- Los pedidos anticipados se abonan antes. Se toman desde las 10:00.
- Salón con reserva, 50 personas, tragos, juegos, estacionamiento y eventos.
- Sin TACC no; opción vegetariana sí. La Mexicana puede ir sin picante.

## Tono

Editorial, nocturno, cálido y directo. Como una carta impresa de un club de barrio que sabe que su burger es el centro de la noche. Nada de “la mejor burger de la ciudad” ni promesas inventadas.

## Material

- Datos reales: carta, precios, horarios, dirección, teléfono, alias, reglas de delivery y salón.
- Fotos: **pendientes de carga**. La carta tiene un slot por burger y un cargador en el panel (`Carta → Foto de la carta`). Mientras no haya foto, muestra una ilustración editorial de los ingredientes.
- No hay testimonios, logos de terceros ni métricas inventadas.

## Visual

Fondo noche, crema, lima ácido y tomate. Bricolage Grotesque para la voz grande; Chivo para la información y Chivo Mono para precios y datos. Estructura de carta de club after hours: wordmark con la línea “de la” en barra lima, logo oficial en el topbar y la tarjeta de socio, grilla de platos, banda clara de combos, precios en mono y bloque de instrucciones. El acento es la tipografía y el contraste, no los efectos.

## Restricciones

- Mobile first: la mayoría llega desde WhatsApp.
- Los precios y la disponibilidad salen de la base, nunca hardcodeados.
- El panel permite crear, editar y borrar productos; los cambios se publican al instante.
- El combo sugerido reemplaza las burgers por el combo y manda las elecciones en una línea separada que el bot no interpreta como productos.
- La promo se administra desde Carta; al vencer desaparece de la carta y deja de aplicarse en el bot.
- El upsell de bebida + dip se activa solo cuando la bebida tiene precio.
- La página pública se sirve en `/carta`; la API en `/public/menu`.
- Las fotos se guardan en Postgres y se sirven en `/menu/image/{producto}`.
