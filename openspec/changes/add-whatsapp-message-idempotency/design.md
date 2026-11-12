# Design: WhatsApp inbound message idempotency

## Context

Meta usa entrega al menos una vez: puede repetir un webhook cuando no obtiene
una respuesta satisfactoria o entregar concurrentemente el mismo mensaje. La
aplicación necesita distinguir un retry del proveedor de una nueva intención
del cliente.

Una deduplicación en memoria no sirve porque se pierde al reiniciar y no se
comparte entre procesos. Una consulta seguida de insert tampoco es segura: dos
transacciones pueden consultar al mismo tiempo y ambas creer que el mensaje es
nuevo. La unicidad debe decidirla PostgreSQL.

La spec original exigía además adquirir locks reales de Conversation, Order y
Reservation. Esos repositorios y el handler de conversación todavía no existen:
pertenecen a `add-conversation-state-machine`. Probar ese orden con un doble
haría pasar un test sin demostrar locks reales. Este design adopta la opción A:
4a estabiliza autenticación, idempotencia, límite transaccional y post-commit;
change 6 hereda los escenarios de locks reales y ausencia de deadlock.

## Goals

- Aplicar como máximo una vez el efecto transaccional de cada `message_id`.
- Permitir un retry real cuando el procesamiento anterior hizo rollback.
- Resolver correctamente dos entregas concurrentes del mismo mensaje.
- Validar la firma sobre el cuerpo HTTP original antes de parsear o tocar DB.
- Procesar cada mensaje de un batch en una transacción independiente y
  explícitamente `READ COMMITTED` (`D-32`).
- Permitir que 4b conecte el parser real de Meta y el cliente Cloud API sin
  reescribir la orquestación ni mover el commit.
- Mantener el dominio sin imports de SQLAlchemy y los adapters externos fuera
  de la transacción.
- Registrar fallos de forma correlacionable y opaca (`D-25`).
- Mantener la tabla mínima y libre de PII.

## Non-goals

- Parsear el payload real de Meta, status updates o tipos no soportados.
- Implementar el `GET` de verificación de Meta.
- Enviar por la Cloud API real.
- Implementar Conversation, Order o Reservation repositories o sus locks.
- Evitar duplicados de mensajes salientes.
- Resolver la brecha entre commit y envío externo.
- Almacenar webhooks para debugging o replay manual.
- Introducir Unit of Work, broker, outbox, cache distribuido o infraestructura
  adicional.

## Data model

```text
processed_whatsapp_messages
-----------------------------------------
message_id   TEXT         PRIMARY KEY
received_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
```

`message_id` es la clave de consulta real y por eso se usa directamente como
PK. Un ID numérico adicional no aportaría una identidad nueva y obligaría a
mantener otro índice único.

`TEXT` evita asumir una longitud máxima del proveedor. `received_at` permite
observar crecimiento y antigüedad, pero no necesita un índice en el MVP.

No se persisten payload, tipo, cliente ni estados salientes. La tabla indica
únicamente que ese mensaje entrante produjo un commit exitoso.

La migración `57f88cf9ee63` ya forma parte del historial compartido. No se edita
si sus pruebas de upgrade, contrato de esquema y downgrade pasan. Cualquier
defecto real que exigiera reescribirla requiere una decisión separada.

## Stable ports between 4a, 4b and change 6

4a define contratos tipados; no implementa los adapters definitivos. Los tipos
genéricos permiten que 4b agregue su modelo Pydantic de mensaje y su intención
de salida sin que 4a conozca campos de Meta.

### Validated inbound message

El orquestador requiere de cada mensaje solamente una identidad opaca
`message_id`. El tipo concreto puede contener más datos, pero debe haber sido
validado antes de llegar al handler. No se permite que el handler reciba un
`dict` crudo (`VAL-1`).

Conceptualmente:

```python
class InboundMessageIdentity(Protocol):
    message_id: str
```

El `Protocol` sirve para typing estructural, no valida en runtime. Por eso el
orquestador de 4a es responsable del invariante mínimo: después del parser y
antes de abrir la primera `Session`, valida todos los mensajes del batch. Cada
`message_id` debe ser un `str` cuyo `strip()` no sea vacío. El valor es opaco:
se valida, pero no se recorta ni normaliza antes del claim.

