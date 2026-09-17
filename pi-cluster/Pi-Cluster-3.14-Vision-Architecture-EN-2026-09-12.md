# Pi-Cluster 3.14

## Idea

**Pi-Cluster 3.14** is a portable laboratory for experiments with autonomous data systems on physical Raspberry Pi hardware.

The current system is intentionally simpler than the long-term autonomous vision. A human supplies a scenario. A deterministic Pi-Cluster controller/executor deploys artifacts and configuration, starts workloads, injects faults, observes the system, runs checks, and records the experiment.

```text
scenario
   |
deterministic controller / executor
   |
agent + backend primitives
   |
nodes
```

OpenClaw is **not** part of the current execution path. It is a planned slow-path layer that may later turn a human goal into desired state or a scenario, repair topology, place workloads, and optimize architecture. Basic execution, failure handling, and experiment correctness must remain deterministic without AI.

Failures must not be only software failures. A person should eventually be able to cut power to a node, slow CPU, network, or storage, isolate part of the cluster, or add a new machine. The system should continue as far as the selected architecture allows, while TPS, latency, replica lag, recovery time, state transitions, and correctness checks remain visible.

The name **Pi-Cluster 3.14** refers both to Raspberry Pi and to pi.

Short description:

> **Pi-Cluster 3.14 - a portable playground for autonomous data systems.**

Pi-Cluster is accompanied by three main documents:

```text
Pi-Cluster 3.14: Vision & Architecture
  what we are building, why, and how the system may evolve

Pi-Cluster: PostgreSQL Physics
  a compact map of PostgreSQL mechanisms
  that a developer must not misunderstand

Pi-Cluster Developer Guide
  the practical path from an empty laptop
  to a working prototype
```

Detailed interface and protocol definitions may later move into a separate Reference as they stabilize. The main design document should not turn into either a PostgreSQL textbook or a step-by-step implementation manual.

The project is not only a conference demo. It is a reproducible experimental environment for comparing architectures, HA mechanisms, search/RAG pipelines, PostgreSQL and DuckDB, and for checking behavior under real and deterministic faults.

## Purpose and intended use

Pi-Cluster starts as a **concrete practical project**: one complete path from a laptop development environment to a physical seven-node Raspberry Pi laboratory, real PostgreSQL HA, observable failures, and measured recovery.

It must not become a general-purpose platform too early. At the same time, architectural seams should remain open so the system can grow without rewriting the core.

Possible modes of use:

```text
self-use
  an individual laboratory for a developer or researcher

research
  reproducible experiments with data systems,
  HA, fault injection, observability, and autonomous control

development
  development and testing of PostgreSQL,
  DuckDB, extensions, injection points, and new architectures

demonstration
  visible physical experiments:
  power off a node, add a node, change the network,
  watch recovery and topology change

education
  learn from working building blocks,
  understand the physics, and assemble a solution

certification
  later: practical verification that a person can
  build, break, measure, and explain a system
```

The education track and any certification model need separate documents. This design only requires that Pi-Cluster architecture should not prevent such uses.

---

## Development principles

Pi-Cluster should be built around a small set of principles.

### Laptop first

Most development and debugging happens on an ordinary laptop. The physical Pi cluster is reserved for properties that the laptop cannot reproduce honestly enough: real power loss, physical switch behavior, NVMe power-loss behavior, thermal effects, powerbank behavior, ARM64 hardware effects, and hardware timing.

```text
laptop
  |
fast development backends
  |
real software stack
  |
physical Pi-Cluster
```

The same scenario must move across these environments without changing its logic.

Laptop fidelity is **explicit and selectable**. The laptop is optimized for iteration speed, not for pretending to be a Raspberry Pi. Every deliberate shortcut must be named and written into the experiment record, and no shortcut may turn an unsupported conclusion into PASS.

Detailed desktop development, fidelity policies, namespaces, CI, and the path to the physical cluster belong in the **Pi-Cluster Developer Guide** and **Appendix A: Desktop Development Environment**.

### DnC - Data, not Code

Pi-Cluster extensibility follows **Data, not Code**: new behavior should normally appear as metadata, interfaces, capabilities, catalogs, policies, and declarative configuration rather than as new branches in the core.

```text
generic core
    |
metadata / catalogs
    |
    +-- nodes
    +-- backends
    +-- services
    +-- artifacts
    +-- workloads
    +-- faults
    +-- checks
    +-- policies
    +-- scenarios
```

YAML is the main external representation.

Practical criterion:

> **If adding a new scenario, workload, node type, fault, or backend requires changing Pi-Cluster core logic, the boundary is probably still wrong.**

DnC does **not** mean moving control flow into YAML. Code defines and validates primitives and their semantics. Data chooses and composes them.

### Interfaces before implementations

Scenarios work with concepts and semantics, not with implementation details.

```text
Node
Power
Network
Storage
Service
PostgreSQL
HA
Telemetry
Checker
```

Laptop backends, physical Pi, and future BYON devices may implement these interfaces differently while preserving scenario semantics.

### Backend semantics are explicit

A backend declares what semantics it actually provides. A scenario or check declares what semantics it requires.

```text
scenario requirements
        +
resolved target nodes
        +
backend provides
        +
effective relaxations
        =
meaningfulness of checks
```

Important rules:

```text
provides
  semantics proven by an implemented backend

planned_provides
  design intent only; executor must not use it

status: stub
  backend is declared in the model but cannot run
```

A stub backend must fail loudly if selected. It must never silently fall back to another backend.

The model intentionally allows a backend to exist as data before its implementation exists. This is a cheap architecture test: if `laptop-docker`, `laptop-vm`, or `pi5` cannot be declared without changing the controller core, the abstraction already leaks.

The initial backend catalog includes:

```text
laptop-nsnode   implemented incrementally
laptop-vm       stub / future higher-fidelity laptop backend
laptop-docker   stub / optional portability backend
pi5             stub until physical implementation exists
```

`laptop-docker` is not a required M0 backend. On Linux it provides no fundamentally stronger semantics than namespace-based nodes. OCI images may still be useful as a portable rootfs format.

### Fidelity, relaxations, and meaningful results

A relaxation states which semantics it breaks. A check states which semantics it requires.

```text
fast_kill
  breaks: peers_see_silence

unfsynced_kept
  breaks: unfsynced_loss

recovery_time
  requires: peers_see_silence, hw_timing
```

Checks have three outcomes:

```text
PASS
FAIL
NOT_MEANINGFUL
```

An experiment has:

```text
FAIL
  at least one mandatory check failed

INCONCLUSIVE
  no mandatory check failed,
  but at least one mandatory check was NOT_MEANINGFUL

PASS
  all mandatory checks passed
```

The rule is:

> **Relax freely; conclude conservatively.**

