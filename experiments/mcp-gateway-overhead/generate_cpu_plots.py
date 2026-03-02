#!/usr/bin/env python3
"""
Mock MCP Server: CPU & Memory Utilization Plot Generator.

Reads cpu_usage.csv and generates per-concurrency bar charts and timelines.

Usage:
    python3 generate_cpu_plots.py <results_dir>
"""

import sys
import os

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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

POD_ROLES = {
    "mcp-gateway-istio":          ("MCP Gateway (Envoy)",         "#FF9800"),
    "mcp-gateway-broker-router":  ("MCP Gateway (Broker-Router)", "#2196F3"),
    "perf-mock-server":           ("Mock MCP Server",             "#4CAF50"),
    "spike-":                     ("Locust (load gen)",            "#9C27B0"),
}

CONCURRENCY_LEVELS = [2, 4, 8, 16, 32, 64, 128, 256, 512]


def classify_pod(pod_name: str):
    for fragment in POD_ROLES:
        if fragment in pod_name:
            return fragment
    if pod_name.startswith("perf-"):
        return "spike-"
    return None


def load_cpu_data(results_dir: str) -> pd.DataFrame:
    csv_path = os.path.join(results_dir, "cpu_usage.csv")
    if not os.path.isfile(csv_path):
        print(f"  WARNING: {csv_path} not found — skipping CPU plots")
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["role"] = df["pod"].apply(classify_pod)
    df = df.dropna(subset=["role"])
    return df


def assign_concurrency_windows(df: pd.DataFrame, results_dir: str) -> pd.DataFrame:
    cps_dirs = sorted([
        d for d in os.listdir(results_dir)
        if os.path.isdir(os.path.join(results_dir, d)) and d.startswith("cps")
    ])
    if not cps_dirs:
        return df

    cps_dir = os.path.join(results_dir, cps_dirs[0])
    windows = []
    for u_dir_name in sorted(os.listdir(cps_dir)):
        if not u_dir_name.startswith("u"):
            continue
        u_dir = os.path.join(cps_dir, u_dir_name)
        if not os.path.isdir(u_dir):
            continue
        users = int(u_dir_name[1:])
        log_files = sorted([
            os.path.join(u_dir, f) for f in os.listdir(u_dir) if f.endswith(".log")
        ])
        if not log_files:
            continue
        mtimes = [os.path.getmtime(f) for f in log_files]
        windows.append((min(mtimes), max(mtimes), users))

    if not windows:
        return df

    windows.sort(key=lambda x: x[0])
    refined = []
    for i, (s, e, u) in enumerate(windows):
        if i < len(windows) - 1:
            refined.append((s, windows[i + 1][0], u))
        else:
            refined.append((s, e + 60, u))

    df["concurrency"] = np.nan
    for start_t, end_t, users in refined:
        start_dt = pd.Timestamp.fromtimestamp(start_t)
        end_dt = pd.Timestamp.fromtimestamp(end_t)
        mask = (df["timestamp"] >= start_dt) & (df["timestamp"] < end_dt)
        df.loc[mask, "concurrency"] = users

    df = df.dropna(subset=["concurrency"])
    df["concurrency"] = df["concurrency"].astype(int)
    return df


def _get_concurrency_windows(results_dir: str):
    cps_dirs = sorted([
        d for d in os.listdir(results_dir)
        if os.path.isdir(os.path.join(results_dir, d)) and d.startswith("cps")
    ])
    if not cps_dirs:
        return []
    cps_dir = os.path.join(results_dir, cps_dirs[0])
    windows = []
    for u_dir_name in sorted(os.listdir(cps_dir)):
        if not u_dir_name.startswith("u"):
            continue
        u_dir = os.path.join(cps_dir, u_dir_name)
        if not os.path.isdir(u_dir):
            continue
        users = int(u_dir_name[1:])
        log_files = sorted([
            os.path.join(u_dir, f) for f in os.listdir(u_dir) if f.endswith(".log")
        ])
        if not log_files:
            continue
        mtimes = [os.path.getmtime(f) for f in log_files]
        windows.append((min(mtimes), max(mtimes), users))
    windows.sort(key=lambda x: x[0])
    refined = []
    for i, (s, e, u) in enumerate(windows):
        if i < len(windows) - 1:
            refined.append((s, windows[i + 1][0], u))
        else:
            refined.append((s, e + 60, u))
    return refined


