# Первый run: smoke-node-off

## Command

```text
git worktree add /tmp/wt bbae29a     # чистое дерево, как после clone
cd /tmp/wt && ./bootstrap-dev        # 27 tests OK
./picluster run scenarios/smoke-node-off.yaml
```

## Environment

Container sandbox, root. Kernel 6.18.44, cgroup hybrid: cgroup2 смонтирован в
`/sys/fs/cgroup/unified`, systemd нет. Python 3.12.3, iproute2 6.1.0, 1 vCPU.
На обычном systemd-laptop не проверялось.

## Event trace (сокращено)

```text
  t,s   event
 0.012  bench_ready             pic-br0 10.3.14.1
 0.049  bench_node_powered_on   n1 10.3.14.11   (n2, n3 далее)
 0.194  node_registered         n1 addr=10.3.14.11 (адрес сообщил agent)
 0.312  artifact_fetched        n1 cached=false   (sha256 56e1a2cd…)
 0.410  service_started         n1 primary :5432; n2, n3 replica :5432
 0.714  services_ready / workload_started
 5.714  fault_resolved          node.off {role: primary} -> n1
 5.718  bench_primitive         port.down n1
 5.718  bench_primitive         power.cut n1
 5.720  fault_injected          settled=true
 6.215  workload_stalled        reason=timeout
 6.638  node_unavailable        n1, heartbeat_timeout (unavailable_after 1.0s)
10.714  workload_stopped        ok/s: 76k 91k 80k 81k 82k 0 0 0 0 0
10.715  check_evaluated         target_processes_gone PASS
10.715  verdict                 PASS
10.977  teardown_done
```

## Record (ключевые поля; полный — docs/first-run-record.json)

```text
experiment_id          exp-20260911T163213Z-ccfc
git_commit             bbae29a1d6c4…, dirty=false
backend_placement      default laptop-nsnode; n1,n2,n3 -> laptop-nsnode
backend_provides       isolated_network_identity, processes_gone,
                       peers_see_silence, artifact_delivery_path
allowed/effective relaxations   [] / []
instruments            []
artifacts              fake-service sha256 56e1a2cd…, source_commit bbae29a, source_matches=true
check                  target_processes_gone PASS
                       n1 before [2657, 2687] after []; controls n2, n3 alive
verdict                PASS
```

## Negative runs

```text
$ ./picluster run scenarios/smoke-node-off.yaml --backend pi5
ERROR: backend pi5 is declared but not implemented          rc=2, record не создаётся, netns не создаются

$ ./picluster run tests/fixtures/scenarios/smoke-hw-timing.yaml --checks-dir tests/fixtures/checks
PASS            target_processes_gone (mandatory)
NOT_MEANINGFUL  test_requires_hw_timing (mandatory)          evidence: missing_semantics [hw_timing]
verdict=INCONCLUSIVE                                         rc=3
```

Impl test-only check'а бросает исключение при вызове. Значит, NOT_MEANINGFUL
выставлен без выполнения check'а, а не по его результату.

## Что доказано

- **Scenario не содержит ни backend, ни node id, ни адресов** (есть тест). Primary
  выбирается во время fault'а по наблюдаемым ролям.
- **Stub-backend определяется только данными.** Тест добавляет новый descriptor без
  изменения кода: он проходит portability (PLANNED) и отвергается при run.
  В `controller/*.py` нет имён backend'ов, это тоже проверяет тест.
- **Ложного PASS нет:** check с неудовлетворённым `requires` не выполняется, verdict INCONCLUSIVE.
- **`node.off` не пропускает FIN/RST.** Реализация `port.down` → `power.cut` дала
  тишину: `workload_stalled reason=timeout`. Контрольный тест (kill без `port.down`)
  видит FIN/RST, то есть тест тишины не vacuous. Если в коде поменять порядок
  primitives, падают `test_netns` и `test_run`; это проверено вручную.
- **`processes_gone` проверяется с контролем:** у цели процессы до fault'а были, а у
  соседей после fault'а остались, так что это не глобальный kill.