The same mechanism may later be applied to dashboard metrics so a metric is not presented as meaningful when its required semantics were not provided.

### Bench outside, agent inside

Portability depends on one simple boundary:

> **Bench controls only what is physically outside the node. The agent controls what happens inside the node.**

```text
BENCH                         AGENT
--------------------------    ----------------------------
power.cut                     unit.*
port.down / port.up           tc.*
external partition            cgroup.*
node hardware existence       fetch
                              wipe/reset experiment state
                              node-local configuration
```

On a laptop, bench may control host-side veths, cgroups, VM processes, or namespace creation. On Pi, the same external semantics may be implemented by a relay, a managed switch, or a human action.

Laptop-only hidden channels are not part of the normal model. Host bind mounts, static inventory injected directly into the controller, or host-side reset of node state are shortcuts that must be explicit relaxations if used.

### Repository is the source of truth

The project itself lives in Git.

```text
docs
scenarios
configuration intent
backend descriptors
workloads
checks
controller / agent code
fake implementations
build recipes
hardware notes
tests
```

Git stores **project intent**. Large built binaries and datasets may live separately in a local artifact repository.

Practical cycle:

```text
clone
  |
run
  |
experiment
  |
improve
  |
commit
```

Neither documentation, scenarios, nor configuration decisions should exist only in a developer's head or in undocumented manual commands.

### Reproducibility from the beginning

Every experiment must have a reproducible description:

```text
scenario
Git commit / tag
backend placement
backend capabilities
allowed and effective relaxations
instruments
artifacts/builds
configuration intent
effective configuration
dataset
fault timeline
seed
hardware inventory
metrics
checker results
semantic trace
```

### AI is not the survival path

OpenClaw is planned for the slow path. It may later select topology, restore redundancy, place workloads, and optimize architecture.

The fast path remains deterministic:

```text
database HA
leader election
promotion
watchdogs
leases
local restart
```

> **AI may optimize survival, but basic survival must not depend on AI.**

---

## Minimum development requirements

Pi-Cluster must be developable on an ordinary laptop.

Minimum guideline:

```text
CPU:      4 cores
RAM:      8 GB
Storage:  30-50 GB free SSD
Network:  Wi-Fi / Ethernet
OS:       Linux preferred
```

This is enough for controller/executor development, backend descriptors, lightweight/fake nodes, event history, checks, and small real PostgreSQL experiments.

More comfortable for several real PostgreSQL or DuckDB processes:

```text
CPU:      8 cores
RAM:      16 GB
Storage:  100 GB free SSD
```

32 GB+ is useful only for higher-fidelity desktop experiments and is not a project minimum.

The first month is laptop-only. Raspberry Pi hardware, managed switches, power electronics, and physical power cuts must not block M0.

---

## Minimum physical laboratory

A complete first physical configuration:

```text
7 Raspberry Pi total

1 preferred-controller-capable node
6 worker/controller-capable nodes

Gigabit Ethernet
programmable managed switch

independent power for each Pi
independent power for switch

local artifact repository
local Wi-Fi / enrollment point

physical hard power cut
basic status indication
```

A practical first version:

```text
preferred controller:
  Raspberry Pi 5
  16 GB RAM
  512 GB - 1 TB storage
  AI accelerator optional,
  needed only for fully local AI experiments

other nodes:
  Raspberry Pi 5
  4 GB minimum
  256 GB NVMe preferred
```

8 GB worker nodes are more convenient for DuckDB/RAG and mixed workloads, but **4 GB remains the minimum baseline** so basic HA, network, and orchestration experiments do not require expensive nodes.

An AI accelerator must not be required for control-plane survival. Pi-Cluster must work in deterministic mode without it.

---

## Possible areas of use

The same substrate should support different classes of experiments:

```text
PostgreSQL development
PostgreSQL HA
BiHA / Patroni / pg_auto_failover
PostgreSQL injection points
planner/executor experiments

DuckDB analytics
PostgreSQL + DuckDB architectures

FTS
vector search
RAG
hybrid retrieval

distributed systems
fault injection
network partitions
resource degradation
correctness checking

observability
OpenTelemetry
experiment provenance

autonomous infrastructure
self-repair
architecture search
recursive control-plane HA

BYON
Android
Linux laptops
MacBook
other SBCs

research
demonstrations
education
future certification
```

This breadth must not cause uncontrolled core growth. New uses should first appear as new capabilities, artifacts, scenarios, policies, adapters, and data.

---

## Implementation strategy: grow gradually without closing the future

This document describes the **space of possibilities**, not the scope of the first release.

The first practical path stays intentionally narrow:

```text
laptop
  |
logical nodes
  |
real PostgreSQL
  |
node lifecycle
  |
first HA mechanism
  |
physical 7-Pi cluster
  |
real physical faults
  |
measured recovery
```

Main rule:

> **Do not build a general platform now. Build one working path with extensible boundaries.**

Add the next mechanism only when a working experiment requires it.

Second rule:

> **Simplify the implementation path, not the architecture. The first version should be small without making the full system impossible later.**

Capabilities are grouped as:

```text
CORE
  required for the first useful practical project

PLANNED
  the interface and place in the architecture are preserved now,
  implementation comes later

RESEARCH
  a research direction that must not complicate the first working system
```

Current status:

```text
CORE
  Laptop-first development
  Git repository as source of truth
  backend/fidelity model
  YAML / DnC
  stable interfaces
  fake components
  local artifact repository
  configuration intent + effective config record
  deterministic executor
  PostgreSQL deployment
  PostgreSQL replication
  node off / node on lifecycle
  first HA mechanism
  benchmark + event history
  checker with PASS / FAIL / NOT_MEANINGFUL
  reproducible experiments

PLANNED
  physical Pi backend
  programmable physical network faults
  PostgreSQL injection-point laboratory
  comparison of multiple HA mechanisms
  OpenClaw slow path
  DuckDB
  FTS / vector / RAG
  full OpenTelemetry integration
  recursive control-plane HA
  BYON
  Android / Linux laptop / MacBook workers

RESEARCH
  autonomous architecture search
  heterogeneous resource optimization
  self-repair across multiple data engines
  crowd / ad-hoc elastic computing
```

### Maturity ladder

Development proceeds from cheap, fast environments toward expensive physical truth. Each stage must produce a complete working result.