Si una identidad incumple ese contrato, se considera un defecto del adapter:
la request termina con un 5xx opaco y ningún mensaje del batch llega al
repositorio, al handler ni al dispatcher. Validar el batch completo antes de
la primera transacción evita commits parciales causados por un parser que
rompió su contrato.

4b aportará un modelo Pydantic concreto que satisfaga este contrato y contenga
los datos normalizados que necesiten los handlers posteriores. Ese modelo
puede imponer además las reglas específicas de Meta; el guard de 4a permanece
como defensa del límite de persistencia.

### Inbound batch parser

```python
class InboundBatchParser(Protocol[MessageT]):
    def parse(self, raw_body: bytes) -> Sequence[MessageT]: ...
```

El parser se invoca solo después de validar la firma. Devuelve cero o más
mensajes ya validados. 4a lo prueba con un fake que vive únicamente en tests;
el parser real de Meta, su tolerancia a status updates y sus errores pertenecen
a 4b.

### Transaction-bound handler factory

```python
class MessageHandlerFactory(Protocol[MessageT, ActionT]):
    def build(self, session: Session) -> MessageHandler[MessageT, ActionT]: ...

class MessageHandler(Protocol[MessageT, ActionT]):
    def handle(self, message: MessageT) -> Sequence[ActionT]: ...
```

El orquestador crea el handler después del claim y le entrega indirectamente la
misma `Session` mediante la factory. La factory vive fuera de `domain` y arma
repositorios/servicios ligados a esa sesión. El servicio de dominio y el
handler no abren, commitean ni revierten transacciones y no realizan llamadas
externas.

El handler devuelve acciones descriptivas, todavía no ejecutadas. En 4b una
acción concreta podrá representar, por ejemplo, un texto saliente; en change 6
podrá representar intenciones de conversación. 4a no interpreta su contenido.

### Post-commit dispatcher

```python
class PostCommitDispatcher(Protocol[ActionT]):
    def dispatch(self, action: ActionT) -> None: ...
```

El dispatcher se invoca únicamente después de que el contexto transaccional
cerró con commit. 4a usa un fake verificable exclusivamente en tests; 4b
aporta el adapter Cloud API. El dispatcher no recibe la `Session` y no puede
participar de la transacción.

### Why a factory rather than passing Session to the handler

Pasar `Session` directamente al handler futuro empujaría SQLAlchemy hacia el
dominio y chocaría con `GEN-1`. La factory mantiene SQLAlchemy en la capa de
orquestación: construye repositorios concretos y entrega al handler dependencias
de dominio ya ligadas a la transacción. El costo es un contrato adicional, pero
evita tener que rediseñar 4a cuando llegue la máquina de conversación.

## Production composition before 4b

Los fakes prueban contratos, no representan una implementación operativa. Se
definen en módulos o fixtures de tests y la composición productiva no los
importa, instancia ni usa como defaults.

4a mantiene la aplicación disponible para el resto de sus rutas y monta el
endpoint estable, pero su proveedor productivo de runtime de WhatsApp queda en
estado explícito “integración no disponible”. Después de autenticar la firma y
antes de invocar el parser, ese guard responde HTTP 503 mediante el contrato de
error opaco existente. No abre una `Session` ni invoca parser, repositorio,
handler o dispatcher. Por lo tanto, 4a nunca responde 200 fingiendo haber
procesado un webhook real. Como tampoco crea el claim, el 503 permite que un
retry posterior a la instalación de 4b procese el mensaje normalmente.

Los tests del orquestador sustituyen explícitamente el proveedor mediante la
composición de test o los overrides de dependencias de FastAPI. En 4b, la
composición productiva sustituye el proveedor no disponible por el bundle real
de parser, handler aprobado y dispatcher. La ruta, los puertos y el límite
transaccional no cambian.

La firma se valida antes de consultar disponibilidad para conservar el contrato
de autenticación: una firma inválida sigue devolviendo 403; una request
correctamente firmada sin integración real devuelve 503.

## Signature verification

`X-Hub-Signature-256` tiene el formato `sha256=<hex digest>`. El valor esperado
se calcula exactamente sobre los bytes recibidos:

```text
HMAC-SHA256(key=WHATSAPP_APP_SECRET, message=raw_request_body)
```

La comparación usa `hmac.compare_digest`. Un header ausente, con esquema
distinto de `sha256`, digest malformado o digest incorrecto devuelve 403.

