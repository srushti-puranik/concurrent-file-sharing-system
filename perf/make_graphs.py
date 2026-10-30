#!/usr/bin/env python3
import csv, os, collections
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
rows = list(csv.DictReader(open(os.path.join(RES, "perf_summary.csv"))))

def fig(metric, ylabel, title, fname):
    fs, axs = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, d in zip(axs, ["upload", "download"]):
        series = collections.defaultdict(list)
        for r in rows:
            if r["direction"] == d:
                series[(r["mode"], r["size_mb"])].append((int(r["n"]), float(r[metric])))
        for (m, s), pts in sorted(series.items()):
            pts.sort()
            ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o",
                    ls="--" if m == "nosdn" else "-",
                    label=f"{'No SDN' if m == 'nosdn' else 'SDN'} {s} MB")
        ax.set_title(f"{title} - {d}")
        ax.set_xlabel("Simultaneous transfers")
        ax.set_ylabel(ylabel)
        ax.set_xticks([1, 3, 5, 10])
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fs.tight_layout()
    fs.savefig(os.path.join(RES, fname), dpi=150)
    print("saved", fname)

fig("mean_mbps", "Mean per-client throughput (Mbps)", "Per-client throughput", "graph_per_client.png")
fig("aggregate_mbps", "Aggregate bandwidth (Mbps)", "Aggregate bandwidth", "graph_aggregate.png")
fig("jain", "Jain fairness index (1 = fair)", "Fairness", "graph_fairness.png")