```text
M0  Laptop development
    backend/fidelity model, fake service, real PostgreSQL,
    node off/on, crash recovery, replication restoration,
    first HA mechanism, checks and provenance

M1  Physical Pi substrate
    same scenarios on 7 Pi, bootstrap, inventory, local repository,
    real power/network/storage behavior, repeatable deployment

M2  Fault laboratory
    process.pause, network/resource faults, injection points,
    stronger checker and reproducibility

M3  Compare HA mechanisms
    same scenarios and fault profiles across
    pg_auto_failover / Patroni / BiHA / other mechanisms

M4  OpenClaw slow path
    human goal -> desired state / scenario
    deterministic executor remains unchanged

M5  Additional data systems
    DuckDB, FTS, vector search, RAG

M6  Recursive control-plane HA
    controller role migration and state recovery

M7  BYON
    local Wi-Fi, QR enrollment,
    Android, Linux laptop, MacBook, capability scheduling

M8  Autonomous architecture
    resources + workload + SLA + constraints
    -> experiment -> measured architecture choice
```

M0 is **fully laptop-based**. Physical hardware may arrive later without blocking M0.

The laptop backend does not need to be perfectly faithful. Its policy is explicit, recorded, and checked against scenario requirements. Physical Pi is used only for questions that cannot be answered honestly enough on the laptop.

## Near-term goal

The near-term goal is:

> **Run the same experiment reproducibly across execution backends, record the fidelity of each environment, and move a tested scenario from laptop to physical Pi without changing scenario logic.**

The project should prove that:

```text
scenario semantics stay the same
configuration intent stays the same
artifact identity stays explicit
backend-specific state is rendered again
checks remain honest about what the environment can prove
```

## Long-term goal

The long-term goal is a system that can assemble and reshape data architecture for a given objective and available resources.

Examples:

```text
Build the fastest PostgreSQL configuration
that survives loss of any one node.
```

or:

```text
Keep OLTP p95 < 20 ms,
maximize analytical throughput,
and survive one node failure.
```

OpenClaw may later decide **what** should be attempted. The lower deterministic layer still knows **how** to execute and record it.

```text
human goal
   |
   v
OpenClaw slow path
   |
   v
desired state / scenario
   |
   v
deterministic Pi-Cluster executor
   |
   +-- provisioning
   +-- artifact deployment
   +-- service adapters
   +-- tc / cgroups
   +-- power / network adapters
   +-- database adapters
   |
   v
execution backend
```

AI may choose a plan, but actual configuration, binary versions, scenario, effective policy, and experiment record remain exact and reproducible.

---

## Physical configuration

Baseline physical configuration:

- **1 preferred-controller Raspberry Pi**
- **6 worker Raspberry Pi**
- **7 Raspberry Pi total**

The preferred controller normally stays out of the tested data plane and acts as observer/control node. Controller is ultimately a role rather than a permanent machine; recursive control-plane HA is a later stage.

```text
                 Controller
             Raspberry Pi 5
        controller / repo / metrics
          optional local AI later
                     |
              managed Ethernet
                     |
      +------+------+------+------+------+------+
      |      |      |      |      |      |
     n1     n2     n3     n4     n5     n6
          six experimental nodes
```

Six worker machines are deliberate. Five nodes allow, for example, `3 PostgreSQL + 2 DuckDB`, but leave no free machine for topology repair. A sixth worker becomes spare/elastic capacity.

Example:

```text
n1  PostgreSQL primary
n2  PostgreSQL replica
n3  PostgreSQL replica

n4  DuckDB worker
n5  DuckDB worker

n6  spare / elastic node
```

In the long-term slow path, OpenClaw could use `n6` to restore redundancy or replace a lost analytical worker. That behavior is not part of the current deterministic M0 execution path.

---

## Power

The physical Pi-Cluster should be able to operate from batteries.

Each node has its own powerbank. This serves two purposes:

1. the cluster can be demonstrated without mains power;
2. one physical machine can be powered off without affecting the others.

Controller and managed switch should also have independent power.

```text
controller ---- own battery
switch -------- own battery

n1 ------------ own battery
n2 ------------ own battery
n3 ------------ own battery
n4 ------------ own battery
n5 ------------ own battery
n6 ------------ own battery
```

This means roughly eight power sources if the switch has its own powerbank.

For Raspberry Pi 5, powerbanks must support the required USB-C power profile. Selection must consider supported voltage/current combinations, not only total watts.

Charging the whole kit requires one or more high-power multi-port USB-C GaN chargers.

---

## Enclosure

The first version can use an open seven-node cluster frame or a custom mini-rack.

The finished version should become a portable demonstration case with the worker Pis mounted vertically like server blades.

```text
+------------------------------------------------+
| Controller              Managed switch         |
| [ Pi ]                  [ Ethernet ]           |
|                                                |
| [ n1 ] [ n2 ] [ n3 ] [ n4 ] [ n5 ] [ n6 ]    |
|   o      o      o      o      o      o         |
|                                                |
| batteries / power control / charging           |
+------------------------------------------------+
```

Each worker should ideally have:

- visible node number;
- power-off control;
- RGB LED or small status indicator;
- accessible Ethernet;
- easy replacement/addition.

Example status colors:

```text
BLUE     primary
CYAN     replica
GREEN    healthy worker
YELLOW   recovering
PURPLE   reconfiguration
RED      failed
OFF      physically unavailable
```

Physical clarity is part of the project. A person should be able to see that `n3` is a real machine, not an abstract pod.

---

## Controller node

The current controller is deterministic and performs:

```text
scenario execution
backend selection
inventory/state
artifact and configuration coordination
benchmark driving
event history
checks
metrics collection
dashboard backend
```

Local AI inference is optional and belongs to the later OpenClaw slow path.

The preferred controller may be more powerful than worker nodes:

- Raspberry Pi 5 16 GB;
- local storage;
- Active Cooler;
- separate powerbank;
- optional AI accelerator for later local AI experiments.

Worker nodes do not require accelerators.

---

## Worker nodes

For worker nodes, independent failure domains matter more than large RAM on each machine.

Baseline:

```text
6 x Raspberry Pi 5
4 GB or 8 GB RAM
NVMe 256 GB
Active Cooler
individual powerbank
Gigabit Ethernet
```

For the first reproducible version, identical workers are preferable so TPS and latency differences come from topology/configuration/failure rather than accidental hardware variation.

Later, a deliberately heterogeneous cluster may be useful:

```text
n1  4 GB
n2  4 GB
n3  4 GB
n4  8 GB
n5  8 GB
n6  8 GB
```

That gives the future slow path a real resource-placement problem.

---

# Software architecture

## Scenario, controller, and executor

The current architecture begins with the scenario, not with OpenClaw.

```text
scenario
   |
   v
deterministic controller / executor
   |
   +-- node agent
   +-- bench power adapter
   +-- bench network adapter
   +-- cgroup adapter
   +-- PostgreSQL adapter
   +-- HA adapter
   +-- DuckDB adapter
```

The controller does not invent HA decisions. Before HA is introduced, the scenario may define initial static roles and the system observes that the services actually match them. Once an external HA mechanism is introduced, election and promotion belong to that mechanism; Pi-Cluster injects faults, observes roles, and checks outcomes.

