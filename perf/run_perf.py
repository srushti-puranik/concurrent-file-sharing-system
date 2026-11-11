#!/usr/bin/env python3
"""Team 19 performance runner: N simultaneous transfers, SDN vs no SDN."""
import os, sys, csv, time, signal, shutil, subprocess, argparse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "topology"))
from topo import build
from mininet.log import setLogLevel

SRV = "10.0.0.100"
SOCK = "/tmp/sdn_ctl.sock"
RES = os.path.join(ROOT, "results")

def parse(out):
    for line in out.splitlines():
        if line.startswith(("UPLOAD OK", "DOWNLOAD")) and "Mbps" in line:
            secs = float(line.split(" in ")[1].split("s")[0])
            mbps = float(line.split("= ")[1].split(" ")[0])
            return secs, mbps
    return None

def run_phase(hosts, action, size_mb):
    procs = []
    for i, h in enumerate(hosts, 1):
        name = f"perf_{size_mb}mb_h{i}.bin"
        base = ["python3", f"{ROOT}/src/client.py", "--host", SRV,
                "-u", "alice", "-p", "alice123"]
        if action == "upload":
            cmd = base + ["upload", f"/tmp/perf/{name}"]
        else:
            cmd = base + ["--out", f"/tmp/perf_dl/h{i}", "download", name]
        procs.append((i, h.popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)))
    res = []
    for i, p in procs:
        out, _ = p.communicate(timeout=900)
        r = parse(out)
        ok = r is not None and (action == "upload" or "INTEGRITY OK" in out)
        res.append((i, r[0] if r else 0.0, r[1] if r else 0.0, ok))
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="5,20", help="file sizes in MB")
    ap.add_argument("--counts", default="1,3,5,10")
    ap.add_argument("--bw", type=float, default=100, help="server link Mbps")
    a = ap.parse_args()
    sizes = [int(x) for x in a.sizes.split(",")]
    counts = [int(x) for x in a.counts.split(",")]
    os.makedirs(RES, exist_ok=True)
    shutil.rmtree("/tmp/perf", ignore_errors=True)
    os.makedirs("/tmp/perf")
    for s in sizes:
        base = f"/tmp/perf/base_{s}.bin"
        with open(base, "wb") as f:
            f.write(os.urandom(s * 1000000))
        for i in range(1, 11):
            shutil.copyfile(base, f"/tmp/perf/perf_{s}mb_h{i}.bin")
    setLogLevel("warning")
    raw, summ = [], []
    for mode in ("nosdn", "sdn"):
        print(f"\n===== MODE: {mode} =====", flush=True)
        net, srv = build(10, mode == "sdn", a.bw)
        net.start()
        ctl = None
        if mode == "sdn":
            if os.path.exists(SOCK):
                os.remove(SOCK)
            ctl = subprocess.Popen(["python3", f"{ROOT}/controller/controller.py", "--quiet"],
                                   stdin=subprocess.DEVNULL,
                                   stdout=open(f"{ROOT}/logs/controller_perf.log", "w"),
                                   stderr=subprocess.STDOUT)
            for _ in range(150):
                if os.path.exists(SOCK):
                    break
                time.sleep(0.2)
            time.sleep(1)
        srv.cmd(f"cd {ROOT} && python3 src/server.py > logs/server_stdout.log 2>&1 &")
        time.sleep(1)
        hosts = [net.get(f"h{i}") for i in range(1, 11)]
        for s in sizes:
            for n in counts:
                for action in ("upload", "download"):
                    res = run_phase(hosts[:n], action, s)
                    xs = [r[2] for r in res]
                    window = max(r[1] for r in res) or 1e-9
                    agg = n * s * 8 / window
                    jain = (sum(xs) ** 2) / (n * sum(x * x for x in xs)) if sum(xs) > 0 else 0
                    ok = all(r[3] for r in res)
                    for r in res:
                        raw.append([mode, s, n, action, f"h{r[0]}", f"{r[1]:.3f}", f"{r[2]:.2f}", r[3]])
                    summ.append([mode, s, n, action, f"{sum(xs)/n:.2f}", f"{min(xs):.2f}",
                                 f"{max(xs):.2f}", f"{agg:.2f}", f"{jain:.3f}", ok])
                    print(f"{mode:6} {s:3}MB n={n:2} {action:8} mean={sum(xs)/n:7.2f} Mbps "
                          f"agg={agg:7.2f} jain={jain:.3f} ok={ok}", flush=True)
        srv.cmd("pkill -f src/server.py")
        if ctl:
            ctl.send_signal(signal.SIGINT)
            try:
                ctl.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ctl.kill()
        net.stop()
    with open(f"{RES}/perf_raw.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mode", "size_mb", "n", "direction", "host", "seconds", "mbps", "integrity_ok"])
        w.writerows(raw)
    with open(f"{RES}/perf_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mode", "size_mb", "n", "direction", "mean_mbps", "min_mbps",
                    "max_mbps", "aggregate_mbps", "jain", "integrity_ok"])
        w.writerows(summ)
    print("\nSaved results/perf_raw.csv and results/perf_summary.csv")

if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    main()