- **Teardown чистый:** после run не остаётся ни netns, ни veth, ни cgroup. Остатки
  упавшего run'а убираются при следующем запуске (`stale_cleanup`).

## Hardening (1758b73, повторный run)

После ACCEPT @yoda: `node_processes()` заменён на `observe(node, semantic)`,
тишина пиров проверена и для пира на другой ноде (n2 → n1, контроль n3 → n2),
тесты корректно пропускаются без CAP_NET_ADMIN (проверено через
`setpriv --bounding-set -net_admin`: 22 OK, 6 skipped). Повторный run на чистом
checkout: `exp-20260911T170205Z-eb72`, verdict PASS, workload_stalled reason=timeout;
evidence check'а теперь в форме observation (docs/rerun-record.json).

## Что не доказано

- Работа на systemd-laptop с unified cgroup2 в `/sys/fs/cgroup`: `picluster/` создаётся
  прямо в корне cgroup2, и возможен конфликт с systemd.
- 7 нод: проверены только 3.
- Controller-as-role: controller и workload работают на хосте. Проверена
  переносимость data-plane ноды, а не роли controller.
- Таймауты и `node_unavailable` не имеют смысла как метрики: нет `hw_timing`, и
  метрик в skeleton нет.
- Relaxations: каталог есть, но ни одна не реализована, поэтому
  `effective_relaxations` всегда `[]`.

# Второй run: pg-node-off (настоящий PostgreSQL)

## Command

```text
git worktree add /tmp/wt 30726c0
cd /tmp/wt && ./bootstrap-dev          # 40 tests OK
./picluster run scenarios/pg-node-off.yaml
```

## Что запускается

3 ноды: n1 primary (`initdb`), n2/n3 standby (`pg_basebackup -R -X stream`),
PostgreSQL 16.15, `synchronous_commit=on`, асинхронная репликация, без HA.
Workload: последовательные `insert` id 1..N в primary, каждый подтверждён до
следующего. `node.off` primary на 10-й секунде, дальше primary не появляется.

## Результат (exp-20260912T030556Z-9f5f, полный record в docs/pg-run-record.json)

```text
  t,s   event
 5.5    services_ready      n1 primary, n2 replica, n3 replica (роли наблюдены)
 5.6    workload_started    ~1200 tx/s
16.3    service_probed      n1: два walsender'а state=streaming
16.4    fault_injected      node.off n1 (port.down, power.cut)
17.2    node_unavailable    n1, heartbeat_timeout
17.8    workload_stalled    reason=timeout
25.6    workload_stopped    acked=14046, hung=false
        probes.end          n2, n3: max_id=count=14046, оба на 0/4287580
        verdict             PASS (3 mandatory checks)
```

## Что доказано

- **Граница `artifact != service` держится при замене fake на настоящий PostgreSQL.**
  Сценарий отличается от `smoke-node-off` только типом сервиса, артефактом и
  workload; ни backend, ни node id, ни путей в нём нет (есть тест).
- **Роль наблюдается, а не предполагается.** Адаптер отдаёт `pg_is_in_recovery()`.
  Мутация (убрать `-R` у `pg_basebackup`) даёт `run_status=error` с
  `observed role 'primary', wanted 'replica'`, а не молчаливо неверный прогон.
- **Репликация реальна:** до fault'а оба standby в состоянии `streaming`, после
  fault'а каждый держит непрерывный префикс подтверждённых id.
- **Артефакт доставлен по сети** (21 МБ tar.gz, sha256), распакован агентом в
  кэш ноды. Bind-mount'а хостового дерева нет.
- **Effective config рендерится агентом на ноде** из config intent: один и тот же
  `port = 5432` на всех нодах, socket dir — node-local.

## Что нашлось по дороге

- **libpq не замечает тишины сам по себе.** `statement_timeout` исполняет сервер,
  поэтому после его исчезновения он не срабатывает; первый прогон дал зависший
  workload и ни одного `workload_stalled`. Нужны `tcp_user_timeout` и keepalives.
  Это цена `peers_see_silence` на реальном клиенте: fake-сервис ловил тишину
  socket timeout'ом бесплатно.