Future OpenClaw integration sits above this deterministic layer:

```text
human goal
   |
OpenClaw
   |
desired state / scenario
   |
deterministic controller
```

No AI-generated shell command should become the primary control model.

---

## Bootstrap agent on every node

Each physical worker permanently contains only a small bootstrap/recovery substrate and agent.

The agent should be able to:

1. register with the controller;
2. report hardware profile;
3. fetch an artifact;
4. verify checksum/content identity;
5. apply configuration;
6. create/start systemd units;
7. report state and telemetry;
8. reset experiment state;
9. preserve or reuse verified artifact cache where allowed.

Example registration:

```text
node-4:
  arch: aarch64
  cpu: ...
  memory: 8192 MB
  storage: NVMe
  network: 1 GbE
  state: available
```

Inventory is built from agent registrations, not injected from a hidden bench-side node list.

---

# Local repository

Two repositories have different roles:

```text
Git repository
  source of truth for the project
  code / docs / scenarios / config intent / backend descriptors /
  build recipes / tests

Local artifact repository
  runtime distribution
  ready-to-use binaries / overlays / datasets / manifests
```

A trusted Pi should be able to serve the local artifact repository so demonstrations do not require Internet access or compilation on workers.

Example layout:

```text
repo/
  postgres/
    18/
      vanilla/
      injection/
    19devel/
      vanilla/
      injection/
      experimental/

  extensions/
    pgvector/
    pg_trgm/
    biha/

  duckdb/

  configs/
    postgres/
      base/
      overlays/
      oltp/
      fts/
      rag/
      ha/
      planner-lab/

  scripts/
  workloads/
  checks/
  datasets/
  scenarios/
  manifests/
  build-metadata/
```

For M1, a simple HTTP server and `tar.zst` artifacts are enough.

```text
GET /artifacts/postgres/19devel/injection/postgres.tar.zst
GET /configs/postgres/ha/node-3.yaml
```

Every artifact has a manifest:

```yaml
name: postgres-injection
arch: aarch64
pg_version: 19devel
commit: abcdef1234
configure:
  - --enable-injection-points
compiler: gcc
build_id: pg19-ip-0042
sha256: ...
```

Keep the boundary explicit:

```text
binary != configuration
```

One PostgreSQL binary can serve multiple profiles:

```text
oltp
ha
fts
rag
planner-lab
```

## Configuration intent and effective configuration

Git and the repository preserve **configuration intent**, not only a ready-made `postgresql.conf`.

```text
distribution defaults
        +
Pi-Cluster overlay / diff
        =
desired configuration
```

For example:

```text
configs/postgres/base.yaml
configs/postgres/ha.yaml
configs/postgres/sync-replication.yaml
```

The executor renders real runtime files from this intent.

For reproducibility, each experiment also stores the **exact effective configuration** that actually ran:

```text
experiment-0042/
  scenario.yaml
  artifacts.json

  config-source/
    postgres-ha.yaml

  config-effective/
    postgresql.conf
    pg_hba.conf
    ha-config.yaml

  events.log
  metrics.csv
  checker.json
```

This answers two different questions:

```text
What did we intend to configure?
What actually ran?
```

---

# PostgreSQL binary profiles

A useful set of prepared PostgreSQL builds:

```text
postgres-vanilla
postgres-debug
postgres-injection
postgres-biha / compatible build
postgres-search
```

`postgres-injection` is particularly important.

It should include PostgreSQL injection-point support and, when needed, experimental injection points from the project.

Two builds of the same commit are useful:

```text
postgres-19devel-normal
postgres-19devel-injection
```

This allows instrumentation overhead to be measured separately.

```text
normal build       -> baseline TPS
injection build    -> TPS with injection support
delta              -> instrumentation overhead
```

Every benchmark result must be tied to exact build ID, commit, configuration hash, and backend/fidelity record.

---

# Fault injection

Pi-Cluster supports two broad levels of faults.

## External physical and system faults

```text
power off
power on
reboot

process pause

CPU throttling
memory restriction
disk throttling
network latency
packet loss
network partition
service kill
disk fill
```

`process.pause` is intentionally distinct from `service.kill` and `node.off`:

```text
service.kill
  process dies

node.off
  node disappears from peers

process.pause
  process makes no progress,
  while kernel/network may remain alive
```

This is particularly useful for lease and HA experiments.

Physical power removal remains especially important for the final demonstration.

## Internal PostgreSQL injection points

Injection points make it possible to stop PostgreSQL reproducibly at a specific execution location.

```text
physical/system fault
        +
PostgreSQL internal fault injection
```

This allows experiments not only on distributed-system behavior from the outside, but also on specific PostgreSQL internal states.

Planner experiments and counterfactual tests form another scenario class.

---

# Scenario catalog: examples, not a closed list

The control language should grow from working experiments rather than from an attempt to describe the entire future up front.

Canonical examples:

```text
node off / on
node add / remove
node slow
process pause

network latency / loss
network partition

service kill postgres@n2
primary loss
replica loss
controller loss

failover
switchover
rebuild replica
restore redundancy

PostgreSQL injection point
planner experiment
recovery experiment

join Android
join MacBook
temporary node disappears

...
```

The ellipsis is intentional: this is an **extensible scenario space**, not a closed feature list.

# Pi-Cluster control language

The project needs a small deterministic orchestration and fault-injection language.

A future OpenClaw layer may translate a human goal into this language. The language itself remains deterministic and testable.

## Node lifecycle

```text
node off n3
node on n3
node reboot n3

node add n6
node remove n4

node drain n4
node undrain n4
```

## Degradation

```text
node slow n2 cpu=30%
node slow n2 io=20MB/s
node slow n2 net=10mbit latency=100ms

node degrade n2 memory=512MB

node restore n2
```

## Network failures

```text
node isolate n3

network partition n2 n3
network latency n2 200ms
network loss n2 5%

network heal
```

## Service failures

```text
service stop n1 postgres
service start n1 postgres
service restart n1 postgres
service kill n1 postgres

process pause n1 postgres
process resume n1 postgres
```

## Storage failures

```text
disk fill n4 90%
disk slow n4 latency=50ms
disk readonly n4

storage restore n4
```

Destructive storage operations should be limited to prepared experiment volumes.

## Temporary actions

```text
node off n3 for 30s

node slow n2 cpu=20% for 60s

network partition n1 n2 for 15s
```

---

# Cluster commands

```text
cluster create postgres-ha nodes=n1,n2,n3

cluster scale 3 -> 5
cluster shrink 5 -> 3

cluster add n6
cluster remove n4

cluster rebalance
cluster failover
cluster switchover n1 -> n2

cluster replace n3 with n6

cluster status
cluster topology
cluster health
```

These are target vocabulary examples, not a claim that all commands belong in the first release.

---