El orden obligatorio es:

```text
leer bytes originales
    ↓
validar formato y HMAC con compare_digest
    ↓
resolver runtime productivo (503 si la integración no está disponible)
    ↓
parser inyectable
    ↓
validar todos los message_id del batch
    ↓
procesamiento transaccional
```

No se reserializa JSON para verificar. Una firma inválida no invoca el parser,
no abre una Session y no escribe en la DB. El app secret viene exclusivamente
de `WHATSAPP_APP_SECRET`; nunca se loguean secret, firma ni cuerpo.

La operación de ruta que toca la base sigue siendo `def` (`DAT-1`). Una
dependencia de borde puede leer asincrónicamente el cuerpo original siempre que
no toque datos ni cambie el límite transaccional.

## Transaction ownership and isolation

La transacción pertenece al orquestador de un mensaje entrante. El repositorio
de idempotencia, la factory y el handler no hacen `commit`, `rollback`, `begin`
ni abren una transacción separada.

Cada mensaje obtiene una Session y una transacción nuevas con aislamiento
`READ COMMITTED` explícito, no inferido del default del servidor. La prueba de
integración consulta `SHOW transaction_isolation` dentro de la transacción.

El flujo obligatorio por mensaje es:

```text
abrir Session/conexión READ COMMITTED
    ↓
abrir transacción del mensaje
    ↓
INSERT idempotente del message_id
    ↓
si es duplicate: terminar sin construir/invocar handler
    ↓
construir handler ligado a esa misma Session
    ↓
handler aplica efectos DB y devuelve acciones descriptivas
    ↓
commit de claim + efectos del handler
    ↓
dispatcher ejecuta las acciones, ya fuera de la transacción
```

La operación de claim usa:

```sql
INSERT INTO processed_whatsapp_messages (message_id)
VALUES (:message_id)
ON CONFLICT (message_id) DO NOTHING
RETURNING message_id;
```

Si retorna una fila, esa transacción adquirió el mensaje. Si no retorna, el
mensaje ya fue confirmado por otra transacción y no se procesa.

## Lock order and deferred scenarios

`CONV-9` no se modifica:

1. claim de idempotencia;
2. lock `FOR UPDATE` de Conversation;
3. locks `FOR UPDATE` de Order o Reservation.

4a implementa y prueba el primer límite: el handler nunca se construye ni se
invoca antes de ganar el claim, y un duplicado corta antes del handler. Por lo
tanto, ningún handler conforme puede tomar un lock previo al claim.

El repositorio real de Conversation y los flujos que toman los locks 2 y 3 son
del change 6. Ese change debe incorporar los escenarios trasladados:

- un mensaje que modifica Order toma claim → Conversation → Order;
- un mensaje que modifica Reservation toma claim → Conversation → Reservation;
- un duplicado no toma ninguno de esos locks;
- dos flujos cruzados concurrentes no producen deadlock.

4a no usa un doble para afirmar que esos locks existen.

## Failure behavior

### Failure before commit

Una excepción del handler hace rollback automático de la marca y de cualquier
cambio parcial. Se descartan las acciones devueltas o acumuladas, el batch se
detiene y el webhook responde 5xx. Cuando Meta reintenta, el insert puede ganar
otra vez.

### Duplicate after successful commit

El insert no retorna fila. El webhook responde 200 sin cuerpo útil y no
construye el handler, no repite efectos ni genera acciones externas.

### External dispatch failure after commit

El dato ya quedó confirmado. La falla se registra sin secretos ni PII, no
revierte la transacción, no cambia la respuesta a 5xx y no impide procesar los
mensajes posteriores del batch. El webhook responde 200 si no hubo una falla
transaccional.

No se reintenta automáticamente el envío dentro de 4a y no se afirma entrega
exactly-once.

### Crash after commit and before external dispatch

El mensaje queda marcado como procesado y un retry se corta como duplicado. La
acción externa podría perderse. Esta es la brecha aceptada al diferir el outbox.

## Concurrent delivery

Dos transacciones que insertan el mismo `message_id` se coordinan mediante la
PK de PostgreSQL. Solo una puede conservar la fila. La otra espera la resolución
del conflicto y luego continúa como duplicado o puede ganar si la primera hace
rollback.

No se hace primero un `SELECT`, porque ese patrón deja una carrera entre la
consulta y el insert.

