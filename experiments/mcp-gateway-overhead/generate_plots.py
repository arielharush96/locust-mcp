#!/usr/bin/env python3
"""
Mock MCP Server Performance Test: Plot Generator.

Generates bar charts comparing Direct Server vs Via Gateway across different
concurrency levels (2, 4, 8, 16, 32, 64, 128, 256, 512 users).

Categories:
    initialize  - MCP session initialization
    tools_list  - tools/list call
    tool_call   - all 10 mock tool calls (alpha..juliet, all zero-latency)

Usage:
    python3 generate_plots.py <results_dir>
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

SERVER_COLOR = "#4CAF50"
SERVER_DOT = "#2E7D32"
GATEWAY_COLOR = "#2196F3"
GATEWAY_DOT = "#1565C0"

LABELS = {"server": "Direct Server", "gateway": "Via Gateway"}

CPS_LABELS = {
    "cps0": "Persistent Session (CPS=0)",
    "cps1": "One Shot Session (CPS=1)",
    "cps10": "Batch 10 Session (CPS=10)",
}

# request name -> category
REQUEST_CATEGORIES = {
    "initialize": "initialize",
    "tools/list": "tools_list",
    "call:alpha": "tool_call",
    "call:bravo": "tool_call",
    "call:charlie": "tool_call",
    "call:delta": "tool_call",
    "call:echo": "tool_call",
    "call:foxtrot": "tool_call",
    "call:golf": "tool_call",
    "call:hotel": "tool_call",
    "call:india": "tool_call",
    "call:juliet": "tool_call",
}

ALL_CATEGORIES = ["initialize", "tools_list", "tool_call"]

CATEGORY_LABELS = {
    "initialize": "Initialize",
    "tools_list": "Tools List",
    "tool_call": "Tool Calls (all)",
}

CONCURRENCY_LEVELS = [2, 4, 8, 16, 32, 64, 128, 256, 512]

# sentinel for survivorship bias correction (no client timeout, but keep for safety)
TIMEOUT_MS = 30000


# ── data loading ──────────────────────────────────────────────────────────────

def load_stats(csv_path: str) -> pd.DataFrame:
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(csv_path)
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception as e:
        print(f"  warning: could not load {csv_path}: {e}")
        return pd.DataFrame()


def load_history(csv_path: str) -> pd.DataFrame:
    """Load stats history CSV and compute success-only time-series metrics."""
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(csv_path)
        df.columns = [c.strip() for c in df.columns]

        has_fail_prefix = df["Name"].str.startswith("FAIL:").any()

        if has_fail_prefix:
            success_rows = df[
                (df["Name"] != "Aggregated") & (~df["Name"].str.startswith("FAIL:"))
            ].copy()
            fail_rows = df[df["Name"].str.startswith("FAIL:")].copy()

            if success_rows.empty:
                return pd.DataFrame()

            num_cols = [
                "Total Request Count", "Total Failure Count",
                "Total Average Response Time", "User Count",
            ]
            for col in num_cols:
                success_rows[col] = pd.to_numeric(success_rows[col], errors="coerce").fillna(0)
                if col in fail_rows.columns:
                    fail_rows[col] = pd.to_numeric(fail_rows[col], errors="coerce").fillna(0)

            success_rows["total_time"] = (
                success_rows["Total Average Response Time"] * success_rows["Total Request Count"]
            )
            agg = success_rows.groupby("Timestamp").agg(
                Total_Request_Count=("Total Request Count", "sum"),
                Total_Time=("total_time", "sum"),
                User_Count=("User Count", "max"),
            ).reset_index()

            if not fail_rows.empty:
                fail_agg = fail_rows.groupby("Timestamp").agg(
                    Total_Fail_Count=("Total Request Count", "sum"),
                ).reset_index()
                agg = agg.merge(fail_agg, on="Timestamp", how="left")
                agg["Total_Fail_Count"] = agg["Total_Fail_Count"].fillna(0)
            else:
                agg["Total_Fail_Count"] = 0

            agg = agg.sort_values("Timestamp").reset_index(drop=True)
            agg["delta_reqs"] = agg["Total_Request_Count"].diff().fillna(0)
            agg["delta_total_time"] = agg["Total_Time"].diff().fillna(0)
            agg["delta_fails"] = agg["Total_Fail_Count"].diff().fillna(0)
            agg["delta_success"] = agg["delta_reqs"].clip(lower=0)

            agg["success_avg_rt"] = np.where(
                agg["delta_success"] > 0,
                agg["delta_total_time"] / agg["delta_success"],
                np.nan,
            )
            agg["success_rps"] = agg["delta_success"]

            total_delta = agg["delta_reqs"] + agg["delta_fails"]
            agg["fail_rate_pct"] = np.where(
                total_delta > 0,
                agg["delta_fails"] / total_delta * 100,
                0,
            )
            agg["User Count"] = agg["User_Count"]
            agg = agg[agg["User Count"] > 0]
            return agg

        # legacy format
        agg = df[df["Name"] == "Aggregated"].copy()
        if agg.empty:
            return pd.DataFrame()

        agg = agg.sort_values("Timestamp").reset_index(drop=True)
        num_cols = [
            "Total Request Count", "Total Failure Count",
            "Total Average Response Time", "User Count",
        ]
        for col in num_cols:
            agg[col] = pd.to_numeric(agg[col], errors="coerce").fillna(0)

        agg["delta_reqs"] = agg["Total Request Count"].diff().fillna(0)
        agg["delta_fails"] = agg["Total Failure Count"].diff().fillna(0)
        agg["delta_success"] = (agg["delta_reqs"] - agg["delta_fails"]).clip(lower=0)

        agg["cum_total_time"] = agg["Total Average Response Time"] * agg["Total Request Count"]
        agg["delta_total_time"] = agg["cum_total_time"].diff().fillna(0)

        agg["success_avg_rt"] = np.where(
            agg["delta_success"] > 0,
            agg["delta_total_time"] / agg["delta_success"],
            np.nan,
        )
        agg["success_rps"] = agg["delta_success"]
        agg["fail_rate_pct"] = np.where(
            agg["delta_reqs"] > 0,
            agg["delta_fails"] / agg["delta_reqs"] * 100,
            0,
        )
        agg = agg[agg["User Count"] > 0]
        return agg

    except Exception as e:
        print(f"  warning: could not load {csv_path}: {e}")
        return pd.DataFrame()


def agg_time_series_stats(hist: pd.DataFrame):
    if hist.empty:
        return {"lat_mean": 0, "lat_std": 0, "rps_mean": 0, "rps_std": 0,
                "fr_mean": 0, "fr_std": 0}

    lat = hist["success_avg_rt"].dropna()
    rps = hist["success_rps"]
    fr = hist["fail_rate_pct"]

    return {
        "lat_mean": lat.mean() if len(lat) else 0,
        "lat_std": lat.std() if len(lat) > 1 else 0,
        "rps_mean": rps.mean(),
        "rps_std": rps.std() if len(rps) > 1 else 0,
        "fr_mean": fr.mean(),
        "fr_std": fr.std() if len(fr) > 1 else 0,
    }


def category_stats_from_stats(stats: pd.DataFrame, test_duration: float = 300.0):
    """Extract per-category metrics from final stats CSV."""
    result = {}
    for cat in ALL_CATEGORIES:
        result[cat] = {"avg_lat": 0, "lats": [], "avg_rps": 0, "rpss": [],
                       "avg_fr": 0, "frs": [], "p95_lat": 0, "p99_lat": 0}

    if stats.empty:
        return result

    named = stats[stats["Name"] != "Aggregated"].copy()
    if named.empty:
        return result

    for col in ["Request Count", "Failure Count", "Average Response Time",
                "95%", "99%", "Max Response Time"]:
        named[col] = pd.to_numeric(named[col], errors="coerce").fillna(0)

    fail_rows = named[named["Name"].str.startswith("FAIL:")].copy()
    success_rows = named[~named["Name"].str.startswith("FAIL:")].copy()

    success_rows["category"] = success_rows["Name"].map(REQUEST_CATEGORIES)
    success_rows = success_rows[success_rows["category"].notna()]

    fail_rows["base_name"] = fail_rows["Name"].str.replace("FAIL:", "", n=1)
    fail_rows["category"] = fail_rows["base_name"].map(REQUEST_CATEGORIES)
    fail_rows = fail_rows[fail_rows["category"].notna()]

    success_rows["success_lat"] = success_rows["Average Response Time"]
    success_rows["p95_lat"] = success_rows["95%"]
    success_rows["p99_lat"] = success_rows["99%"]
    success_rows["success_rps"] = np.where(
        test_duration > 0, success_rows["Request Count"] / test_duration, 0)

    for cat in ALL_CATEGORIES:
        s_group = success_rows[success_rows["category"] == cat]
        f_group = fail_rows[fail_rows["category"] == cat]

        total_success = s_group["Request Count"].sum() if not s_group.empty else 0
        total_fail = f_group["Request Count"].sum() if not f_group.empty else 0
        total_all = total_success + total_fail

        lats = s_group["success_lat"].values.tolist() if not s_group.empty else []
        rpss = s_group["success_rps"].values.tolist() if not s_group.empty else []
        fail_pct = (total_fail / total_all * 100) if total_all > 0 else 0

        if total_success == 0:
            p95_weighted = 0
            p99_weighted = 0
        elif not s_group.empty:
            weights = s_group["Request Count"].values
            p95_raw = float(np.average(s_group["p95_lat"].values, weights=weights))
            p99_raw = float(np.average(s_group["p99_lat"].values, weights=weights))
            p95_weighted = TIMEOUT_MS if fail_pct >= 5.0 else p95_raw
            p99_weighted = TIMEOUT_MS if fail_pct >= 1.0 else p99_raw
        else:
            p95_weighted = 0
            p99_weighted = 0

        if total_success == 0:
            corrected_avg = 0
        elif total_fail > 0 and lats:
            raw_avg = float(np.average(
                s_group["Average Response Time"].values,
                weights=s_group["Request Count"].values,
            ))
            corrected_avg = (total_success * raw_avg + total_fail * TIMEOUT_MS) / total_all
        else:
            corrected_avg = np.mean(lats) if lats else 0

        result[cat] = {
            "avg_lat": corrected_avg,
            "lats": lats,
            "p95_lat": p95_weighted,
            "p99_lat": p99_weighted,
            "avg_rps": np.sum(rpss) if rpss else 0,
            "rpss": rpss,
            "avg_fr": fail_pct,
            "frs": [fail_pct],
            "fail_count": int(total_fail),
            "success_count": int(total_success),
        }

    return result


def extract_aggregated_percentiles(stats: pd.DataFrame):
    if stats.empty:
        return {"p95": 0, "p99": 0, "median": 0, "rps": 0}

    named = stats[stats["Name"] != "Aggregated"].copy()
    if named.empty:
        return {"p95": 0, "p99": 0, "median": 0, "rps": 0}

    fail_rows = named[named["Name"].str.startswith("FAIL:")].copy()
    success = named[~named["Name"].str.startswith("FAIL:")].copy()
    if success.empty:
        return {"p95": 0, "p99": 0, "median": 0, "rps": 0}

    for col in ["Request Count", "95%", "99%", "50%", "Requests/s"]:
        success[col] = pd.to_numeric(success[col], errors="coerce").fillna(0)
    for col in ["Request Count"]:
        if col in fail_rows.columns:
            fail_rows[col] = pd.to_numeric(fail_rows[col], errors="coerce").fillna(0)

    total_success = success["Request Count"].sum()
    total_fail = fail_rows["Request Count"].sum() if not fail_rows.empty else 0
    total_all = total_success + total_fail
    fail_pct = (total_fail / total_all * 100) if total_all > 0 else 0

    if total_success > 0:
        weights = success["Request Count"].values
        p95_raw = float(np.average(success["95%"].values, weights=weights))
        p99_raw = float(np.average(success["99%"].values, weights=weights))
        median = float(np.average(success["50%"].values, weights=weights))
    else:
        p95_raw = p99_raw = median = 0

    if total_success == 0:
        p95 = 0
        p99 = 0
    else:
        p95 = TIMEOUT_MS if fail_pct >= 5.0 else p95_raw
        p99 = TIMEOUT_MS if fail_pct >= 1.0 else p99_raw

    rps = float(success["Requests/s"].sum())

    return {"p95": p95, "p99": p99, "median": median, "rps": rps,
            "fail_count": int(total_fail), "success_count": int(total_success)}


# ── plot helpers ──────────────────────────────────────────────────────────────

def _annotate_failures(ax, x_positions, server_vals, gateway_vals,
                       server_fail_pcts, gateway_fail_pcts):
    """Add failure rate table strip below the x-axis labels."""
    from matplotlib.transforms import blended_transform_factory

    trans = blended_transform_factory(ax.transData, ax.transAxes)

    ax.text(x_positions[0] - 0.75, -0.14, "Server Fail%:",
            transform=trans, fontsize=7, fontweight="bold",
            color=SERVER_DOT, ha="right", va="center")
    ax.text(x_positions[0] - 0.75, -0.22, "Gateway Fail%:",
            transform=trans, fontsize=7, fontweight="bold",
            color=GATEWAY_DOT, ha="right", va="center")

    for i, x in enumerate(x_positions):
        sf = server_fail_pcts[i] if i < len(server_fail_pcts) else 0.0
        gf = gateway_fail_pcts[i] if i < len(gateway_fail_pcts) else 0.0

        s_text = f"{sf:.1f}" if sf > 0.05 else "0"
        s_color = "#D32F2F" if sf > 0.05 else "#999999"
        s_weight = "bold" if sf > 0.05 else "normal"
        ax.text(x, -0.14, s_text, transform=trans, fontsize=7.5,
                color=s_color, fontweight=s_weight, ha="center", va="center")

        g_text = f"{gf:.1f}" if gf > 0.05 else "0"
        g_color = "#D32F2F" if gf > 0.05 else "#999999"
        g_weight = "bold" if gf > 0.05 else "normal"
        ax.text(x, -0.22, g_text, transform=trans, fontsize=7.5,
                color=g_color, fontweight=g_weight, ha="center", va="center")


def _bar_scatter_plot(ax, x_positions, server_vals, gateway_vals,
                      server_errs=None, gateway_errs=None,
                      server_scatter=None, gateway_scatter=None,
                      ylabel="", title=""):
    w = 0.3

    bars_s = ax.bar(x_positions - w / 2, server_vals, w,
           label=LABELS["server"], color=SERVER_COLOR,
           edgecolor="white", linewidth=0.5, alpha=0.85,
           yerr=server_errs, capsize=4, error_kw={"linewidth": 1.2})

    bars_g = ax.bar(x_positions + w / 2, gateway_vals, w,
           label=LABELS["gateway"], color=GATEWAY_COLOR,
           edgecolor="white", linewidth=0.5, alpha=0.85,
           yerr=gateway_errs, capsize=4, error_kw={"linewidth": 1.2})

    # annotate values above each bar
    def _fmt(v):
        if v == 0:
            return ""
        if v >= 100:
            return f"{v:.0f}"
        if v >= 10:
            return f"{v:.1f}"
        return f"{v:.1f}"

    for bar in bars_s:
        h = bar.get_height()
        if h > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, h,
                    _fmt(h), ha="center", va="bottom",
                    fontsize=7, fontweight="bold", color=SERVER_DOT)

    for bar in bars_g:
        h = bar.get_height()
        if h > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, h,
                    _fmt(h), ha="center", va="bottom",
                    fontsize=7, fontweight="bold", color=GATEWAY_DOT)

    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=9)


# ── plot functions ────────────────────────────────────────────────────────────

def plot_aggregated(cps, data, out_dir):
    """Five panel plot: avg/p95/p99 latency, throughput, fail rate."""
    users_list = sorted(data.keys())
    if not users_list:
        return

    x_labels = [str(u) for u in users_list]
    x_pos = np.arange(len(users_list))

    s_lat_mean, g_lat_mean = [], []
    s_p95, g_p95 = [], []
    s_p99, g_p99 = [], []
    s_rps_mean, g_rps_mean = [], []
    s_fr_mean, g_fr_mean = [], []

    for u in users_list:
        d = data[u]
        for prefix, lm, p95, p99, rm, fm in [
            ("server", s_lat_mean, s_p95, s_p99, s_rps_mean, s_fr_mean),
            ("gateway", g_lat_mean, g_p95, g_p99, g_rps_mean, g_fr_mean),
        ]:
            ts = d.get(f"{prefix}_ts", {})
            pct = d.get(f"{prefix}_agg_pct", {})
            lm.append(ts.get("lat_mean", 0))
            p95.append(pct.get("p95", 0))
            p99.append(pct.get("p99", 0))
            rm.append(ts.get("rps_mean", 0))
            fm.append(ts.get("fr_mean", 0))

    fig, axes = plt.subplots(1, 5, figsize=(30, 7))

    panels = [
        (axes[0], s_lat_mean, g_lat_mean, "Avg Latency (ms)", "Avg Latency"),
        (axes[1], s_p95, g_p95, "P95 Latency (ms)", "P95 Latency"),
        (axes[2], s_p99, g_p99, "P99 Latency (ms)", "P99 Latency"),
        (axes[3], s_rps_mean, g_rps_mean, "Throughput (RPS)", "Throughput"),
        (axes[4], s_fr_mean, g_fr_mean, "Failure Rate (%)", "Fail Rate"),
    ]

    for ax, s_vals, g_vals, ylabel, subtitle in panels:
        _bar_scatter_plot(ax, x_pos, s_vals, g_vals,
                          ylabel=ylabel,
                          title=f"{CPS_LABELS[cps]}: {subtitle}")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(x_labels)
        ax.set_xlabel("Concurrent Users")
        _annotate_failures(ax, x_pos, s_vals, g_vals, s_fr_mean, g_fr_mean)

    plt.subplots_adjust(bottom=0.22)
    fname = f"{cps}_01_aggregated.png"
    plt.savefig(os.path.join(out_dir, fname), bbox_inches="tight")
    plt.close()
    print(f"  saved: {fname}")


def plot_full_comparison(cps, data, out_dir):
    """4-panel comparison: avg, p95, p99 latency, throughput."""
    users_list = sorted(data.keys())
    if not users_list:
        return

    x_labels = [str(u) for u in users_list]
    x_pos = np.arange(len(users_list))

    s_avg, g_avg = [], []
    s_p95, g_p95 = [], []
    s_p99, g_p99 = [], []
    s_rps, g_rps = [], []
    s_fr, g_fr = [], []

    for u in users_list:
        d = data[u]
        for prefix, avg, p95, p99, rps, fr in [
            ("server", s_avg, s_p95, s_p99, s_rps, s_fr),
            ("gateway", g_avg, g_p95, g_p99, g_rps, g_fr),
        ]:
            ts = d.get(f"{prefix}_ts", {})
            pct = d.get(f"{prefix}_agg_pct", {})
            avg.append(ts.get("lat_mean", 0))
            p95.append(pct.get("p95", 0))
            p99.append(pct.get("p99", 0))
            rps.append(ts.get("rps_mean", 0))
            fr.append(ts.get("fr_mean", 0))

    fig, axes = plt.subplots(1, 4, figsize=(24, 7))

    panels = [
        (axes[0], s_avg, g_avg, "Avg Latency (ms)", "Avg Latency"),
        (axes[1], s_p95, g_p95, "P95 Latency (ms)", "P95 Latency"),
        (axes[2], s_p99, g_p99, "P99 Latency (ms)", "P99 Latency"),
        (axes[3], s_rps, g_rps, "Throughput (RPS)", "Throughput"),
    ]

    for ax, s_vals, g_vals, ylabel, subtitle in panels:
        _bar_scatter_plot(ax, x_pos, s_vals, g_vals,
                          ylabel=ylabel,
                          title=f"{CPS_LABELS[cps]}: {subtitle}")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(x_labels)
        ax.set_xlabel("Concurrent Users")
        _annotate_failures(ax, x_pos, s_vals, g_vals, s_fr, g_fr)

    plt.subplots_adjust(bottom=0.22)
    fname = f"{cps}_05_full_comparison.png"
    plt.savefig(os.path.join(out_dir, fname), bbox_inches="tight")
    plt.close()
    print(f"  saved: {fname}")


def plot_per_category_separate(cps, data, out_dir):
    """Separate plots per category: avg/p95/p99 latency + throughput."""
    users_list = sorted(data.keys())
    if not users_list:
        return

    x_labels = [str(u) for u in users_list]
    x_pos = np.arange(len(users_list))

    CATEGORY_FNAME = {
        "initialize": ("a", "Initialize"),
        "tools_list": ("b", "Tools List"),
        "tool_call":  ("c", "Tool Calls"),
    }

    for cat, (letter, cat_label) in CATEGORY_FNAME.items():
        s_avg, g_avg = [], []
        s_p95, g_p95 = [], []
        s_p99, g_p99 = [], []
        s_rps, g_rps = [], []
        s_fr, g_fr = [], []
        has_data = False

        for u in users_list:
            d = data[u]
            for prefix, avg, p95, p99, rps, fr in [
                ("server", s_avg, s_p95, s_p99, s_rps, s_fr),
                ("gateway", g_avg, g_p95, g_p99, g_rps, g_fr),
            ]:
                cat_data = d.get(f"{prefix}_categories", {}).get(cat, {})
                avg_lat = cat_data.get("avg_lat", 0)
                avg.append(avg_lat)
                p95.append(cat_data.get("p95_lat", 0))
                p99.append(cat_data.get("p99_lat", 0))
                rps.append(cat_data.get("avg_rps", 0))
                fr.append(cat_data.get("avg_fr", 0))
                if avg_lat > 0:
                    has_data = True

        if not has_data:
            continue

        for metric, s_vals, g_vals, ylabel, suffix, num in [
            ("latency", s_avg, g_avg, "Avg Latency (ms)", "Avg Latency", "03"),
            ("p95", s_p95, g_p95, "P95 Latency (ms)", "P95 Latency", "03"),
            ("p99", s_p99, g_p99, "P99 Latency (ms)", "P99 Latency", "03"),
            ("throughput", s_rps, g_rps, "Throughput (RPS)", "Throughput", "04"),
        ]:
            fig, ax = plt.subplots(figsize=(12, 6))
            _bar_scatter_plot(ax, x_pos, s_vals, g_vals,
                              ylabel=ylabel,
                              title=f"{CPS_LABELS[cps]}: {cat_label} — {suffix}")
            ax.set_xticks(x_pos)
            ax.set_xticklabels(x_labels)
            ax.set_xlabel("Concurrent Users")
            _annotate_failures(ax, x_pos, s_vals, g_vals, s_fr, g_fr)
            plt.subplots_adjust(bottom=0.18)
            if metric == "throughput":
                fname = f"{cps}_{num}{letter}_throughput_{cat}.png"
            elif metric == "p95":
                fname = f"{cps}_{num}{letter}_latency_p95_{cat}.png"
            elif metric == "p99":
                fname = f"{cps}_{num}{letter}_latency_p99_{cat}.png"
            else:
                fname = f"{cps}_{num}{letter}_latency_{cat}.png"
            plt.savefig(os.path.join(out_dir, fname), bbox_inches="tight")
            plt.close()
            print(f"  saved: {fname}")


def plot_summary_table(all_data, out_dir):
    """Summary table comparing all CPS profiles and concurrency levels."""
    rows = []
    for cps in ["cps0", "cps1", "cps10"]:
        if cps not in all_data:
            continue
        for u in sorted(all_data[cps].keys()):
            d = all_data[cps][u]
            for target in ["server", "gateway"]:
                ts = d.get(f"{target}_ts", {})
                pct = d.get(f"{target}_agg_pct", {})
                rows.append({
                    "Session": CPS_LABELS.get(cps, cps),
                    "Users": u,
                    "Target": LABELS[target],
                    "Avg Lat (ms)": f"{ts.get('lat_mean', 0):.0f}",
                    "P95 (ms)": f"{pct.get('p95', 0):.0f}",
                    "P99 (ms)": f"{pct.get('p99', 0):.0f}",
                    "RPS": f"{ts.get('rps_mean', 0):.1f}",
                    "Fail %": f"{ts.get('fr_mean', 0):.1f}",
                })

    if not rows:
        return

    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(16, 2 + len(rows) * 0.35))
    ax.axis("off")

    table = ax.table(cellText=df.values, colLabels=df.columns,
                     cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.4)

    for j in range(len(df.columns)):
        cell = table[0, j]
        cell.set_facecolor("#37474F")
        cell.set_text_props(color="white", fontweight="bold")
    for i in range(1, len(rows) + 1):
        for j in range(len(df.columns)):
            cell = table[i, j]
            if "Gateway" in str(df.iloc[i - 1]["Target"]):
                cell.set_facecolor("#E3F2FD")
            else:
                cell.set_facecolor("#E8F5E9")

    ax.set_title("Mock MCP Server — Performance Summary", fontsize=14,
                 fontweight="bold", pad=20)

    plt.savefig(os.path.join(out_dir, "00_summary_table.png"), bbox_inches="tight")
    plt.close()
    print("  saved: 00_summary_table.png")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results_dir>")
        sys.exit(1)

    results_dir = sys.argv[1]
    plots_dir = os.path.join(results_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)

    print(f"generating plots from: {results_dir}")

    all_data = {}

    for cps in ["cps0", "cps1", "cps10"]:
        cps_dir = os.path.join(results_dir, cps)
        if not os.path.isdir(cps_dir):
            continue

        all_data[cps] = {}

        for u in CONCURRENCY_LEVELS:
            u_dir = os.path.join(cps_dir, f"u{u}")
            if not os.path.isdir(u_dir):
                continue

            entry = {}
            for target in ["server", "gateway"]:
                stats = load_stats(os.path.join(u_dir, f"{target}_stats.csv"))
                hist = load_history(os.path.join(u_dir, f"{target}_stats_history.csv"))

                entry[f"{target}_ts"] = agg_time_series_stats(hist)
                entry[f"{target}_categories"] = category_stats_from_stats(stats)
                entry[f"{target}_agg_pct"] = extract_aggregated_percentiles(stats)

            all_data[cps][u] = entry

    if not all_data:
        print("ERROR: no data found")
        sys.exit(1)

    print(f"loaded profiles: {list(all_data.keys())}")

    plot_summary_table(all_data, plots_dir)

    for cps in ["cps0", "cps1", "cps10"]:
        if cps not in all_data:
            continue
        print(f"\n  {cps} plots...")
        plot_aggregated(cps, all_data[cps], plots_dir)
        plot_full_comparison(cps, all_data[cps], plots_dir)
        plot_per_category_separate(cps, all_data[cps], plots_dir)

    print(f"\nall plots saved to: {plots_dir}")


if __name__ == "__main__":
    main()