# Benchmark commands

```text
bench start pgbench clients=32
bench stop

bench set clients=64

measure tps
measure latency
measure recovery-time
measure replica-lag
```

Mixed workloads may later look like:

```text
bench mixed:
  oltp: 60%
  fts: 20%
  vector: 15%
  analytics: 5%
```

---

# Scenario lifecycle

```text
scenario run node-loss
scenario run network-partition
scenario run slow-replica
scenario run rolling-upgrade
scenario run add-node

scenario pause
scenario resume
scenario abort
```

Laboratory recovery operations are separate from scenario semantics:

```text
reset
factory/reprovision
```

A normal experiment reset clears experiment state but may preserve verified artifact cache.

The guiding rule is:

> **A Pi may be warm, but it must not have hidden state.**


---

# PostgreSQL HA

Pi-Cluster should compare several HA approaches under the same scenarios, workloads, and fault profiles:

```text
vanilla streaming replication
pg_auto_failover
Patroni
BiHA
```

Other mechanisms may be added later.

The key value is not the product list. It is the ability to run the **same experiment** against different HA philosophies and compare measured behavior.

Before an HA mechanism is introduced, Pi-Cluster may use a static role layout defined by the scenario:

```text
n1 primary
n2 replica
n3 replica
```

The controller deploys that layout and observes the real PostgreSQL role through probes such as `pg_is_in_recovery()`. It does not perform election.

Once an HA mechanism is introduced, this changes:

```text
Pi-Cluster:
  injects fault
  observes state
  runs workload
  runs checks
  records history

HA mechanism:
  detects
  elects
  promotes
  fences / rejoins
```

Scenario logic must not secretly implement consensus, election, or fencing.

A basic primary-loss experiment:

```text
deploy HA configuration
start workload

wait 30s
node off current primary

measure:
  failure detection time
  write downtime
  failover time
  acknowledged-write behavior
  TPS recovery

node on former primary

measure:
  reintegration time
  final role
  writability
  restored redundancy
```

A former primary that is simply unreachable does not prove fencing. Final checks must distinguish:

```text
node unavailable
from
node reachable but not writable
```

Another important scenario is:

```text
network partition
```

This is where quorum, lease behavior, split-brain protection, and partial network failure become meaningful. It should be introduced only when the selected HA mechanism has a real decision topology worth testing.

`process.pause` is also important for HA:

```text
processes stop making progress
kernel/network may remain alive
```

It can expose lease and stale-primary failures that ordinary process death does not.

---

# BiHA

BiHA is interesting as a different PostgreSQL HA philosophy.

Possible topology:

```text
n1  BiHA leader
n2  follower
n3  follower

n4  auxiliary / referee role if required by scenario

n5  other workload
n6  spare
```

Pi-Cluster should eventually run the same primary-loss scenario through multiple HA mechanisms:

```text
scenario: primary-loss

pg_auto_failover:
  run
  record

reset

Patroni:
  run
  record

reset

BiHA:
  run
  record

compare
```

The purpose is to compare measured behavior, not claims.

---

# PostgreSQL + DuckDB

Pi-Cluster is not limited to HA.

PostgreSQL and DuckDB can play different execution/storage roles.

Long-term model:

```text
              deterministic control
                     |
          +----------+----------+
          |                     |
   PostgreSQL cluster        DuckDB nodes
      n1 n2 n3                n4 n5
          |                     |
          +------ data ----------+
                     |
                    n6
               spare / elastic
```

PostgreSQL holds mutable operational state.

DuckDB can serve local analytics, Parquet processing, and analytical queries.

Possible data paths:

```text
PostgreSQL
   |
   +-- snapshot -> Parquet -> DuckDB
   |
   +-- CDC -> analytical pipeline
   |
   +-- selected export -> DuckDB
```

Possible target commands:

```text
engine deploy postgres n1,n2,n3
engine deploy duckdb n4,n5

pipeline create pg_to_duckdb
pipeline start pg_to_duckdb
pipeline pause pg_to_duckdb

export postgres table=events format=parquet target=n4

query run postgres "..."
query run duckdb "..."
```

Later, routing may become dynamic:

```text
query route oltp postgres
query route analytics duckdb
query route analytics auto
```

This is future evolution, not M0 scope.

---

# FTS, vector search, and RAG

Another workload class is search and RAG.

One document set may use:

```text
PostgreSQL table
      |
      +-- tsvector / GIN
      |       |
      |      FTS
      |
      +-- embeddings
      |       |
      |    pgvector
      |
      +-- hybrid retrieval
              |
            rerank
              |
             LLM
```

FTS and vector search are different retrieval mechanisms. Hybrid search is its own experiment.

Possible commands:

```text
search mode fts
search mode vector
search mode hybrid

rag enable
rag disable

rag set topk=10
rag set reranker=on

index build fts
index build vector

index drop fts
index drop vector
```

Benchmarks should measure more than speed:

```text
QPS
p50
p95
index build time
index size
Recall@K
RAG latency
freshness lag
```

---

# Mixed workload

A particularly interesting Pi-Cluster experiment combines multiple workload classes.

For example:

```text
60% OLTP
20% FTS
15% vector search / RAG
 5% DuckDB analytics
```

After stabilization, a human or future slow-path controller changes the physical system:

```text
slow n2

off n4

add n6

network partition n1 n3

restore n4
```

The system should show what happened to each SLA:

```text
OLTP TPS
OLTP p95

FTS QPS
Vector QPS
RAG p95

DuckDB analytical throughput

replica lag
recovery time
```

---

# Autonomous architecture experiments

This is a later stage.

OpenClaw does not receive a fixed topology. It receives resources and an objective.

Example:

```text
Available:
  6 worker nodes

Goal:
  maximize TPS
  survive any single-node failure
  OLTP p95 < 20 ms
```

It may propose several configurations:

```text
A:
  PostgreSQL only

B:
  PostgreSQL HA + DuckDB

C:
  PostgreSQL HA + DuckDB + spare

D:
  PostgreSQL + FTS + vector + DuckDB
```

For each candidate:

```text
deploy
warm up
benchmark
inject failure
measure recovery
record result
reset
```

The system then selects the architecture that best satisfies the objective.

```text
resources + SLA + workload
            |
            v
     architecture search
            |
            v
        experiments
            |
            v
      measured choice
```

This is the long-term self-driving data-architecture direction, not a dependency of the current system.

---

# Dashboard

The dashboard should be simple enough to understand from across a room.

Main graph:

```text
TPS
850 |--------------------
800 |                  \
750 |                   \____
700 |                        \---------
    +---------------------------------> time
                         ^
                     node n2 off
```

Events are overlaid on the timeline:

```text
T+30s  n2 power lost
T+32s  failure detected
T+35s  new leader elected
T+38s  writes restored
T+45s  n6 deployed as replica
T+71s  cluster healthy
```

