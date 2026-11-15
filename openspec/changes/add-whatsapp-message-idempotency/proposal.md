# Change: Add WhatsApp inbound message idempotency

## Why

WhatsApp puede entregar más de una vez el mismo mensaje. Sin una barrera
persistente, un retry del proveedor o dos entregas concurrentes podrían aplicar
dos veces el mismo efecto: crear un pedido, ampliar uno existente, cancelar una
operación o avanzar dos veces la conversación.

La regla `WA-4` exige registrar el `message_id` antes de procesar y cortar los
duplicados. `CONV-10` exige además que la marca de idempotencia comparta la
transacción con el efecto de dominio y que cualquier envío externo ocurra
después del commit. Antes de exponer el `POST`, `WA-3` exige autenticar el cuerpo
HTTP original con HMAC-SHA256.

## What changes

- Agregar `processed_whatsapp_messages` con `message_id` como PK de texto y
  `received_at` como `TIMESTAMPTZ` en UTC.
- Insertar el identificador mediante `INSERT ... ON CONFLICT DO NOTHING
  RETURNING message_id` y devolver un resultado explícito `claimed` o
  `duplicate`.
- Verificar `X-Hub-Signature-256` sobre los bytes originales del request, con
  HMAC-SHA256 y `hmac.compare_digest`, antes de parsear o abrir una transacción.
- Incorporar `WHATSAPP_APP_SECRET` como secreto obligatorio de entorno, oculto
  en representaciones, errores y logs.
- Definir puertos tipados e inyectables para:
  - convertir el cuerpo autenticado en una secuencia de mensajes validados;
  - construir un handler ligado a la `Session` de la transacción actual;
  - despachar acciones externas después del commit.
- Validar en la orquestación, antes de abrir una `Session`, que cada
  `message_id` sea un `str` no vacío ni compuesto solo por whitespace. Un
  parser defectuoso no puede enviar una identidad inválida al repositorio.
- Mantener los fakes exclusivamente en tests. Hasta que 4b aporte la
  composición real, una request firmada al webhook productivo falla cerrada
  con un 503 opaco y sin invocar parser, Session, repositorio, handler ni
  dispatcher.
- Procesar cada mensaje de un batch bajo su propia transacción PostgreSQL
  `READ COMMITTED`.
- Invocar el handler solamente después de ganar el claim y hacer commit de su
  efecto junto con la fila de idempotencia.
- Hacer rollback de todo, incluida la marca, si el handler falla, responder 5xx
  y permitir que un retry vuelva a reclamar el mismo `message_id`.
- Responder 200 sin repetir el handler ni el envío cuando el mensaje ya fue
  confirmado.
- Despachar acciones externas solamente después del commit. Una falla externa
  se registra de forma segura, no revierte datos confirmados y no se presenta
  como una garantía exactly-once.
- Reutilizar el tratamiento opaco de errores para que un fallo transaccional se
  correlacione por `message_id` sin registrar `str(exc)`, SQL, parámetros,
  payload ni PII (`D-25`).
- Probar duplicados, rollback y retry, batches y carreras reales con threads y
  conexiones PostgreSQL independientes.

## Architectural boundary with the following changes

Este change define la orquestación estable, no los adapters definitivos:

- `add-whatsapp-cloud-api-roundtrip` (4b) implementará el parser Pydantic del
  payload real de Meta, el `GET` de verificación y el dispatcher de salida por
  WhatsApp Cloud API. Ambos se conectarán a los puertos de 4a sin cambiar el
  límite transaccional. También reemplazará el proveedor productivo
  “integración no disponible” de 4a por una composición real; ningún fake de
  4a puede ocupar ese lugar.
- `add-conversation-state-machine` (change 6) implementará el handler real de
  conversación, el `SELECT ... FOR UPDATE` de `Conversation` y los locks
  posteriores de Order o Reservation.

`CONV-9` sigue siendo normativo. En 4a se garantiza el primer tramo del orden:
el claim ocurre antes de invocar al handler. Cuando change 6 reemplace el
handler de prueba, deberá adquirir después Conversation y luego cualquier lock
de Order o Reservation, y deberá incorporar los escenarios reales de ausencia
de deadlock.

## Impact

El cambio afecta únicamente:

- el modelo y la migración ya existentes, mediante revisión y tests;
- la configuración mínima del app secret y su plantilla sin valor real;
- el repositorio de idempotencia del adapter de WhatsApp;
- la verificación de firma y la orquestación transaccional del `POST`;
- los contratos inyectables de entrada, procesamiento y post-commit;
- el logging seguro del fallo del webhook;
- tests unitarios, de API, integración, migración y concurrencia;
- documentación del change, `ESTADO.md` y bitácora al cerrarlo.

No se reorganizan módulos existentes ni se introducen Unit of Work genéricos,
outbox, brokers o una nueva arquitectura.

## Dependencies

- Reglas normativas `WA-3`, `WA-4`, `WA-9`, `CONV-9` y `CONV-10`.
- Contratos aprobados `application-runtime`, `runtime-configuration`,
  `error-contract`, `structured-logging` y `domain-data-boundary`.
- La transacción del webhook fija `READ COMMITTED`, supuesto requerido por el
  repositorio de customers (`D-32`).

No depende de que exista un repositorio o servicio de Conversation.

## Non-goals

- Implementar el parser del payload real de Meta, normalizar teléfonos o
  interpretar tipos de mensaje. Eso pertenece a 4b.
- Implementar el `GET` de verificación de Meta, el verify token, access token,
  phone number ID o versión de Graph API. Eso pertenece a 4b.
- Realizar llamadas a WhatsApp Cloud API. 4a usa un dispatcher inyectable de
  prueba exclusivamente en tests; el adapter real pertenece a 4b.
- Crear `ConversationRepository`, la máquina de estados o locks reales sobre
  Conversation, Order o Reservation. Eso pertenece al change 6.
- Guardar el payload completo del webhook.
- Registrar texto, firma, teléfono, dirección u otra PII.
- Rastrear mensajes salientes, entregados o leídos.
- Implementar un outbox o resolver la brecha entre commit y envío.
- Garantizar entrega exactly-once hacia sistemas externos.
- Definir una política automática de borrado en el MVP.
- Particionar la tabla o agregar índices distintos de su PK sin una consulta
  real que los justifique.