- **`max(id)` и `count(*)` в разных снимках.** Probe делал два запроса, и на
  работающем workload'е они расходились. Проверка на разрывы могла бы сообщить о
  разрыве, которого не было. Теперь один запрос.
- **Утечка сегментов.** После `node.off` в общем `/dev/shm` остаются `PostgreSQL.*`
  (~23 МБ за несколько прогонов) и SysV-сегменты с `nattch=0`. Это прямое
  следствие отсутствия mount/ipc namespace; на Pi это снимает перезагрузка.

## Что не доказано

- Потеря неотфсинченного при `node.off` (нет `unfsynced_loss`), поэтому
  «сколько потеряно» не измеряется — проверяется только префикс.
- Failover, promote, восстановление redundancy: HA в этом слайсе нет.
- Абсолютные числа (1200 tx/s) ничего не говорят о Pi: общий SSD, нет `hw_timing`.
- Артефакт собран переупаковкой пакета хоста, x86_64. Для Pi нужен отдельный
  рецепт сборки из исходников.

# Третий run: pg-node-restart (жизненный цикл ноды)

## Command

```text
git worktree add /tmp/wt 27babaa
cd /tmp/wt && ./bootstrap-dev           # 49 tests OK
./picluster run scenarios/pg-node-restart.yaml
```

## Результат (exp-20260912T050242Z-dcd4, полный record в docs/restart-run-record.json)

```text
  t,s   event
 5.8    workload_started    ~1700 tx/s
16.1    fault_injected      node.off n1 (port.down, power.cut)
17.0    node_unavailable    heartbeat_timeout
17.5    workload_stalled    reason=timeout
20.8    fault_injected      node.on n1 (power.on, port.up), boot 2
21.4    node_returned       n1
21.4    service_deployed    n1 primary, reused_state=true    <- crash recovery
21.7    workload_resynced   client_acked=16598, server_max=16599
21.7    workload_resumed
46.0    workload_stopped    acked=73475
        verdict             PASS (7 mandatory checks)
```

После прогона на хосте не остаётся ни одного сегмента `PostgreSQL.*` в `/dev/shm`
и ни одного SysV-сегмента — проверяют `no_dev_shm_leak` и `no_ipc_leak`.

## Что доказано

- **Нода умеет возвращаться.** Тот же netns и тот же диск, новые mount и ipc
  namespace. PostgreSQL поднимается на своей PGDATA: `reused_pgdata=true`,
  system identifier не изменился и совпадает со standby, postmaster новый.
- **Ничего от умершей ноды не остаётся на хосте.** Мутация (убрать `unshare` и
  tmpfs) заваливает обе leak-проверки с конкретными уликами — новые ключи SysV и
  список `PostgreSQL.*`; остальные пять проверок при этом проходят.
- **Все подтверждённые записи пережили крах**, и standby переподключились сами —
  ничего в контроллере их не толкает.
- **Сценарий не называет ноду:** `{node_of: 1}`.

## Что нашлось по дороге

- **Проверки смотрели на состояние слишком поздно.** `target_processes_gone`
  вызывал наблюдение в момент оценки, а к тому времени нода уже жива. Пока
  жизненный цикл был односторонним, ошибка не проявлялась. Теперь наблюдения
  снимаются по всем нодам до и после каждого fault'а и хранятся в самом fault'е.
- **Клиент не возобновлял работу после возвращения ноды.** Запись, судьба которой
  была неизвестна в момент смерти, на самом деле закоммитилась; повтор того же id
  вечно упирался в primary key. В первом прогоне нода 24 секунды простояла живой и
  пустой, а `acked` стоял на месте. Теперь клиент при переподключении спрашивает
  сервер, что у того есть, и записывает `workload_resynced` с обоими числами.
  В этом прогоне: `client_acked=16598, server_max=16599` — ровно одна запись.
- **Идентичность namespace нельзя проверять по inode.** Ядро переиспользует номера
  после освобождения; тест сначала так и делал и дал ложное срабатывание.
  Свежесть проверяется по содержимому: файл, созданный в `/dev/shm` ноды до
  `node.off`, после возвращения отсутствует, а маркер на диске на месте.