For mixed workloads:

```text
OLTP TPS
OLTP p95

FTS QPS
Vector QPS
RAG latency

DuckDB queries/s

replica lag
CPU
RAM
disk IO
network
```

The dashboard should show topology and experiment validity, not just performance numbers.

A metric should eventually be able to report that it is not meaningful under the effective fidelity policy, using the same semantic-requirement model as checks.

---

# Provenance and reproducibility

Every experiment has its own ID.

Store at least:

```text
Git commit / scenario tag
scenario
backend placement
backend capabilities
allowed relaxations
effective relaxations
instruments
hardware inventory
node roles / observed roles
artifact hashes and build IDs
PostgreSQL commit
DuckDB version
extension versions
configuration intent
effective configuration hashes/files
dataset version / recipe / seed
benchmark parameters
fault timeline
semantic event trace
raw metrics
check results
experiment verdict
```

Example:

```yaml
experiment: exp-0042

source:
  tag: scenario/primary-loss/v3
  commit: abcdef1234

placement:
  n1:
    backend: laptop-nsnode
    role_initial: primary

postgres:
  build: pg19-ip-0042
  commit: abcdef1234

config:
  hash: sha256:...

dataset:
  id: postgres-mailing-list-v1

workload:
  pgbench_clients: 64

fault:
  type: node_power_loss
  node: n1
  at: 30s

fidelity:
  effective_relaxations: []
```

This makes every graph and check traceable to the exact environment that produced it.

---

# Laptop -> repo -> Pi promotion

The transfer unit is not laptop state. It is a **frozen Git commit/tag describing experiment intent**.

The one-way development path is:

```text
develop/debug on laptop
        |
reference run
        |
freeze Git tag
        |
static preflight
        |
run on Pi
        |
compare semantic behavior
```

## Reference run

A laptop reference run records:

```text
experiment id
scenario tag
backend placement
effective fidelity
instruments
artifact identities
effective configuration
semantic trace
checks
```

The reference run is not "physical truth". It is a known behavioral baseline whose limitations are explicit.

Before promotion to Pi, there should be at least one successful laptop run without Class B shortcuts that bypass normal project code paths.

## Freeze

Promote a tag, not a working tree.

Example:

```text
scenario/primary-loss/v3
```

## Static preflight

Before spending time on the physical cluster, verify:

```text
pi5.provides covers scenario requirements
hardware overlays exist
mapping requirements fit current inventory
required aarch64 artifacts exist in repository
build IDs and hashes are consistent
dataset can be reproduced
```

If preflight fails, do not touch the Pi cluster.

## Pi run

The same scenario and configuration intent are used.

Target-specific state is rendered again:

```text
aarch64 artifacts from the same recipes
dataset from the same deterministic recipe
effective config = intent + hw/pi5-* overlay
mapping from current Pi inventory
```

No manual configuration editing on Pi is part of the normal workflow.

## Comparison

Compare **semantic trace**, not raw timing equality.

Example normalized trace:

```text
workload_started
primary_failed
failure_detected
new_primary_selected
new_primary_writable
redundancy_restored
checks_completed
```

Compare:

```text
roles
state transitions
causal ordering
checks
```

Analyze timing and hardware observations separately.

Expected differences:

```text
timing
hardware behavior
checks that were NOT_MEANINGFUL on laptop
but become meaningful on Pi
```

Unexpected differences:

```text
different causal order
different code-path behavior
FAIL where the laptop proved the same required semantics
```

Unexpected differences are fixed in the repository, followed by a new laptop reference run, a new tag, and then another Pi run. They are not repaired by hand on the physical node.

---

# Example complete scenario

A future mixed scenario may look like:

```text
cluster create postgres-ha nodes=n1,n2,n3

engine deploy duckdb n4,n5

bench mixed start

wait 30s

node off n2

wait until postgres healthy

measure recovery-time

node off n4

wait 10s

engine deploy duckdb n6

wait until analytics healthy

node on n2

cluster add n2

cluster rebalance

node slow n3 net=5mbit latency=100ms for 60s

wait 60s

node restore n3

bench stop

report
```

A person should be able to watch topology and performance change together.

---

# Example demonstration

Long-term demonstration:

A person says:

> Build a PostgreSQL HA cluster, add analytical processing with DuckDB, keep OLTP p95 below 20 ms, and survive loss of one node.

OpenClaw may later:

```text
discover six workers

select:
  n1 n2 n3 -> PostgreSQL
  n4 n5    -> DuckDB
  n6       -> spare

produce desired state / scenario
```

The deterministic Pi-Cluster executor then:

```text
resolves artifacts
applies configuration
starts services
warms up
starts benchmark
```

The person physically powers off `n2`.

```text
n2 OFF
```

The selected PostgreSQL HA mechanism detects and handles the failure. Pi-Cluster observes and checks the result.

Later, the slow path may decide to use `n6` to restore redundancy.

Then the person powers off a DuckDB worker. The slow path may decide to leave analytics degraded, use another resource temporarily, or reshape topology.

This is where the demonstration moves from fault tolerance into **agency over physical data architecture**.

---

# Budget

The budget below is approximate. Raspberry Pi, NVMe, and powerbank prices vary significantly by supplier and country.

## Base kit: 7 Raspberry Pi

| Component | Qty | Approx. unit | Approx. total |
|---|---:|---:|---:|
| Raspberry Pi 5 16 GB, controller | 1 | €250-300 | €250-300 |
| Raspberry Pi 5 4/8 GB, workers | 6 | €130-185 | €780-1110 |
| AI accelerator for controller | 1 | €180-220 | €180-220 |
| NVMe 256 GB for workers | 6 | €30-50 | €180-300 |
| M.2 HAT / storage adapters | 6 | €10-15 | €60-90 |
| Controller storage | 1 | €30-50 | €30-50 |
| Active Cooler | 7 | €6-10 | €42-70 |
| Worker/controller powerbanks | 7 | €40-50 | €280-350 |
| Switch battery/power solution | 1 | €30-50 | €30-50 |
| 8-port managed Gigabit switch | 1 | €25-40 | €25-40 |
| Ethernet cables | 7 | €3-5 | €21-35 |
| Cluster frame / case | 1 | €80-150 | €80-150 |
| USB-C GaN charging | 1 set | €100-200 | €100-200 |
| buttons, LEDs, USB-C, mounting, spare cables | | | €100-150 |

Approximate total:

```text
economy configuration:       ~€2,150
normal configuration:        ~€2,500
finished demo suitcase:      ~€2,700-3,000
```

The largest budget variable is worker RAM and Raspberry Pi unit price.

A practical first version:

```text
1 x Pi 5 16 GB controller
6 x Pi 5 4 GB workers
```

