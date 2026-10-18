# GEbPDCM + LTPM -- v2 (Python model)

This is **v2**, the pure-Python version of the project: all 4 schemes
(`baseline`, `gebpdcm`, `gebpdcm_geo`, `gebpdcm_ltpm`) are computed by
`src/simulator.py`'s tick loop, an aggregate per-node discrete-time model.
NS-3 is used only once, to export the real 100-node topology
(`data/topology.csv`) -- nothing else in this version talks to NS-3.

See `~/computer_networks_project/ns3_simulation` for the sibling version where the same
GEbPDCM/GEO/LTPM logic instead runs inside a real NS-3 packet-level
simulation, acting on real per-node WiFi queues, real packets, and real
trace-driven trust. See the project's **Lab Report PDF** for the full
comparison and why the two versions' results disagree despite implementing
the same algorithm (short version: v2 models capacity as independent
per-node buckets, where moving load off an overloaded node always helps by
construction; v1's real network shares one contended radio channel, where
rerouting adds airtime rather than relieving it).

## Layout

```
python_model/
├── data/
│   └── topology.csv          # 100 real node positions, exported by NS-3 (shared with v1)
├── src/                      # the Python control layer -- THE engine in this version
│   ├── config.py             # every tunable parameter, paper's Table 1 + our own
│   ├── topology.py           # Node, seeded random topology / NS-3 topology loader
│   ├── status.py             # Eq 2-5: predicted load, density, status, classification
│   ├── metrics.py            # Eq 9-12: delay, throughput, PDR, drop ratio + energy
│   ├── migrate.py            # Eq 6-8: priority, share decision, migration + target dispatch
│   ├── geo.py                # Golden Eagle Optimizer
│   ├── ltpm.py                # trust scoring + predictive forecasting
│   └── simulator.py          # the tick loop; run(scheme, speed, packet_size_kb)
├── ns3/
│   └── gebpdcm-baseline.cc   # topology-export scaffold only (no congestion agent in v2)
├── run_all.py                 # runs all 4 schemes x 5 speeds (+ packet sizes), writes results/*.csv
├── plot_results.py            # 5 graphs + comparison_table.csv
├── demo.py                    # live animated demo -> results/demo.gif
└── results/                   # everything run_all/plot_results/demo produce
```

## How to run it

```bash
cd ~/computer_networks_project/python_model
python3 run_all.py          # ~5-10 seconds, pure Python, no NS-3 involved
python3 plot_results.py     # writes the 5 PNGs + comparison_table.csv
python3 demo.py             # optional: results/demo.gif
```

See the project's **Run / Simulator Guide PDF** for full step-by-step
instructions covering both v1 and v2.

## What's implemented (equation -> file -> function)

- Eq 2 (predicted load) -> `src/status.py` -> `predict_data_load()`
- Eq 3 (node density) -> `src/status.py` -> `node_density()`
- Eq 4 (node status) -> `src/status.py` -> `node_status()`
- Eq 5 (congestion classification) -> `src/status.py` -> `congestion_score()`, `classify()`
- Eq 6 (user priority) -> `src/migrate.py` -> `user_priority()`
- Eq 7 (first-user check) -> `src/migrate.py` -> `is_first_user()`
- Eq 8 (share decision) -> `src/migrate.py` -> `should_share()`
- Migration execution -> `src/migrate.py` -> `migrate_half()`
- Eq 9-12 (delay, throughput, drop ratio, PDR) -> `src/metrics.py`
- **GEO** -> `src/geo.py` -> `fitness()`, `select()`
- **LTPM** -> `src/ltpm.py` -> `update_trust()`, `trusted()`, `forecast_load()`, `should_preemptively_migrate()`

For the line-by-line "why this and not that" explanation of every function
above, see the project's **Study / Prep Guide PDF**.
