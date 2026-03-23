#!/usr/bin/env python3
"""
Late-stage labor/capital simulation (Python-native, scalable)

Core ideas:
- Workers have "capital" measured in runway months (buffer).
- Each month, only a fraction of ACTIVE workers can be employed (job slots).
- Job slot fraction declines over time (automation / consolidation proxy).
- Unemployment burns runway; employment slightly replenishes or stabilizes it.
- Random shocks + separations add volatility.
- Exit happens probabilistically when insolvent + prolonged unemployment.

This is a population-level model: vectorized NumPy + chunking.
For 100M workers, use --memmap to store arrays on disk.
"""

import os
import argparse
from dataclasses import dataclass
import numpy as np

# =========================
# State encoding
# =========================
STABLE      = np.uint8(0)
PRECARIOUS  = np.uint8(1)
INSOLVENT   = np.uint8(2)
EXITED      = np.uint8(3)

STATE_NAMES = {
    int(STABLE): "stable",
    int(PRECARIOUS): "precarious",
    int(INSOLVENT): "insolvent",
    int(EXITED): "exited",
}

@dataclass
class SimParams:
    seed: int = 1234
    months: int = 120  # 10 years (monthly ticks)

    # Runway months (buffer) distribution
    capital_logn_mu: float = 1.15
    capital_logn_sigma: float = 0.85
    capital_cap_max: float = 48.0

    # Tier mixture (low/mid/high)
    tier_probs: tuple = (0.45, 0.40, 0.15)

    # Employment constraint
    job_slot_ratio0: float = 0.62         # fraction of active that could be employed at t=0
    job_slot_decline: float = 0.0015      # monthly decline in slots (~1.8%/year)
    hire_friction: float = 0.92           # only this fraction of slots fill each month

    # Monthly runway change when employed/unemployed (by tier)
    # (Think: income - expenses expressed as months of expenses saved/burned per month)
    rev_employed_mu: tuple = (0.02, 0.08, 0.20)
    rev_unemployed_mu: tuple = (-0.40, -0.32, -0.22)
    rev_sigma: tuple = (0.10, 0.08, 0.07)

    # Shocks (random expenses)
    shock_prob_monthly: float = 0.02
    shock_cost_mu: float = 0.5
    shock_cost_sigma: float = 0.65

    # Separations (job disruption) as extra hit to runway (also forces unemployment that month)
    separation_prob_monthly: float = 0.030
    separation_income_hit: float = 0.40

    # Thresholds / exits
    precarious_thresh: float = 1.5
    insolvent_thresh: float = 0.0
    unemp_exit_boost_after: int = 6
    exit_prob_monthly_if_insolvent_unemployed: float = 0.06
    exit_prob_monthly_if_insolvent_only: float = 0.02

    # Performance / viz
    chunk_size: int = 5_000_000
    sample_size: int = 1_000_000
    hist_bins: tuple = (0, 0.5, 1, 2, 3, 6, 12, 24, 36, 48)


def _maybe_memmap(path: str, name: str, shape, dtype, use_memmap: bool):
    os.makedirs(path, exist_ok=True)
    fp = os.path.join(path, f"{name}.dat")
    if use_memmap:
        return np.memmap(fp, dtype=dtype, mode="w+", shape=shape)
    return np.empty(shape, dtype=dtype)


def initialize_population(N: int, p: SimParams, *, use_memmap=False, memmap_dir="sim_mem"):
    rng = np.random.default_rng(p.seed)

    capital = _maybe_memmap(memmap_dir, "capital", (N,), np.float32, use_memmap)
    tiers   = _maybe_memmap(memmap_dir, "tiers",   (N,), np.uint8,  use_memmap)
    employed = _maybe_memmap(memmap_dir, "employed", (N,), np.uint8, use_memmap)
    unemp_months = _maybe_memmap(memmap_dir, "unemp_months", (N,), np.uint8, use_memmap)
    state   = _maybe_memmap(memmap_dir, "state",   (N,), np.uint8,  use_memmap)

    # Capital runway (months)
    cap = rng.lognormal(mean=p.capital_logn_mu, sigma=p.capital_logn_sigma, size=N).astype(np.float32)
    np.minimum(cap, p.capital_cap_max, out=cap)
    capital[:] = cap

    # Tier assignment (store for speed)
    tiers[:] = rng.choice(3, size=N, p=p.tier_probs).astype(np.uint8)

    employed[:] = 0
    unemp_months[:] = 0

    # State based on runway
    state[:] = STABLE
    state[capital < p.precarious_thresh] = PRECARIOUS
    state[capital <= p.insolvent_thresh] = INSOLVENT

    # Fixed visualization sample (stable across time)
    sample_idx = rng.choice(N, size=min(p.sample_size, N), replace=False)

    return {
        "capital": capital,
        "tiers": tiers,
        "employed": employed,
        "unemp_months": unemp_months,
        "state": state,
        "sample_idx": sample_idx,
    }