If heavier DuckDB/RAG experiments are expected, some workers can use 8 GB.

For example:

```text
3 x 4 GB
3 x 8 GB
```

For clean comparative benchmarks, six identical workers are preferable.

---

# What to buy

Practical first-version BOM:

```text
1 x Raspberry Pi 5 16 GB

6 x Raspberry Pi 5 4 GB or 8 GB

1 x optional AI accelerator for later slow-path work

7 x Active Cooler

6 x NVMe 256 GB
6 x compatible M.2 adapters

1 x controller storage

7 x suitable USB-C powerbanks
1 x battery/power solution for switch

1 x 8-port programmable managed Gigabit switch

7 x Ethernet cables

1 x cluster frame / custom rack

1 x multi-port USB-C GaN charging system

buttons / LEDs / wiring / mounting hardware
```

---

# Target software scenarios

The scenarios described in this document - PostgreSQL HA, multiple HA mechanisms, PostgreSQL + DuckDB, search/RAG, injection points, and autonomous architecture - remain target capabilities.

Their existence in the architecture does **not** mean they belong in the first version.

The maturity ladder determines implementation order.

# What the project should become

Pi-Cluster 3.14 should eventually be:

1. a portable distributed-data-systems laboratory;
2. a fault-injection bench;
3. an environment for comparing PostgreSQL HA architectures;
4. a PostgreSQL/DuckDB playground;
5. an FTS/vector/RAG laboratory;
6. a PostgreSQL injection-point laboratory;
7. an environment for autonomous data-architecture experiments;
8. a visible conference demonstration.

The long-term idea remains:

> **The system receives resources, workload, and an objective. It assembles an architecture, measures it, survives physical faults, and reshapes itself in front of a person.**

But this long-term statement must not obscure the current engineering path:

> **First make the same scenario run honestly and reproducibly from laptop to Pi. Then add autonomy above that deterministic substrate.**

---

# Extensions: recursive HA, observability, BYON, and experiment discipline

The sections below **extend** the practical core. They are not prerequisites for M0.

# Recursive High Availability

The physical `1 preferred controller + 6 workers` layout is a convenient starting point, but the controller should not remain a permanent single point of failure.

Logical model:

```text
7 controller-capable Pi nodes
1 node currently holds the preferred controller role
6 nodes are currently available for workloads
```

One Pi may be physically stronger and carry the main AI accelerator, but **controller is a role, not a permanent machine**.

```text
                replicated control state
                        |
          +-------------+-------------+
          |             |             |
         n0            n5            n6
      controller      standby       standby
```

If `n0` disappears:

```text
n0 OFF
  |
controller lease lost
  |
n5 becomes controller
  |
  +--> restore desired state
  +--> rediscover nodes
  +--> resume experiment control
  +--> restart observability
  +--> restore controller redundancy
```

Recursive principle:

```text
data plane survives node loss
control plane survives controller loss

control plane can restore data plane
remaining nodes can restore control plane
```

There is no need to replicate an OpenClaw process image. Replicate durable state sufficient to restore control.

Critical recovery artifacts, manifests, and configuration state must not exist only on the current controller. Several trusted Pi nodes should have enough data to:

```text
take over controller role
+
serve critical artifacts
+
reconstruct desired state
+
continue or safely terminate experiment
```

---

# Fast path and slow path

Database survival must not depend on local LLM latency.

```text
FAST PATH
  database HA
  leader election
  promotion
  watchdogs
  leases
  local restart
  deterministic

SLOW PATH
  OpenClaw
  topology repair
  spare-node deployment
  workload placement
  architecture optimization
```

Example:

```text
primary dies
   |
HA mechanism restores service
   |
OpenClaw later observes lost redundancy
   |
OpenClaw selects n6
   |
new replica is deployed
```

Main rule:

> **AI may optimize survival, but basic survival must not depend on AI.**

---

# Laboratory substrate, artifact cache, and reset

Each trusted Pi contains a minimal recovery substrate that an experiment cannot destroy.

```text
+----------------------------------+
| bootstrap / recovery substrate   |
| node identity                    |
| bootstrap agent                  |
| minimal networking               |
| artifact installer               |
| recovery tools                   |
+----------------------------------+
| verified artifact cache          |
| binaries / packages / datasets   |
+----------------------------------+
| experiment state                 |
| PostgreSQL / DuckDB              |
| PGDATA                           |
| rendered configs                 |
| temporary volumes                |
| runtime logs                     |
+----------------------------------+
```

The three layers have different lifetimes.

```text
persistent substrate
  remains across experiments

artifact cache
  may remain across experiments
  but is never the source of truth

experiment state
  reset/recreated for a new experiment
```

A normal reset:

```text
stop experiment services
+
wipe/recreate experiment state
+
resolve exact artifact identities
+
reuse verified cache or fetch missing artifacts
+
render effective configuration
+
start experiment services
```

A factory/reprovision operation restores the whole node including the substrate.

An artifact may already exist on a Pi, but use is permitted only if its exact build/content identity matches the scenario/tag and manifest.

> **A Pi may be warm, but it must not have hidden state.**

Disk-level snapshot is not required for the first version.

---

# Power as part of provenance

A powerbank is not only a power source; it may influence an experiment. Provenance should therefore include power model/state.

```yaml
node: n3
board:
  model: Raspberry Pi 5
  ram: 8GB
storage:
  model: ...
  firmware: ...
power:
  source: powerbank
  model: ...
  charge_start: 87%
network:
  switch_port: 4
  switch_firmware: ...
```

Two hard-failure modes:

```text
manual:
  a person physically presses POWER CUT

automated:
  bench controls relay / MOSFET / power controller
```

For manual faults, the requested fault time and observed fault time may differ. The experiment record should distinguish commanded/applied/observed timing where possible.

Central demonstration:

> **Pick a node. Kill it. Watch the system survive.**

Then:

> **Now kill the controller.**

The second gesture belongs to the later recursive-control-plane stage.

---

# Network fault mechanics

`network degradation` and a real `network partition` are different experiments.

For degradation:

```text
tc/netem:
  latency
  jitter
  packet loss
  bandwidth restriction
  reordering
```

Degradation belongs inside the node/agent boundary where possible.

For a physical partition, the switch should provide external network control:

```text
port disable
port isolation
VLAN split
switch ACL
```

Therefore the switch should be not merely managed, but **programmable managed**:

```text
8+ Gigabit ports
remote CLI/API/SNMP or equivalent
scriptable port state
scriptable VLAN/isolation
```

The exact switch model is intentionally deferred until the minimal `port.*` interface is clear.

---

# OpenTelemetry

OTel can serve as a common observability transport:

