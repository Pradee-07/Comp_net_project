# GEbPDCM + LTPM -- v1 (real NS-3)

This is **v1**, the real-NS-3 version of the project: GEbPDCM's
threshold+migration, GEO's attack/cruise search, and LTPM's trust/forecast
logic all run **inside a real NS-3 packet-level simulation**
(`ns3/gebpdcm.cc`), acting on real per-node WiFi queues, real packets, and
real trace-driven trust -- not an abstracted model.

See `~/computer_networks_project/python_model` for the sibling version where the same
algorithm is computed by a pure-Python aggregate model instead, and the
project's **Lab Report PDF** for the full comparison and why the two
versions' results disagree despite implementing the same algorithm (short
version: this version's real network shares one contended radio channel,
where rerouting adds airtime rather than relieving it; v2 models capacity
as independent per-node buckets, where moving load off an overloaded node
always helps by construction).

## Layout

```
ns3_simulation/
├── ns3/
│   ├── gebpdcm.cc             # THE scenario: all 4 schemes, real congestion agent
│   ├── gebpdcm-baseline.cc    # earlier baseline-only scaffold, kept as reference
│   └── run_sweep.sh           # runs all 4 schemes x 5 speeds through gebpdcm.cc
├── data/
│   ├── topology.csv           # 100 real node positions, exported by NS-3
│   └── ns3_<scheme>_speed<N>.csv  # real per-run metrics, 20 files (4 schemes x 5 speeds)
├── src/                       # Python reference implementation (design-validated first,
│   │                          # then ported to ns3/gebpdcm.cc -- no longer drives the
│   │                          # reported numbers, except the packet-size sweep)
│   ├── config.py              # every tunable parameter, paper's Table 1 + our own
│   ├── topology.py            # Node, seeded random topology / NS-3 topology loader
│   ├── status.py              # Eq 2-5: predicted load, density, status, classification
│   ├── metrics.py             # Eq 9-12: delay, throughput, PDR, drop ratio + energy
│   ├── migrate.py             # Eq 6-8: priority, share decision, migration + target dispatch
│   ├── geo.py                 # Golden Eagle Optimizer (Python reference)
│   ├── ltpm.py                # trust scoring + predictive forecasting (Python reference)
│   └── simulator.py           # the tick loop; run(scheme, speed, packet_size_kb)
├── run_all.py                  # speed sweep <- real NS-3 CSVs; packet-size sweep <- Python
├── plot_results.py             # 5 graphs + comparison_table.csv
├── demo.py                     # live animated demo (Python model) -> results/demo.gif
└── results/                    # everything run_all/plot_results/demo produce
```

## How it works now

GEbPDCM's threshold+migration, GEO's attack/cruise search, and LTPM's
trust/forecast logic all run **inside `ns3/gebpdcm.cc`**, acting on real
packets and real per-node state:

- **Load** is sampled from each node's real WiFi MAC transmit queue
  (`WifiMacQueue::GetNBytes()`) every simulated second -- not a synthetic
  random draw.
- **Migration** is a real, observable network effect: a bounded-duration
  (0.5s) `Ipv4StaticRouting` host-route override that forces a congested
  node's traffic through the chosen neighbour instead of AODV's own route,
  approximating Eq6-8's "share 50% of load with a neighbour."
- **GEO**'s attack/cruise vector search (20 eagles, 30 iterations) runs
  over real neighbour positions and a fitness function built from each
  candidate's real queue load, real distance, and real status.
- **LTPM**'s trust EWMA is fed by real `Ipv4L3Protocol` Tx/Drop trace
  counts (not a coin flip), and its forecast is a linear extrapolation over
  each node's real load history.