- **Однажды виденная аномалия** (разные system identifier у вернувшегося primary и
  standby, пустая репликация) не воспроизвелась ни разу за десяток прогонов.
  Инвариант закодирован в `postgres_recovers`; мутация «пересоздавать PGDATA»
  даёт ровно такую картину, так что проверка её поймает.

## Ревью @Teodor и правки (c9d1820)

Ревью нашло две дыры в гарантиях от вакуума, обе воспроизведены и закрыты.

- **`node_returns` читал наблюдение, которого не объявлял.** Исполнитель отдавал
  check'у объединение наблюдений всех check'ов сценария, поэтому защита работала
  случайно. На backend'е без `processes_gone` она молча исчезала: проверка
  проходила для ноды, которая никогда не выключалась. Теперь у каждого check'а
  свой отфильтрованный вид, `node_returns` объявляет то, что читает, и падает,
  если наблюдения нет. `acked_prefix_present` и `postgres_recovers` объявляли
  `processes_gone` и не использовали — теперь требуют, чтобы крах действительно
  произошёл.
- **`replication_streaming` считал реплики по ответам сервиса.** Standby, чья
  проба упала, исчезал из обеих частей сравнения, и проверка проходила с мёртвой
  нодой. Ожидаемое множество теперь выводится из inventory минус ноды, которые
  исполненные fault'ы оставили выключенными.

Мелкое: наблюдатель SysV читал столбец `shmid`, называя его ключом; контроллер
не закрывал сокет перезагрузившейся ноды; в `pg-node-restart` не было проверки
состояния до отказа. Все три исправлены.

Красные пробы от коммита (`git worktree add --detach`), в том числе сквозная:
проба одного standby падает в настоящем прогоне → `verdict FAIL`. До правки
такой прогон проходил.

## Что не доказано

- Failover и promote: HA по-прежнему нет, роли заданы сценарием и не меняются.
- Потеря неотфсинченного: `node.off` — крах процессов, не потеря питания.
- PID/UTS namespace, свой rootfs, resource limits.
- Абсолютные числа (1700 tx/s) ничего не говорят о Pi.

# Четвёртый примитив: process.pause

`cgroup.freeze` на группе ноды. Процессы стоят, ядро ноды продолжает отвечать.
Каталог отказов теперь покрывает три разных смерти:

```text
service.kill     процесс умер, ядро ноды живо        (ещё не реализовано)
node.off         машина исчезла, пиры видят тишину
process.pause    процессы стоят, сеть отвечает
```

## Сценарий pg-primary-pause (короткая версия, прогон)

```text
 5.3  process.pause primary   cgroup.freeze
 6.3  node_unavailable        heartbeat_timeout
12.0  process.resume          cgroup.thaw
12.3  node_available          heartbeat (то же соединение, без регистрации)
12.3  node_resumed            сервис не передеплоен
      verdict PASS            replication_streaming, paused_node_is_not_dead,
                              replication_streaming_again
```

Пропускная способность клиента по секундам:

```text
1404 1602 1673 1657 1636 192 0 0 0 0 0 0 1394 1716 1640 1650 ...
                              ^ пауза ^
```

## Главное наблюдение

За семь секунд нулевой пропускной способности клиент **не получил ни одной
ошибки**: ни `workload_stalled`, ни переподключения, ни resync. Ядро замороженной
ноды подтверждает пакеты, поэтому `tcp_user_timeout` не срабатывает, а
`statement_timeout` исполняет сервер, который стоит. Запрос просто висел 6.7 с и
завершился успешно.

Сравнение с `node.off` на том же стенде: там клиент получает `timeout` через 1.4 с.
Это и есть разница, ради которой примитив нужен для lease и HA: с точки зрения
сети пауза выглядит как жизнь.

## Что нашлось по дороге

- **Доступность ноды считалась в одну сторону.** Монитор умел только терять ноду
  по таймауту heartbeat'а; вернуть её могла лишь повторная регистрация. После
  thaw агент продолжал слать heartbeat'ы по тому же соединению, а контроллер
  держал ноду недоступной, и прогон падал по таймауту. Теперь доступность
  наблюдается в обе стороны (`node_available`).
