# Late-Stage Labor & Capital Simulation

## Overview

This project is a large-scale, **Python-native simulation** of labor scarcity, economic precarity, and population-level displacement in a modern late-stage capitalist system.

Rather than assuming “everyone who wants a job can have one,” the model explicitly enforces a **finite and declining number of job slots** relative to population size. Workers compete probabilistically for employment each month, experience income volatility and shocks, burn or replenish financial buffers (“runway”), and may eventually **exit** the formal system after prolonged insolvency and unemployment.

The goal is not prediction.
The goal is **structural exploration**: understanding how simple, empirically grounded rules can produce emergent macro-level outcomes.

---

## Core Concepts

### Workers

Each worker is an agent with:

* **Capital (runway)**: months of basic expenses they can cover
* **Tier**: low / middle / high income potential
* **Employment status**: employed or unemployed
* **Unemployment duration**: consecutive months without work
* **State**: stable, precarious, insolvent, or exited

### Employment is Capacity-Constrained

* Only a fixed fraction of *active* workers can be employed each month.
* That fraction **declines over time**, modeling automation, consolidation, and productivity without redistribution.
* Employment is assigned probabilistically at scale (accurate in expectation for large N).

### Cashflow

* Employed workers generally stabilize or grow runway slightly.
* Unemployed workers burn runway each month.
* Random **shocks** (medical bills, emergencies) and **separations** add volatility.

### Exit

“Exit” does **not** mean death.

It represents long-term displacement from tracked, stable economic participation:

* long-term unemployment,
* disability,
* informal labor,
* homelessness,
* incarceration,
* permanent withdrawal from the labor force.

Exit becomes more likely when a worker is:

* insolvent, and
* unemployed for a sustained period.

---

## Why This Model Exists

Most economic models assume:

* full employment,
* representative agents,
* or equilibrium conditions.

This simulation instead explores:

* **job scarcity as a first-class constraint**,
* **buffer sensitivity** (how small shocks cascade when savings are thin),
* **path dependence** (unemployment duration matters),
* **nonlinear phase shifts** (small parameter changes can cause mass exits).

The result is a system where:

> the median can look “fine” while the lower tail collapses.

---

## Requirements

* Python 3.9+
* NumPy

No pandas. No spreadsheets. No external dependencies.

---

## Running the Simulation

### Quick test (recommended first)

```bash
python sim.py --N 2000000 --months 24 --chunk 500000 --sample 200000
```

### Large run (100 million agents)

For very large populations, use disk-backed arrays (memmap):

```bash
python sim.py \
  --N 100000000 \
  --months 120 \
  --chunk 5000000 \
  --sample 1000000 \
  --memmap \
  --memmap-dir sim_mem \
  --out sim_100m_series.npz
```

> ⚠️ Expect long runtimes and heavy disk I/O. NVMe strongly recommended.

---

## Output

The simulation produces a compressed `.npz` file containing:

* `active`: active population per month
* `exited`: cumulative exits per month
* `employed`: employed count per month
* `unemployed`: unemployed count per month
* `mean_runway_sample`
* `p10_runway_sample`
* `p50_runway_sample`
* `p90_runway_sample`
* `hist_runway_sample`: histogram over runway bins (for heatmaps)
* `final_effective_job_slot_ratio`

These outputs are designed to feed directly into visualization pipelines.

---

## Interpretation Guidelines

* **This is not a forecast.**
  It is a *structural model*, not a predictive one.

* **Exit ≠ death.**
  Exit is best read as “falls out of stable, visible economic participation.”

* **Employment rates here may look high or low** depending on parameters.
  Real economies mask surplus labor via underemployment, debt, caregiving, informal work, and policy—those are *not* yet fully modeled.

* **Results are highly sensitive to buffers.**
  Increasing initial runway or slowing job-slot decline dramatically reduces exits.

---

## Model Limitations (Important)

This version does **not** yet model:

* underemployment or gig work as a separate state,
* household pooling of resources,
* debt accumulation,
* government policy responses (benefits, austerity),
* demographic change,
* migration or re-entry from EXITED.

These are deliberate omissions to keep the core dynamics legible.