`src/*.py` is kept as the original design-reference implementation -- the
paper's equations worked out and validated in Python first, before being
ported to C++ -- but only `packet_size_sweep.csv` still comes from it (the
priority speed sweep does not; see spec's own cut order).

## How to run it

All commands run from inside WSL (Ubuntu).

**1. Re-run the full real NS-3 sweep (4 schemes x 5 speeds, ~5 minutes):**
```bash
~/computer_networks_project/ns3_simulation/ns3/run_sweep.sh
```
Rebuilds `ns3/gebpdcm.cc` and writes `data/ns3_<scheme>_speed<N>.csv` (20
files) plus a fresh `data/topology.csv`.

**2. Load those into the results pipeline (+ the Python packet-size sweep):**
```bash
cd ~/computer_networks_project/ns3_simulation
python3 run_all.py
```
Writes `results/speed_sweep.csv` (real NS-3), `results/packet_size_sweep.csv`
(Python reference model), `results/all_results.csv`.

**3. Generate the 5 graphs + comparison table:**
```bash
python3 plot_results.py
```
Writes `results/delay_vs_speed.png`, `throughput_vs_speed.png`, `pdr_vs_speed.png`,
`drop_ratio_vs_speed.png`, `energy_vs_speed.png`, `comparison_table.csv`.

**4. Generate the live demo animation (Python model, unchanged):**
```bash
python3 demo.py
```
Writes `results/demo.gif`. Optional flags: `--scheme gebpdcm_geo`, `--speed 30`.

## What each scheme means

| Scheme | What it does |
|---|---|
| `baseline` | Plain AODV forwarding, no congestion control at all |
| `gebpdcm` | The paper as written: threshold check + migrate to nearest free neighbour |
| `gebpdcm_geo` | Same, but a real Golden Eagle Optimizer search picks the migration target |
| `gebpdcm_ltpm` | Same as GEO, plus trust-filtered targets and forecast-based preemptive migration (**our novelty**) |

## A real finding, reported as found

Across all 5 speeds, `baseline` (no congestion control) beats all three
congestion-controlled schemes on delay, throughput and PDR -- e.g. at
10 m/s, PDR is 0.205 (baseline) vs 0.160/0.148/0.148 (GEbPDCM/GEO/LTPM). On
a real, shared 2Mbps DSSS channel, rerouting a congested node's traffic
through a neighbour adds a hop across the *same* contended medium rather
than relieving it, and the repeated static-route overrides add AODV/routing
churn. GEO and LTPM's smarter target selection claw back a little of the
gap versus plain nearest-free migration at higher speeds, but not all of
it. This is documented as a genuine result in the report's Discussion
(Section 7), not hidden or re-tuned away -- consistent with how the paper's
own claimed GEO/primal-dual framing gap is documented.

## What's implemented (equation -> file -> function)

- Eq 2-5 (predicted load, density, status, classification) -> `ns3/gebpdcm.cc` -> `PredictDataLoad`, `NodeDensity`, `NodeStatusScore`, `Classify` (Python reference: `src/status.py`)
- Eq 6-8 (priority, share decision, migration) -> `ns3/gebpdcm.cc` -> `SelectMigrationTarget`, `InstallMigrationRoute` (Python reference: `src/migrate.py`)
- Eq 9-12 (delay, throughput, drop ratio, PDR) -> NS-3's own FlowMonitor, in `ns3/gebpdcm.cc::main()`
- **GEO (our contribution)** -> `ns3/gebpdcm.cc` -> `GeoFitness()`, `GeoSelect()` (Python reference: `src/geo.py`)
- **LTPM (our contribution)** -> `ns3/gebpdcm.cc` -> `UpdateTrust()`, `Trusted()`, `ForecastLoad()`, `ShouldPreemptivelyMigrate()`, fed by `TxTraceCallback`/`DropTraceCallback` (Python reference: `src/ltpm.py`)

## Showing it to the examiner

1. Open `ns3/gebpdcm.cc`'s header comment -- explains why migration has no
   direct NS-3 equivalent and how it's implemented as a real static-route
   override, and the earlier HR/DSSS WiFi PHY bug (found via a debug-profile
   NS_LOG rebuild) that had to be fixed before any of this could deliver a
   single packet.
2. Walk through `GeoSelect()`/`GeoFitness()` -- the attack/cruise vector
   loop the paper's title claims but never builds, now running on real
   neighbour positions.
3. Walk through `UpdateTrust()`/`TxTraceCallback()`/`DropTraceCallback()` --
   trust fed by real per-node packet outcomes, not a coin flip.
4. Run `~/networks_project/ns3/run_sweep.sh` (or point at the already-saved
   `data/ns3_*.csv`), then `python3 run_all.py && python3 plot_results.py`,
   and open the 5 PNGs in `results/` -- point out baseline beating all three
   congestion-controlled schemes, and be ready to explain why (see "A real
   finding" above and report Section 7).
5. `src/*.py` is the design-reference implementation -- useful to show the
   same equations worked out in a simpler, readable form before the C++ port.
6. Play `results/demo.gif` for the visual, animated version of the Python
   reference model (not re-pointed at NS-3 -- lowest-priority spec item).