def simulate(N: int, p: SimParams, *, use_memmap=False, memmap_dir="sim_mem", progress_every=12):
    pop = initialize_population(N, p, use_memmap=use_memmap, memmap_dir=memmap_dir)
    capital = pop["capital"]
    tiers = pop["tiers"]
    employed = pop["employed"]
    unemp_months = pop["unemp_months"]
    state = pop["state"]
    sample_idx = pop["sample_idx"]

    rng = np.random.default_rng(p.seed + 1)

    bins = np.array(p.hist_bins, dtype=np.float32)
    hist = np.zeros((p.months, len(bins) - 1), dtype=np.int64)

    active_ct = np.zeros(p.months, dtype=np.int64)
    exited_ct = np.zeros(p.months, dtype=np.int64)
    unemp_ct  = np.zeros(p.months, dtype=np.int64)
    emp_ct    = np.zeros(p.months, dtype=np.int64)

    mean = np.zeros(p.months, dtype=np.float32)
    p10  = np.zeros(p.months, dtype=np.float32)
    p50  = np.zeros(p.months, dtype=np.float32)
    p90  = np.zeros(p.months, dtype=np.float32)

    chunk = p.chunk_size

    for m in range(p.months):
        # Effective job slot ratio this month (declining capacity)
        slot_ratio = p.job_slot_ratio0 * ((1.0 - p.job_slot_decline) ** m)
        slot_ratio = float(np.clip(slot_ratio * p.hire_friction, 0.0, 1.0))

        for start in range(0, N, chunk):
            end = min(start + chunk, N)
            sl = slice(start, end)
            size = end - start

            cap = capital[sl]
            tr  = tiers[sl]
            emp = employed[sl]
            um  = unemp_months[sl]
            stt = state[sl]

            active = (stt != EXITED)

            # --- Employment assignment (probabilistic capacity) ---
            draw = rng.random(size, dtype=np.float32)
            emp[:] = 0
            emp[active & (draw < slot_ratio)] = 1

            # --- Shocks (expenses) ---
            shock_draw = rng.random(size, dtype=np.float32)
            shocked = active & (shock_draw < p.shock_prob_monthly)
            if shocked.any():
                costs = rng.lognormal(mean=p.shock_cost_mu, sigma=p.shock_cost_sigma, size=int(shocked.sum())).astype(np.float32)
                cap[shocked] -= costs

            # --- Separations (extra runway hit + forces unemployment this month) ---
            sep_draw = rng.random(size, dtype=np.float32)
            separated = active & (sep_draw < p.separation_prob_monthly)
            if separated.any():
                cap[separated] -= np.float32(p.separation_income_hit)
                emp[separated] = 0

            # --- Unemployment duration update ---
            unemp_mask = active & (emp == 0)
            if unemp_mask.any():
                um[unemp_mask] = np.minimum(um[unemp_mask] + 1, 255).astype(np.uint8)
            reemp_mask = active & (emp == 1)
            if reemp_mask.any():
                um[reemp_mask] = 0

            # --- Monthly cashflow: depends on employment + tier ---
            rev = np.empty(size, dtype=np.float32)
            for t in (0, 1, 2):
                mask = active & (tr == t)
                if mask.any():
                    mu = np.where(emp[mask] == 1, p.rev_employed_mu[t], p.rev_unemployed_mu[t]).astype(np.float32)
                    rev[mask] = rng.normal(loc=mu, scale=p.rev_sigma[t], size=int(mask.sum())).astype(np.float32)

            cap[active] += rev[active]

            # --- State transitions based on runway ---
            newly_insolvent = active & (cap <= p.insolvent_thresh)
            newly_precarious = active & (cap < p.precarious_thresh) & ~newly_insolvent
            newly_stable = active & (cap >= p.precarious_thresh)

            stt[newly_insolvent] = INSOLVENT
            stt[newly_precarious] = PRECARIOUS
            stt[newly_stable] = STABLE

            # --- Exit rule: insolvent + prolonged unemployment raises exit risk ---
            insol = (stt == INSOLVENT) & active
            if insol.any():
                deep_unemp = insol & (um >= p.unemp_exit_boost_after) & (emp == 0)
                shallow = insol & ~deep_unemp

                risk = np.zeros(size, dtype=np.float32)
                risk[deep_unemp] = p.exit_prob_monthly_if_insolvent_unemployed
                risk[shallow] = p.exit_prob_monthly_if_insolvent_only

                exit_draw = rng.random(size, dtype=np.float32)
                exiting = insol & (exit_draw < risk)
                stt[exiting] = EXITED
                emp[exiting] = 0
                um[exiting] = np.minimum(um[exiting] + 1, 255).astype(np.uint8)

            # write back
            capital[sl] = cap
            employed[sl] = emp
            unemp_months[sl] = um
            state[sl] = stt

        # --- Aggregates (exact for full arrays; for 100M you may prefer sampled counts) ---
        exited_ct[m] = int(np.count_nonzero(state == EXITED))
        active_ct[m] = int(np.count_nonzero(state != EXITED))
        emp_ct[m]    = int(np.count_nonzero((state != EXITED) & (employed == 1)))
        unemp_ct[m]  = int(np.count_nonzero((state != EXITED) & (employed == 0)))

        samp = capital[sample_idx]
        mean[m] = samp.mean().astype(np.float32)
        p10[m], p50[m], p90[m] = np.percentile(samp, [10, 50, 90]).astype(np.float32)
        hist[m] = np.histogram(samp, bins=bins)[0]

        if progress_every and (m + 1) % progress_every == 0:
            unemp_rate = unemp_ct[m] / max(active_ct[m], 1)
            print(
                f"Month {m+1:4d}/{p.months}: "
                f"active={active_ct[m]:,} exited={exited_ct[m]:,} "
                f"emp_rate={emp_ct[m]/max(active_ct[m],1):.3f} "
                f"unemp_rate={unemp_rate:.3f} "
                f"median_runway={p50[m]:.2f} "
                f"slot_ratio={slot_ratio:.3f}"
            )

    final_slot_ratio = p.job_slot_ratio0 * ((1.0 - p.job_slot_decline) ** (p.months - 1)) * p.hire_friction

    return {
        "params": p,
        "series": {
            "active": active_ct,
            "exited": exited_ct,
            "employed": emp_ct,
            "unemployed": unemp_ct,
            "mean_runway_sample": mean,
            "p10_runway_sample": p10,
            "p50_runway_sample": p50,
            "p90_runway_sample": p90,
            "hist_runway_sample": hist,
            "hist_bins": bins,
            "final_effective_job_slot_ratio": float(final_slot_ratio),
        },
        "pop": pop,  # arrays (possibly memmap on disk)
    }


