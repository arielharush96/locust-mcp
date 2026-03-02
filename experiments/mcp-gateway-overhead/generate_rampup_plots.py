#!/usr/bin/env python3
"""
Ramp-Up Test: Plot Generator.

Reads stats_history.csv from server and gateway ramp-up runs and generates
plots showing latency, throughput, and failure rate vs concurrent user count.

Usage:
    python3 generate_rampup_plots.py <results_dir>
"""

import sys
import os

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

# ── style ─────────────────────────────────────────────────────────────────────

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 11,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "axes.labelsize": 12,
    "legend.fontsize": 10,
    "figure.dpi": 150,
    "savefig.dpi": 150,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

SERVER_COLOR = "#4CAF50"
GATEWAY_COLOR = "#2196F3"
USER_COLOR = "#FF9800"
FAIL_COLOR = "#F44336"

LABELS = {"server": "Direct Server", "gateway": "Via Gateway"}


# ── data loading ──────────────────────────────────────────────────────────────

def load_history(csv_path: str) -> pd.DataFrame:
    """Load stats_history.csv and compute per-second metrics."""
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return pd.DataFrame()

    try:
        df = pd.read_csv(csv_path)
        df.columns = [c.strip() for c in df.columns]
    except Exception as e:
        print(f"  warning: could not load {csv_path}: {e}")
        return pd.DataFrame()

    # work with Aggregated rows for overall metrics
    agg = df[df["Name"] == "Aggregated"].copy()
    if agg.empty:
        named = df[df["Name"] != "Aggregated"].copy()
        if named.empty:
            return pd.DataFrame()

        for col in ["Total Request Count", "Total Failure Count",
                     "Total Average Response Time", "User Count"]:
            named[col] = pd.to_numeric(named[col], errors="coerce").fillna(0)

        named["total_time"] = named["Total Average Response Time"] * named["Total Request Count"]
        agg = named.groupby("Timestamp").agg(
            Total_Request_Count=("Total Request Count", "sum"),
            Total_Failure_Count=("Total Failure Count", "sum"),
            Total_Time=("total_time", "sum"),
            User_Count=("User Count", "max"),
        ).reset_index()
        agg["Total Average Response Time"] = np.where(
            agg["Total_Request_Count"] > 0,
            agg["Total_Time"] / agg["Total_Request_Count"],
            0,
        )
        agg["Total Request Count"] = agg["Total_Request_Count"]
        agg["Total Failure Count"] = agg["Total_Failure_Count"]
    else:
        for col in ["Total Request Count", "Total Failure Count",
                     "Total Average Response Time", "User Count"]:
            agg[col] = pd.to_numeric(agg[col], errors="coerce").fillna(0)

    agg = agg.sort_values("Timestamp").reset_index(drop=True)

    # compute deltas (per-second metrics)
    agg["delta_reqs"] = agg["Total Request Count"].diff().fillna(0)
    agg["delta_fails"] = agg["Total Failure Count"].diff().fillna(0)
    agg["delta_success"] = (agg["delta_reqs"] - agg["delta_fails"]).clip(lower=0)

    agg["cum_total_time"] = agg["Total Average Response Time"] * agg["Total Request Count"]
    agg["delta_total_time"] = agg["cum_total_time"].diff().fillna(0)

    agg["avg_latency"] = np.where(
        agg["delta_success"] > 0,
        agg["delta_total_time"] / agg["delta_success"],
        np.nan,
    )
    agg["rps"] = agg["delta_success"]
    agg["fail_rate"] = np.where(
        agg["delta_reqs"] > 0,
        agg["delta_fails"] / agg["delta_reqs"] * 100,
        0,
    )

    # cumulative failure rate
    agg["cum_fails"] = agg["Total Failure Count"]
    agg["cum_reqs"] = agg["Total Request Count"]
    agg["cum_fail_rate"] = np.where(
        agg["cum_reqs"] > 0,
        agg["cum_fails"] / agg["cum_reqs"] * 100,
        0,
    )

    # elapsed seconds from start
    t0 = agg["Timestamp"].iloc[0]
    agg["elapsed_s"] = agg["Timestamp"] - t0

    agg = agg[agg["User Count"] > 0]
    return agg


# ── plot functions ────────────────────────────────────────────────────────────

