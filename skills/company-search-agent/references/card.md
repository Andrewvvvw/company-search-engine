# Карточка версии 1

## Запрос

`request.json` содержит:

```json
{
  "original_request": "Найди 50 стоматологических клиник Алматы с email действующего директора. Один Excel, 5 листов по 10.",
  "geography": "Алматы, Казахстан",
  "industry": "Стоматологические клиники",
  "quota": 50,
  "batch_size": 10,
  "fields": [
    {"key":"name","label":"Название"},
    {"key":"city","label":"Город"},
    {"key":"address","label":"Адрес"},
    {"key":"phone","label":"Телефон"},
    {"key":"person","label":"ФИО"},
    {"key":"role","label":"Должность"},
    {"key":"email","label":"Рабочий email директора"}
  ],
  "named_contact": true,
  "allowed_roles": ["директор", "генеральный директор"],
  "uniqueness": "company",
  "evidence_policy": "Открытые первичные источники, актуальная роль, прямая связь email с человеком",
  "independent_review_required": false,
  "max_evidence_age_days": 30
}
```

Все `fields` обязательны. Для другого поиска меняй только JSON. Для именного контакта обязательны поля `person`, `role`, `email` и список допустимых ролей. Для обычного поиска `named_contact: false`, `allowed_roles: []`. Всегда включай `name`. `uniqueness`: `company` (юридический оператор), `brand` (сеть), `branch` (филиал); выбор согласуй. `identity` записи содержит `registration_id`, `legal_name`, `brand`, `branch_id` по наличию. При company необходим регистрационный номер либо подтверждённое полное наименование оператора; при brand — подтверждённый бренд; при branch — подтверждённый branch_id. Нельзя генерировать идентификатор из порядкового номера в реальном поиске. География и отрасль подтверждаются отдельными `scope_evidence`.

## Операции

`operations.json` — массив событий. Вид события: `candidate`, `segment`, `coverage`, `note`, `review`. `entity_id` стабилен. `id` можно задать для идемпотентного повтора; повторный ID с другим содержимым — ошибка. Скрипт добавляет timestamp и родителей текущей версии сущности. Для снятия конфликта новой операцией укажи все текущие головы в `parents` (или опусти: актуальные головы берутся автоматически).

Пример кандидата (источники `.example` ниже только для демонстрации, в продуктивном запросе запрещены):

```json
[
  {"kind":"candidate", "entity_id":"company-stable-id", "payload":{
    "state":"needs_review", "reason":"Нужно подтвердить принадлежность email",
    "identity":{"registration_id":"проверенный БИН", "legal_name":"ТОО ...", "brand":"..."},
    "scope_evidence":{
      "geography":{"url":"https://company.example/contacts","checked_at":"2026-11-21","quote":"Алматы"},
      "industry":{"url":"https://company.example/about","checked_at":"2026-11-21","quote":"Стоматологическая клиника"},
      "identity":{"url":"https://company.example/legal","checked_at":"2026-11-21","quote":"ТОО ... БИН ..."}
    },
    "fields":{
      "name":{"value":"Название", "sources":[{"url":"https://company.example/","checked_at":"2026-11-21","quote":"Название"}]}
    },
    "contact":{"current_role":false,"email_kind":"unknown","person_email_link":false,"role_source_url":"","email_source_url":""},
    "verification":{"reviewer":"coordinator","checked_at":"2026-11-21","scope_matches":false,"identity_checked":false,"duplicates_checked":false,"evidence_checked":false}
  }},
  {"kind":"segment","entity_id":"almaty-official-sites-1","payload":{
    "status":"pending", "query":"Алматы стоматология директор email",
    "cursor":"страница 1, результат 1", "next_action":"Открыть первый официальный сайт",
    "attempts":0, "processed_urls":[], "last_error":null
  }}
]
```

Для принятой записи `state: accepted`, все запрошенные поля и scope_evidence заполнены, verification содержит реальные отметки проверки. Для именного контакта `current_role: true`, `email_kind: person_work`, `person_email_link: true`; role_source_url указывает на источник поля role, email_source_url — email. Источник role должен цитировать человека и должность, источник email — человека и полный адрес. Если связь следует из структуры таблицы, сохрани короткую дословную выдержку нужной строки с человеком/email. Скрипт проверяет присутствие фрагментов, фактический смысл проверяет координатор. Старую должность нельзя подтверждать одной свежей датой посещения старой публикации.

Отклонённая запись остаётся с `state: rejected` и конкретным reason. `needs_review` не входит в квоту. При повторной проверке передавай полную новую версию кандидата, старые версии остаются в журнале. После каждого участка обновляй segment и кандидатов одной операцией `apply`.

## Структура хранения

Файл `<root>/<code>.json` содержит schema_version, code, request, created_at, revision, events, derived. Events — неизменяемый журнал с ID, kind, entity_id, parents и payload. `derived` — пересчитываемая проекция, её нельзя считать источником истины. При merge объединяются события, конфликтующие головы переводятся в ручную проверку. Регистрационные и контактные коллизии не дают повторного зачёта. Команда `merge` не меняет требования: несовместимый запрос отклоняется.

`review` привязан к SHA-256 digest именно набора принятых записей и требований. Итоговый export тоже привязан к digest и SHA-256 книги; готовность не переживает изменение набора, конфликты, отказ проверки или изменение review. Диагностика и журнал хранятся в карточке; исправление причины требует нового события, а не удаления истории.