def plot_cpu_per_concurrency(df, out_dir, cps_label):
    if df.empty:
        return
    roles_present = [r for r in POD_ROLES if r in df["role"].values]
    if not roles_present:
        return

    concurrency_vals = sorted(df["concurrency"].unique())
    x = np.arange(len(concurrency_vals))
    n_roles = len(roles_present)
    bar_width = 0.8 / n_roles

    fig, ax = plt.subplots(figsize=(max(12, len(concurrency_vals) * 1.5), 6))

    for i, role in enumerate(roles_present):
        label, color = POD_ROLES[role]
        avgs = []
        peaks = []
        for c in concurrency_vals:
            subset = df[(df["concurrency"] == c) & (df["role"] == role)]
            if subset.empty:
                avgs.append(0)
                peaks.append(0)
            else:
                avgs.append(subset["cpu_millicores"].mean())
                peaks.append(subset["cpu_millicores"].max())

        offset = (i - n_roles / 2 + 0.5) * bar_width
        bars = ax.bar(x + offset, avgs, bar_width,
                      label=f"{label} (avg)", color=color, alpha=0.85,
                      edgecolor="white", linewidth=0.5)
        ax.scatter(x + offset, peaks, s=30, color=color, edgecolors="black",
                   linewidths=0.5, zorder=5, marker="^", label=f"{label} (peak)")

        for bar, avg in zip(bars, avgs):
            if avg > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, avg + 10,
                        f"{avg:.0f}", ha="center", va="bottom", fontsize=7)

    ax.set_xlabel("Concurrent Users")
    ax.set_ylabel("CPU (millicores)")
    ax.set_xticks(x)
    ax.set_xticklabels([str(c) for c in concurrency_vals])
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.set_title(f"{cps_label}: CPU Utilization per Concurrency Level")

    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "cpu_utilization_per_concurrency.png"), bbox_inches="tight")
    plt.close()
    print("  saved: cpu_utilization_per_concurrency.png")


def plot_cpu_timeline(df, out_dir, cps_label, windows):
    if df.empty:
        return
    roles_present = [r for r in POD_ROLES if r in df["role"].values]
    if not roles_present:
        return

    fig, ax = plt.subplots(figsize=(20, 6))
    for role in roles_present:
        label, color = POD_ROLES[role]
        subset = df[df["role"] == role].sort_values("timestamp")
        ax.plot(subset["timestamp"], subset["cpu_millicores"],
                color=color, linewidth=1.5, label=label, alpha=0.9)

    ax.set_ylim(bottom=0)
    ymax = df["cpu_millicores"].max() * 1.15
    ax.set_ylim(0, ymax)

    if windows:
        band_colors = ["#E3F2FD", "#FFF3E0"]
        for i, (start_t, end_t, users) in enumerate(windows):
            start_dt = pd.Timestamp.fromtimestamp(start_t)
            end_dt = pd.Timestamp.fromtimestamp(end_t)
            ax.axvspan(start_dt, end_dt, alpha=0.25,
                       color=band_colors[i % 2], zorder=0)
            mid = start_dt + (end_dt - start_dt) / 2
            ax.text(mid, ymax * 0.96, f"u{users}",
                    ha="center", va="top", fontsize=7, fontweight="bold",
                    color="#333333", zorder=10)

    ax.set_xlabel("Time")
    ax.set_ylabel("CPU (millicores)")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title(f"{cps_label}: CPU Utilization Over Time")
    fig.autofmt_xdate(rotation=30)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "cpu_utilization_timeline.png"), bbox_inches="tight")
    plt.close()
    print("  saved: cpu_utilization_timeline.png")


def plot_memory_timeline(df, out_dir, cps_label, windows):
    if df.empty:
        return
    roles_present = [r for r in POD_ROLES if r in df["role"].values]
    if not roles_present:
        return

    fig, ax = plt.subplots(figsize=(20, 6))
    for role in roles_present:
        label, color = POD_ROLES[role]
        subset = df[df["role"] == role].sort_values("timestamp")
        ax.plot(subset["timestamp"], subset["memory_MiB"],
                color=color, linewidth=1.5, label=label, alpha=0.9)

    ax.set_ylim(bottom=0)
    ymax = df["memory_MiB"].max() * 1.15
    ax.set_ylim(0, ymax)

    if windows:
        band_colors = ["#E3F2FD", "#FFF3E0"]
        for i, (start_t, end_t, users) in enumerate(windows):
            start_dt = pd.Timestamp.fromtimestamp(start_t)
            end_dt = pd.Timestamp.fromtimestamp(end_t)
            ax.axvspan(start_dt, end_dt, alpha=0.25,
                       color=band_colors[i % 2], zorder=0)
            mid = start_dt + (end_dt - start_dt) / 2
            ax.text(mid, ymax * 0.96, f"u{users}",
                    ha="center", va="top", fontsize=7, fontweight="bold",
                    color="#333333", zorder=10)

    ax.set_xlabel("Time")
    ax.set_ylabel("Memory (MiB)")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title(f"{cps_label}: Memory Utilization Over Time")
    fig.autofmt_xdate(rotation=30)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "memory_utilization_timeline.png"), bbox_inches="tight")
    plt.close()
    print("  saved: memory_utilization_timeline.png")


