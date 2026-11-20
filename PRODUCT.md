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
- Delivery propio hasta 10 km, a cotizar por km. Retiro en el salón.
- Efectivo o transferencia. Alias Mathias1998.
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

Papel crema, tinta negra, tomate, cheddar y oliva. Fraunces para la voz grande; Archivo para la información. Estructura de carta impresa: membrecía del club en el hero, grilla de platos, banda oscura de combos, lista de precios y bloque de instrucciones. El acento es la tipografía, no un efecto.

## Restricciones

- Mobile first: la mayoría llega desde WhatsApp.
- Los precios y la disponibilidad salen de la base, nunca hardcodeados.
- La página pública se sirve en `/carta`; la API en `/public/menu`.
- Las fotos se guardan en Postgres y se sirven en `/menu/image/{producto}`.
