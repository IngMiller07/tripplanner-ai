# Guion de la demostración

Duración sugerida: 8 a 10 minutos. Hablas tú; el sistema hace el trabajo.

## Diez minutos antes

1. Abre el sitio (la URL `WebsiteUrl` del stack) y entra con tu nombre.
2. **Calienta el agente**: manda un mensaje corto (por ejemplo, «hola») y espera la respuesta. La primera llamada a AgentCore tiene arranque en frío; así no ocurre en vivo.
3. Prepara dos pestañas más: la consola de DynamoDB (tablas `TripPlanner-*`) y el diagrama (`docs/diagrama-arquitectura.png`).
4. Ten a mano capturas de un recorrido exitoso por si falla la red.
5. Abre DevTools (F12) en la pestaña Red y déjala lista, sin mostrarla todavía.

## Pasos

| # | Qué haces | Qué dices |
|---|---|---|
| 1 | Muestra el diagrama de arquitectura | «Hay dos canales independientes. El de negocio es REST clásico. El agéntico va por streaming, directo a AgentCore, sin API Gateway.» |
| 2 | Abre el sitio y entra con tu nombre | «No hay credenciales de AWS en el navegador: solo dos URLs públicas.» |
| 3 | Ve a **Asistente** y pulsa la sugerencia «Vacaciones de 4 días en Cartagena» | «Este es el prompt de la prueba: cuatro días en Cartagena, desde Bogotá, tres personas, 5 millones de pesos.» |
| 4 | Envía y mira la **línea de tiempo** avanzar | «Cada paso dice quién lo hace y cuánto tarda. Ahora activo **Detalles técnicos** para mostrar la arquitectura: clima y vuelos van en paralelo; el Orchestrator delega en dos agentes especialistas por A2A y cada uno usa su servidor MCP.» (Al terminar la explicación, apaga el botón: la persona usuaria normal no ve esa jerga.) |
| 5 | Expande el paso del clima, los vuelos y el itinerario | «Esto no es texto: el agente devuelve JSON y el frontend lo dibuja. Y el itinerario solo usa lugares reales de Cartagena, adaptados al clima.» |
| 6 | Señala el paso «Guardar el viaje» | «Estas dos últimas son Tools que invocan Lambdas y escriben en DynamoDB.» |
| 6b | Escribe en el chat: «quiero que sean 3 días nada más» | «Cambiar un viaje ya armado también lo hace el código: reutiliza el vuelo más barato que ya encontramos, recalcula el presupuesto con ese precio real, y actualiza el viaje y el itinerario con las Tools UpdateTrip y UpdateItinerary. Los pasos que no cambian (clima, vuelo) se marcan como «sin cambios».» |
| 7 | Pulsa el botón **Reservar el vuelo más barato** | «Las reservas son simuladas, pero quedan guardadas de verdad. Es la misma acción que escribirle al agente.» |
| 8 | Ve a **Mis viajes**, abre el viaje, edita una actividad del itinerario y guarda | «Esta pantalla usa el otro canal: REST con API Gateway. Es el mismo backend que usan las Tools del agente.» |
| 9 | Ve a **Reservas**, cambia el estado a CONFIRMADA y luego pide por chat: «Elimina la reserva del vuelo» | «Estados: simulada, pendiente, confirmada. Y el agente también puede eliminarla con una Tool; el botón de la tabla hace lo mismo. Si hay varias, el agente pregunta cuál y ofrece un botón por reserva: no borra de más.» |
| 10 | Prueba **Vuelos** y **Clima** | «Estos módulos usan al mismo agente con una petición acotada.» |
| 11 | **Cierra la pestaña y vuelve a abrir el sitio** | «Los datos viven en DynamoDB, no en el navegador: el viaje, el itinerario y las reservas siguen ahí.» Al volver, la pantalla de entrada muestra la cuenta con «N viajes guardados»; también puedes escribir el mismo nombre desde otro navegador y verás lo mismo. |
| 12 | Abre DevTools → Red y luego DynamoDB | Muestra una llamada a `execute-api` (Canal 1) y la de `lambda-url` con los eventos llegando (Canal 2); en DynamoDB, los registros de las tres tablas. |
| 13 | Cierra con seguridad | «Mínimo privilegio en cada Lambda y en el rol del Orchestrator, sin tarjetas, CORS y throttling. Y declaramos los límites: sin Cognito y con Function URL público; en producción llevaría Cognito y CloudFront con WAF.» |

## Si algo falla

| Problema | Qué hacer |
|---|---|
| El agente tarda mucho la primera vez | Es el arranque en frío. Explícalo y espera. Por eso se calienta antes. |
| Un servicio externo (vuelos o clima) no responde | Ese paso queda en rojo con la causa y un botón **Reintentar**; el resto del plan se entrega igual. Aprovéchalo: «así se ve un fallo parcial controlado». Pulsa Reintentar. |
| El chat muestra un error | Pulsa «Nueva conversación» y vuelve a mandar el prompt. |
| No hay internet | Usa las capturas y explica el diagrama. |