def plot_throttle_timeline(throttle_csv, out_dir, cps_label, windows):
    try:
        tdf = pd.read_csv(throttle_csv, parse_dates=["timestamp"])
    except Exception:
        return
    if tdf.empty:
        return

    tdf["role"] = tdf["pod"].apply(classify_pod)
    tdf = tdf.dropna(subset=["role"])
    if tdf.empty:
        return

    roles_present = [r for r in POD_ROLES if r in tdf["role"].values]
    if not roles_present:
        return

    fig, ax = plt.subplots(figsize=(20, 6))
    for role in roles_present:
        label, color = POD_ROLES[role]
        subset = tdf[tdf["role"] == role].sort_values("timestamp")
        ax.plot(subset["timestamp"], subset["throttled_pct"],
                color=color, linewidth=1.5, label=label, alpha=0.9)

    ax.set_ylim(bottom=0)
    ymax = max(tdf["throttled_pct"].max() * 1.15, 10)
    ax.set_ylim(0, min(ymax, 105))

    if windows:
        band_colors = ["#E3F2FD", "#FFF3E0"]
        for i, (start_t, end_t, users) in enumerate(windows):
            start_dt = pd.Timestamp.fromtimestamp(start_t)
            end_dt = pd.Timestamp.fromtimestamp(end_t)
            ax.axvspan(start_dt, end_dt, alpha=0.15,
                       color=band_colors[i % len(band_colors)], zorder=0)
            ax.text(start_dt, ax.get_ylim()[1] * 0.98, f"u{users}",
                    fontsize=8, fontweight="bold", va="top", ha="left",
                    color="#333333")

    ax.set_xlabel("Time")
    ax.set_ylabel("CPU Throttle (%)")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title(f"{cps_label}: CPU Throttling Over Time")
    fig.autofmt_xdate(rotation=30)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "cpu_throttle_timeline.png"), bbox_inches="tight")
    plt.close()
    print("  saved: cpu_throttle_timeline.png")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_cpu_plots.py <results_dir>")
        sys.exit(1)

    results_dir = sys.argv[1]
    if not os.path.isdir(results_dir):
        print(f"ERROR: {results_dir} is not a directory")
        sys.exit(1)

    df = load_cpu_data(results_dir)
    if df.empty:
        print("No CPU data found.")
        return

    print(f"Loaded {len(df)} CPU samples across {df['role'].nunique()} pod roles")

    df_full = df.copy()
    windows = _get_concurrency_windows(results_dir)
    df = assign_concurrency_windows(df, results_dir)

    if not df.empty and "concurrency" in df.columns:
        concurrency_vals = sorted(df["concurrency"].unique())
        print(f"Concurrency levels found: {concurrency_vals}")

    cps_dirs = [d for d in os.listdir(results_dir)
                if os.path.isdir(os.path.join(results_dir, d)) and d.startswith("cps")]
    CPS_LABELS = {
        "cps0": "Persistent Session (CPS=0)",
        "cps1": "One Shot Session (CPS=1)",
        "cps10": "Batch 10 Session (CPS=10)",
    }
    cps_label = CPS_LABELS.get(cps_dirs[0], cps_dirs[0]) if cps_dirs else "Mock MCP"

    plots_dir = os.path.join(results_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    print(f"\nGenerating CPU/Memory plots → {plots_dir}")

    if not df.empty and "concurrency" in df.columns:
        plot_cpu_per_concurrency(df, plots_dir, cps_label)

    plot_cpu_timeline(df_full, plots_dir, cps_label, windows)
    plot_memory_timeline(df_full, plots_dir, cps_label, windows)

    throttle_csv = os.path.join(results_dir, "cpu_usage_throttle.csv")
    if os.path.isfile(throttle_csv):
        plot_throttle_timeline(throttle_csv, plots_dir, cps_label, windows)

    print("Done.")


if __name__ == "__main__":
    main()