```text
workers
  |
  +-- node metrics
  +-- PostgreSQL metrics
  +-- DuckDB metrics
  +-- traces
  +-- executor actions
  +-- future OpenClaw actions
  |
  v
OTel Collector
  |
  +--> metrics backend
  +--> trace backend
  +--> dashboard
```

But:

```text
observability telemetry != experiment truth
```

The authoritative experiment record remains a separate append-only history.

All components use one `experiment_id`.

Nodes should keep a small local journal so critical events are not lost exactly when controller/collector connectivity disappears.

---

# Generator, Nemesis, Observer, Checker

An experiment separates four roles:

```text
Generator  - workload
Nemesis    - faults
Observer   - performance/state
Checker    - correctness
```

```text
              Experiment planner
                     |
        +------------+------------+
        |                         |
    Generator                  Nemesis
        |                         |
        +------------+------------+
                     |
              system under test
                     |
              +------+------+
              |             |
           Observer       Checker
              |             |
              +------+------+
                     |
                  Result
```

For PostgreSQL, checks may include:

```text
acknowledged writes remain visible
no impossible duplicate logical operations
replicas converge
no conflicting writable leaders
application invariants remain true
observed role matches required state
required nodes did not disappear from the comparison
```

Check declarations are binding: a check may only consume observations it explicitly declares. Missing observations and probe errors must not silently shrink the expected set and produce a vacuous PASS.

For search/RAG:

```text
latency SLA
Recall@K
retrieval relevance
freshness
```

TPS and recovery time alone are not enough.

---

# Built-in Wi-Fi and BYON

Pi-Cluster may later provide its own Wi-Fi network and operate without external Internet.

```text
SSID: Pi-Cluster-3.14
```

QR enrollment:

```text
scan QR
   |
connect to Pi-Cluster Wi-Fi
   |
open local enrollment page
   |
approve "Join experiment"
   |
device reports capabilities
   |
controller benchmarks/classifies device
   |
temporary node appears in topology
```

Possible temporary nodes:

```text
Android phone
Linux laptop
MacBook
Orange Pi
other ARM64 SBC
x86_64 laptop
```

Scheduling is capability-based.

Android may be suitable for:

```text
DuckDB
Parquet scan
embedding worker
RAG retrieval
benchmark client
generic stateless compute
```

An arbitrary phone must not become a trusted PostgreSQL HA node.

Temporary nodes get short-lived credentials and do not receive:

```text
controller signing keys
database superuser credentials
HA secrets
control-plane replication state
persistent private datasets
```

---

# Multi-platform artifact repository

The local repository may later become multi-platform:

```text
repo/
  linux-aarch64/
  linux-x86_64/
  android-arm64/
  macos-arm64/
```

Artifact selection:

```text
task requirements
+
node capabilities
+
OS / ABI
+
architecture
+
trust class
=
compatible artifact
```

The scenario should refer to a logical artifact identity, not hard-code an architecture-specific file path.

Critical recovery artifacts should be replicated across several trusted Pi nodes rather than existing only on the current controller.

---

# Elastic physical computing demo

The demonstration may eventually have two opposite gestures.

Remove compute:

```text
visitor powers off n3
capacity drops
HA reacts
slow path may later restore redundancy
```

Add compute:

```text
visitor scans QR
Android joins

another visitor joins with MacBook

topology:
6 nodes
7 nodes
8 nodes
10 nodes
```

Compute can literally be **removed with a button** and **added with a QR code**.

Trusted durable nodes and temporary compute are different resource classes. BYON does not mean any phone becomes an HA node. Temporary nodes are assumed to be able to disappear without warning.

---

# M0

M0 is a **fully laptop-based development stage**.

Its purpose is not to perfectly emulate Pi hardware. It is to make iteration fast while keeping fidelity explicit and conclusions honest.

The practical M0 path now is:

```text
M0.1 repository + backend catalog + scenario model
M0.2 fake service smoke scenario
M0.3 real PostgreSQL primary/standbys
M0.4 node.off with real client silence
M0.5 node.on and PostgreSQL crash recovery
M0.6 replication restoration
M0.7 binding check declarations and anti-vacuity tests
M0.8 first external HA mechanism
```

The M0 implementation may continue to strengthen namespace/storage isolation as required by real experiments.

Not gates for M0:

```text
physical power control
physical switch partition
recursive controller HA
critical repository replication across physical nodes
BYON
AI accelerator
OpenClaw
```

These remain architectural directions for later stages.

---

# Related work / reading

## Jepsen
Generator, nemesis, history, checker:
https://github.com/jepsen-io/jepsen

## Chaos Mesh
Fault taxonomy:
https://chaos-mesh.org/docs/

## Toxiproxy
Controlled TCP degradation:
https://github.com/Shopify/toxiproxy

## ChaosBlade
Fault-injection vocabulary:
https://github.com/chaosblade-io/chaosblade

## Pumba
Compact process/network fault model:
https://github.com/alexei-led/pumba

## FoundationDB deterministic simulation
https://apple.github.io/foundationdb/testing.html

## Antithesis
https://antithesis.com/docs/

## TigerBeetle VOPR
https://docs.tigerbeetle.com/concepts/safety/

## Raspberry Pi cluster tutorial
https://www.raspberrypi.com/tutorials/cluster-raspberry-pi-tutorial/

## Jeff Geerling Pi clusters
https://github.com/geerlingguy/pi-cluster

## Turing Pi
https://turingpi.com/

## ClusterHAT
https://clusterhat.com/

## PostgreSQL injection points
https://www.postgresql.org/docs/current/xfunc-c.html

## Patroni
https://patroni.readthedocs.io/

## pg_auto_failover
https://github.com/hapostgres/pg_auto_failover

## DuckDB PostgreSQL integration
https://duckdb.org/docs/stable/core_extensions/postgres

## pg_duckdb
https://github.com/duckdb/pg_duckdb

## NoisePage
https://db.cs.cmu.edu/projects/noisepage/

# Extended project formula

The original long-term statement remains:

> **The system receives resources, workload, and an objective. It assembles an architecture, measures it, survives physical faults, and reshapes itself in front of a person.**

The practical substrate underneath that vision is now clearer:

```text
deterministic scenarios
+
explicit backend semantics and fidelity
+
laptop-first development
+
physical Raspberry Pi cluster
+
reproducible promotion laptop -> repo -> Pi
+
recursive control-plane HA
+
OpenTelemetry observability
+
Generator / Nemesis / Observer / Checker
+
multi-platform local artifact repository
+
temporary Android / laptop / MacBook nodes
+
future OpenClaw slow path
=
elastic physical autonomous data laboratory
```

Two long-term demonstration gestures:

> **Pick a node. Kill it. Watch the system survive.**

> **Scan the QR code. Add your phone. Watch the system grow.**

And the engineering rule underneath both:

> **Develop fast on the laptop. Use Pi only for the physics that only Pi can tell us.**