def save_outputs(out: dict, out_path: str):
    # Save only the series to NPZ (small, visualization-ready)
    s = out["series"]
    np.savez_compressed(
        out_path,
        active=s["active"],
        exited=s["exited"],
        employed=s["employed"],
        unemployed=s["unemployed"],
        mean_runway_sample=s["mean_runway_sample"],
        p10_runway_sample=s["p10_runway_sample"],
        p50_runway_sample=s["p50_runway_sample"],
        p90_runway_sample=s["p90_runway_sample"],
        hist_runway_sample=s["hist_runway_sample"],
        hist_bins=s["hist_bins"],
        final_effective_job_slot_ratio=np.array([s["final_effective_job_slot_ratio"]], dtype=np.float32),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=2_000_000, help="Population size (use 100000000 for 100M)")
    ap.add_argument("--months", type=int, default=24, help="Number of monthly ticks")
    ap.add_argument("--chunk", type=int, default=500_000, help="Chunk size for processing")
    ap.add_argument("--sample", type=int, default=200_000, help="Sample size for percentiles/histograms")
    ap.add_argument("--memmap", action="store_true", help="Use disk-backed arrays")
    ap.add_argument("--memmap-dir", type=str, default="sim_mem", help="Directory for memmap arrays")
    ap.add_argument("--out", type=str, default="sim_series.npz", help="Output NPZ for visualization series")
    ap.add_argument("--progress-every", type=int, default=6, help="Print progress every N months (0 disables)")
    args = ap.parse_args()

    params = SimParams(
        months=args.months,
        chunk_size=args.chunk,
        sample_size=args.sample,
    )

    out = simulate(
        args.N,
        params,
        use_memmap=args.memmap,
        memmap_dir=args.memmap_dir,
        progress_every=args.progress_every,
    )
    save_outputs(out, args.out)

    s = out["series"]
    exited_end = int(s["exited"][-1])
    exit_pct = exited_end / args.N * 100
    unemp_rate_end = float(s["unemployed"][-1] / max(int(s["active"][-1]), 1))

    print("\n=== FINAL ===")
    print(f"N: {args.N:,}  months: {args.months}")
    print(f"Exited: {exited_end:,}  ({exit_pct:.2f}% over horizon)")
    print(f"Unemployment rate (end): {unemp_rate_end:.3f}")
    print(f"Median runway (end, sample): {float(s['p50_runway_sample'][-1]):.2f} months")
    print(f"Final effective job-slot ratio: {float(s['final_effective_job_slot_ratio']):.3f}")
    print(f"Saved series to: {args.out}")


if __name__ == "__main__":
    main()