- **Возвращение после паузы — не то же, что после перезагрузки.** Примитив
  сообщает, потерян ли сервис: после `node.on` агент новый и сервис надо поднимать,
  после `process.resume` и агент, и PostgreSQL те же, и передеплой уничтожил бы
  ровно то, что проверяется.
- **`cgroup.kill` доходит до замороженных процессов** (проверено отдельно), так
  что teardown не виснет на паузе. На всякий случай teardown сначала размораживает.

## Что не доказано

- `service.kill` (умер процесс, ядро живо) ещё не реализован — третья строка
  каталога пока пустая.
- Что пауза ломает lease: для этого нужен HA-механизм, его нет.

# Пятый слайс: pg_auto_failover (pgaf-primary-loss)

Первый механизм, который сам принимает решение о роли. Монитор — обычный сервис
на отдельной ноде n4 из того же inventory, не хостовый процесс.

## Результат (exp-20260913T011451Z-ae61, полный record в docs/pgaf-run-record.json)

```text
14.2   кластер поднят: n4 монитор, n1 primary, n2/n3 secondary
20.4   node.off {role: primary}   -> n1 (роль разрешена по наблюдению)
21.8   workload_stalled           timeout
46.7   workload_resynced          клиент переподключился к новому primary
70.1   node.on {node_of: 1}       -> n1, restoring: member
71.2   workload_resumed
       конец: n1 replica (не writable), n2 primary, n3 replica
       монитор согласен: secondary / primary / secondary
       verdict PASS (4 обязательные проверки)
```

## Измерение, а не проверка

Записи не шли с 21-й по 70-ю секунду — пятьдесят секунд. Отдельный прогон без
возвращения старой ноды показал то же самое: пауза около минуты, после чего
записи идут на новом primary. То есть окно не связано с возвращением старого
primary, хотя в первом прогоне они совпали по времени и это легко принять за
причину.

Что известно из записи: новый primary держит
`synchronous_standby_names = ANY 1 (pgautofailover_standby_1, pgautofailover_standby_3)`,
а монитор до конца прогона сообщает про старую ноду `reported: primary, goal: demoted`.
Причину минутного окна назвать не могу: пробы снимаются вокруг fault'ов, а в
середине окна проб нет. Это нужно мерить, а не угадывать.

## Что доказано

- **Исполнитель не возвращает роль.** Вернувшаяся нода деплоится как `member`;
  в записи это видно, и красная проба (заставить его вернуть прежнюю роль)
  тест роняет.
- **Роль переехала**, и сценарий при этом не назвал ни одной ноды: `{role: primary}`
  разрешается по наблюдаемой роли в момент fault'а, `{node_of: 1}` — по прошлому fault'у.
- **Старый primary вернулся replica и не writable**, и монитор с ним согласен.
- **Проверки не знают про продукт.** Они про свойство: два writable одновременно,
  переезд роли, возвращение не-writable, ровно один writable в конце.

## Что нашлось по дороге

- **У нод был общий `/tmp`.** pg_autoctl держит там runtime-состояние и нашёл
  pid-файл ноды из прошлого прогона. Третий случай той же ошибки после `/dev/shm`
  и `/run/postgresql`.
- **Изоляция `/tmp` вскрыла зависимость от хостового дерева:** агент запускался
  прямо из репозитория, а мои одноразовые worktree лежат в `/tmp`. Теперь агент
  ставится на диск ноды.
- **Сервис с одной ролью не поднимался вовсе** — bootstrap раздавал только
  primary/replica, монитор оставался без роли, и keeper ждал его вечно.
- **Красная проба поймала слабый тест.** Удаление проверки «не writable» из
  `old_primary_returns_as_replica` не уронило тест: случай отличался только
  ролью. Добавлен случай «называет себя replica, но принимает записи» — роль и
  writability читаются разными запросами и могут разойтись.

## Что не доказано

- Отказ монитора: в этом сценарии его не ломаем.
- Network partition: сознательно отложен до механизма с настоящим кворумом.
- Причина минутного окна недоступности записи.
