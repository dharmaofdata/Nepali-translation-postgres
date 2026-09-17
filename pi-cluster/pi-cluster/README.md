# Pi-Cluster 3.14 — skeleton

Первый vertical slice по Developer Guide §37–39: backend catalog, scenario model,
fidelity-проверка, один реализованный laptop backend, fake service, один fault,
один check, experiment record.

## Запуск

```text
git clone ... && cd pi-cluster
./bootstrap-dev                      # проверка prerequisites, publish artifacts, tests
sudo ./picluster run scenarios/smoke-node-off.yaml
```

```text
sudo ./picluster run scenarios/pg-node-off.yaml      # настоящий PostgreSQL
sudo ./picluster run scenarios/pg-node-restart.yaml  # node.off -> node.on
```

Требования: Linux, kernel ≥ 5.14 (`cgroup.kill`), cgroup2, iproute2, Python ≥ 3.10,
PyYAML. Для `pg-node-off` дополнительно: установленный PostgreSQL 16 на хосте
(рецепт переупаковывает `/usr/lib/postgresql/16`), python3-psycopg2, пользователь
`postgres`. `run` и netns-тесты требуют root с CAP_NET_ADMIN. Перед запуском backend
проверяет пробой, что может создать netns, veth и cgroup: если нет — тесты
пропускаются с причиной, а `run` отказывается до создания record.

```text
picluster validate    SCENARIO [--backend B]   модель + backend + artifact, без side effects
picluster portability SCENARIO                 SUPPORTED / PLANNED / UNSUPPORTED по каталогу
picluster run         SCENARIO [--backend B]   exit: 0 PASS, 1 FAIL, 3 INCONCLUSIVE, 2 error
```

Backend по умолчанию берётся из `configs/defaults.yaml`; сценарий backend не называет.
Record: `experiments/<id>/record.json` + `events.jsonl` + `console/` (вывод агентов).

## Backends

| backend | status | provides |
|---|---|---|
| laptop-nsnode | implemented | isolated_network_identity, processes_gone, peers_see_silence, artifact_delivery_path, disk_preserved, private_ipc_namespace, private_dev_shm, peers_see_no_progress |
| laptop-vm | stub | — |
| laptop-docker | stub | — |
| pi5 | stub | — |

Stub существует только как descriptor в `backends/`. Запуск на нём —
`ERROR: backend <name> is declared but not implemented`, без fallback.

## laptop-nsnode в этой форме

Нода = netns + cgroup v2 (`<cgroup2>/picluster/nN`) + mount и ipc namespace +
agent-процесс. Bench bridge `pic-br0`, адреса и таймауты из
`configs/hw/laptop-nsnode.yaml` (10.3.14.11…; сервис на 5432 на каждой ноде).

Каждый подъём ноды получает свои mount и ipc namespace и свой tmpfs на
`/dev/shm`. Хранилище (`<node_root>/<nN>/disk`) живёт на хосте и переживает
`node.off` — оно и даёт `disk_preserved`, а не namespace'ы.

- `node.off` = `port.down` (host-side veth down), затем `power.cut` (`cgroup.kill`).
  Порядок обязателен: иначе ядро ноды шлёт FIN/RST и пиры узнают об отказе мгновенно.
- `node.on` = `power.on` + `port.up`: тот же netns и тот же диск, новые mount и ipc
  namespace — как после перезагрузки. PostgreSQL проходит crash recovery на своих
  данных; роли не меняются, promote нет.
- `process.pause` / `process.resume` = `cgroup.freeze`: процессы стоят, ядро ноды
  продолжает отвечать. Для пиров это не смерть: соединения живы, ACK'и идут,
  прогресса нет. Клиент при этом **не получает ошибки** — в отличие от `node.off`,
  где он упирается в таймаут.
- Checks видят ноду только через `backend.observe(node, semantic)` → `holds` +
  evidence. На nsnode evidence — cgroup и pid'ы, на Pi будет состояние питания.
  Check descriptor объявляет `observes` (подмножество `requires`).
- Agent регистрируется у controller по TCP. Inventory строится только из регистраций.
  Артефакт agent скачивает по HTTP с проверкой sha256.

Чего нет, поэтому это не в `provides`:

- изоляции pid/ipc/uts/mount, отдельной FS и `/dev/shm` — нода видит файловую систему хоста;
- resource limits. Hardware profile агента берётся из `/proc`, то есть это ресурсы хоста;
- `node.on`, recovery и `disk_preserved`;
- LazyFS, поэтому нет `unfsynced_loss`.
- PID и UTS namespace, свой rootfs и resource limits.
- Агент ставится на диск ноды копированием из дерева, а не доставляется
  артефактом: это по-прежнему укороченный путь.
- Проверки утечек (`no_ipc_leak`, `no_dev_shm_leak`) смотрят на хост целиком:
  базовая линия снимается при подъёме стенда, и всё появившееся после неё
  считается утечкой. Чужой PostgreSQL, запущенный на том же ноутбуке во время
  прогона, завалит прогон. Привязки сегмента к ноде у нас нет.
- LazyFS нет, поэтому `unfsynced_loss` нет: `node.off` — это крах процессов, а не
  потеря питания, и сколько потеряно, не измеряется.

Controller и workload живут на хосте, на bench-адресе.

## Сценарии и роли

Сценарий размещает сервисы по нодам и говорит, откуда берутся роли:

```text
role_source: declared   роль возвращает исполнитель (статическая топология)
role_source: observed   исполнитель возвращает только членство,
                        роль наблюдает у сервиса
```

`initial.primary` — состояние на старте, а не то, к чему возвращаются.
`nodes.count` — сколько логических нод выделено эксперименту, а не сколько из
них с базой: монитор или координатор — такой же сервис со своим placement.

## Сервисы

| service | адаптер | что делает |
|---|---|---|
| fake | `services.fake` | запускает скрипт-артефакт, роль — та, что сказали |
| postgres | `services.postgres` | initdb / `pg_basebackup -R -X stream`, роль наблюдается через `pg_is_in_recovery()` |
| pgaf | `services.pgaf` | монитор pg_auto_failover (`pg_autoctl create monitor`) |
| pgaf-postgres | `services.pgaf` | нода БД под управлением монитора; роль решает монитор, адаптер только сообщает |

Сценарии: `pg-node-off` (primary исчезает и не возвращается), `pg-node-restart`
(возвращается и проходит crash recovery) и `pg-primary-pause` (primary жив для
сети, но не двигается). Failover и promote нет ни в одном: роли задаёт сценарий.
Проверки restart-сценария: `node_returns`, `postgres_recovers`,
`acked_prefix_present`, `replication_streaming_again`, `no_ipc_leak`,
`no_dev_shm_leak`, `target_processes_gone`.

Сценарий не называет ноду: `node.on` целится селектором `{node_of: 1}` — в ту
ноду, которую выключил первый fault.

`pgaf-primary-loss` — первый HA-сценарий: монитор на n4, три ноды БД, потеря
primary, промоушен выжившего, возвращение старого primary. Проверки:
`never_two_writable`, `failover_promoted_survivor`, `old_primary_returns_as_replica`,
`exactly_one_primary`. Ни одна не знает про pg_auto_failover: они про свойство,
а не про продукт.

Требования для него: `postgresql-16-auto-failover` и `pg-auto-failover-cli` на
хосте (рецепт переупаковывает `/usr/bin/pg_autoctl` вместе с деревом PostgreSQL).

Адаптер держит node-local вещи, которых на backend без mount namespace не избежать:
свой `unix_socket_directories` (иначе все ноды на порту 5432 столкнутся в общем
`/var/run/postgresql`) и запуск от `postgres` (сервер отказывается работать от root).

## Layout

```text
backends/        descriptors (data)          semantics.yaml    vocabulary
scenarios/       scenarios                   relaxations.yaml  relaxation catalog
checks/          check descriptors + impl    services.yaml     service types, ports
configs/hw/      hardware overlays           artifacts/        recipes; repo/ is built, gitignored
controller/      model, executor, cli        agent/            node agent (stdlib only)
controller/backends/  backend implementations fake/            fake service
tests/           unit + root-only netns/run tests
```

Добавить backend = добавить descriptor; для реализации — модуль в
`controller/backends/`, на который descriptor ссылается через `implementation:`.
Тест `NoBackendNamesInCore` запрещает имена backend'ов в `controller/*.py`.