La prueba crítica usa threads reales, dos conexiones PostgreSQL independientes
y una barrera con timeout. Demuestra exactamente un commit del claim y un solo
efecto persistente del handler. Otra prueba mantiene al ganador abierto, hace
rollback y demuestra que el competidor bloqueado puede adquirir el claim y
procesar.

## Batched webhooks

La firma se verifica una vez sobre el cuerpo completo y el parser produce la
secuencia tipada. El orquestador valida todas sus identidades antes de abrir la
primera Session. Después, cada `message_id` tiene Session y transacción propias.

La política ante fallas es fail-fast para fallas transaccionales:

- los mensajes anteriores ya confirmados permanecen confirmados;
- el mensaje fallido revierte claim y efectos;
- los mensajes posteriores todavía no fueron procesados;
- el webhook responde 5xx;
- en el retry, los anteriores son duplicados y el fallido o los posteriores
  pueden procesarse normalmente.

Una falla exclusivamente post-commit se loguea y el batch continúa; no produce
5xx porque un retry no podría repetir de forma segura la acción perdida.

## HTTP outcomes

- 200 sin cuerpo útil: todos los mensajes nuevos se confirmaron o eran
  duplicados; también cuando hubo solo fallas externas post-commit.
- 403 uniforme: firma ausente, malformada o inválida.
- 503 uniforme: la firma es válida pero la composición productiva real todavía
  no está disponible; no se abre una Session ni se simula procesamiento.
- otro 5xx uniforme: el parser rompe su contrato de identidad o el
  handler/orquestación falla. Los errores propios del futuro parser Meta se
  definirán en 4b.

Todas las rutas declaran `response_model` para conservar el contrato aprobado
de `application-runtime`, aunque el 200 se materialice como una respuesta
vacía. La ruta exacta `/webhook` cuenta como perteneciente al prefijo; el test
de prefijos debe aceptar tanto el prefijo exacto como subrutas.

## Safe logging and D-25

Se puede registrar:

- `message_id`;
- outcome `claimed`, `duplicate`, `rolled_back` o `dispatch_failed`;
- duración del procesamiento;
- tipo de error y metadatos SQL opacos ya admitidos por `api/errors.py`.

No se registra payload, texto, firma, secret, teléfono, nombre, dirección, SQL
con parámetros ni `str(exc)`. El fallo transaccional reutiliza el tratamiento
opaco existente y produce un solo record de error correlacionado por
`message_id`; no se agrega un log en el webhook para luego volver a loguear la
misma excepción en el middleware.

El mecanismo concreto puede transportar el `message_id` como contexto seguro
del request hacia el middleware, pero no debe ampliar el contenido permitido
de las excepciones. `hide_parameters=True` sigue siendo defensa adicional, no
la solución de D-25.

## Retention

El MVP no elimina filas automáticamente. Borrar una PK permite que un replay
antiguo vuelva a producir efectos. El volumen por instancia se medirá antes de
definir una retención segura.

## Trade-offs

### Benefits

- Corrección frente a retries y concurrencia sin servicios adicionales.
- La misma transacción permite reintentar fallos reales.
- 4b y change 6 reemplazan adapters/factories, no la orquestación.
- El dominio futuro no recibe una Session ni un cliente externo.
- Modelo pequeño y fácil de consultar.

### Costs

- Los puertos parser/factory/dispatcher agregan tres interfaces pequeñas antes
  de sus implementaciones productivas.
- 4a se verifica con fakes de borde y no constituye todavía un roundtrip real
  con Meta.
- Crecimiento continuo de la tabla hasta definir retención.
- Una PK de texto ocupa más que una PK numérica, aunque evita otro índice.
- No recupera acciones externas perdidas después del commit.

## Alternatives rejected

- **Crear `ConversationRepository` en 4a:** invade change 6 y su spec.
- **Simular locks con un doble:** el test aparentaría probar PostgreSQL sin
  adquirir ningún lock real.
- **Implementar el parser Meta y Cloud API ahora:** mezcla 4a con 4b y amplía
  superficie de secretos y red antes de cerrar idempotencia.
- **Pasar Session al servicio de dominio:** viola el límite de `GEN-1`.
- **Agregar Unit of Work u outbox:** resuelve problemas que este change no
  necesita y aumenta complejidad operativa.