def plot_rampup_latency(server_hist, gateway_hist, out_dir):
    """Avg latency vs concurrent user count."""
    fig, ax = plt.subplots(figsize=(14, 6))

    if not server_hist.empty:
        ax.plot(server_hist["User Count"], server_hist["avg_latency"],
                color=SERVER_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["server"])
    if not gateway_hist.empty:
        ax.plot(gateway_hist["User Count"], gateway_hist["avg_latency"],
                color=GATEWAY_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["gateway"])

    ax.set_xlabel("Concurrent Users")
    ax.set_ylabel("Avg Latency (ms)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    ax.set_title("Ramp-Up: Avg Latency vs Concurrent Users")
    fig.tight_layout()
    plt.savefig(os.path.join(out_dir, "rampup_latency.png"), bbox_inches="tight")
    plt.close()
    print("  saved: rampup_latency.png")


def plot_rampup_throughput(server_hist, gateway_hist, out_dir):
    """Throughput (RPS) vs concurrent user count."""
    fig, ax = plt.subplots(figsize=(14, 6))

    if not server_hist.empty:
        ax.plot(server_hist["User Count"], server_hist["rps"],
                color=SERVER_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["server"])
    if not gateway_hist.empty:
        ax.plot(gateway_hist["User Count"], gateway_hist["rps"],
                color=GATEWAY_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["gateway"])

    ax.set_xlabel("Concurrent Users")
    ax.set_ylabel("Throughput (RPS)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    ax.set_title("Ramp-Up: Throughput vs Concurrent Users")
    fig.tight_layout()
    plt.savefig(os.path.join(out_dir, "rampup_throughput.png"), bbox_inches="tight")
    plt.close()
    print("  saved: rampup_throughput.png")


def plot_rampup_failure_rate(server_hist, gateway_hist, out_dir):
    """Cumulative failure rate (%) vs concurrent user count."""
    fig, ax = plt.subplots(figsize=(14, 6))

    if not server_hist.empty:
        ax.plot(server_hist["User Count"], server_hist["cum_fail_rate"],
                color=SERVER_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["server"])
    if not gateway_hist.empty:
        ax.plot(gateway_hist["User Count"], gateway_hist["cum_fail_rate"],
                color=GATEWAY_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["gateway"])

    ax.set_xlabel("Concurrent Users")
    ax.set_ylabel("Cumulative Failure Rate (%)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    ax.set_title("Ramp-Up: Cumulative Failure Rate vs Concurrent Users")
    fig.tight_layout()
    plt.savefig(os.path.join(out_dir, "rampup_failure_rate.png"), bbox_inches="tight")
    plt.close()
    print("  saved: rampup_failure_rate.png")


def plot_rampup_combined(server_hist, gateway_hist, out_dir):
    """4-panel combined: latency, throughput, failure rate, instantaneous failure rate."""
    fig, axes = plt.subplots(2, 2, figsize=(18, 12))

    ref = gateway_hist if not gateway_hist.empty else server_hist
    max_users = int(ref["User Count"].max()) if not ref.empty else 0

    # panel 1: latency
    ax = axes[0, 0]
    if not server_hist.empty:
        ax.plot(server_hist["User Count"], server_hist["avg_latency"],
                color=SERVER_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["server"])
    if not gateway_hist.empty:
        ax.plot(gateway_hist["User Count"], gateway_hist["avg_latency"],
                color=GATEWAY_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["gateway"])
    ax.set_ylabel("Avg Latency (ms)")
    ax.set_ylim(bottom=0)
    ax.set_title("Avg Latency")
    ax.legend(fontsize=9)

    # panel 2: throughput
    ax = axes[0, 1]
    if not server_hist.empty:
        ax.plot(server_hist["User Count"], server_hist["rps"],
                color=SERVER_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["server"])
    if not gateway_hist.empty:
        ax.plot(gateway_hist["User Count"], gateway_hist["rps"],
                color=GATEWAY_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["gateway"])
    ax.set_ylabel("Throughput (RPS)")
    ax.set_ylim(bottom=0)
    ax.set_title("Throughput")
    ax.legend(fontsize=9)

    # panel 3: cumulative failure rate
    ax = axes[1, 0]
    if not server_hist.empty:
        ax.plot(server_hist["User Count"], server_hist["cum_fail_rate"],
                color=SERVER_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["server"])
    if not gateway_hist.empty:
        ax.plot(gateway_hist["User Count"], gateway_hist["cum_fail_rate"],
                color=GATEWAY_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["gateway"])
    ax.set_ylabel("Cumulative Failure Rate (%)")
    ax.set_ylim(bottom=0)
    ax.set_title("Cumulative Failure Rate")
    ax.legend(fontsize=9)
    ax.set_xlabel("Concurrent Users")

    # panel 4: instantaneous failure rate
    ax = axes[1, 1]
    if not server_hist.empty:
        ax.plot(server_hist["User Count"], server_hist["fail_rate"],
                color=SERVER_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["server"])
    if not gateway_hist.empty:
        ax.plot(gateway_hist["User Count"], gateway_hist["fail_rate"],
                color=GATEWAY_COLOR, linewidth=1.5, alpha=0.8,
                label=LABELS["gateway"])
    ax.set_ylabel("Instantaneous Failure Rate (%)")
    ax.set_ylim(bottom=0)
    ax.set_title("Instantaneous Failure Rate")
    ax.legend(fontsize=9)
    ax.set_xlabel("Concurrent Users")

    fig.suptitle(f"Ramp-Up Under Load: 0 → {max_users} Users",
                 fontsize=16, fontweight="bold", y=1.02)
    fig.tight_layout()
    plt.savefig(os.path.join(out_dir, "rampup_combined.png"), bbox_inches="tight")
    plt.close()
    print("  saved: rampup_combined.png")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results_dir>")
        sys.exit(1)

    results_dir = sys.argv[1]
    plots_dir = os.path.join(results_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)

    print(f"generating ramp-up plots from: {results_dir}")

    server_hist = load_history(os.path.join(results_dir, "server_stats_history.csv"))
    gateway_hist = load_history(os.path.join(results_dir, "gateway_stats_history.csv"))

    if server_hist.empty and gateway_hist.empty:
        print("ERROR: no history data found")
        sys.exit(1)

    if not server_hist.empty:
        print(f"  server:  {len(server_hist)} data points, "
              f"max users={server_hist['User Count'].max():.0f}")
    if not gateway_hist.empty:
        print(f"  gateway: {len(gateway_hist)} data points, "
              f"max users={gateway_hist['User Count'].max():.0f}")

    plot_rampup_latency(server_hist, gateway_hist, plots_dir)
    plot_rampup_throughput(server_hist, gateway_hist, plots_dir)
    plot_rampup_failure_rate(server_hist, gateway_hist, plots_dir)
    plot_rampup_combined(server_hist, gateway_hist, plots_dir)

    print(f"\nall ramp-up plots saved to: {plots_dir}")


if __name__ == "__main__":
    main()
