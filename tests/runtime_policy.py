#!/usr/bin/env python3
"""Team 19 runtime policy test (TC-31, TC-32, TC-35): throttle, unthrottle,
block and unblock one host during a transfer; bandwidth read from the controller CSV."""
import os, sys, csv, time, json, signal, shutil, hashlib, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "topology"))
from topo import build
from mininet.log import setLogLevel

SRV, SOCK = "10.0.0.100", "/tmp/sdn_ctl.sock"
STORE = os.path.join(ROOT, "storage")
CSVF = os.path.join(ROOT, "results", "bandwidth_log.csv")
NAME, SIZE = "rt80.bin", 80

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

def series():
    rows = []
    for r in csv.reader(open(CSVF)):
        try:
            t = float(r[0])
        except (ValueError, IndexError):
            continue
        if r[1] == "10.0.0.1":
            rows.append((t, float(r[6])))
    return rows

def mean(rows, a, b):
    v = [m for t, m in rows if a <= t <= b]
    return (sum(v) / len(v)) if v else 0.0

def send(cmd):
    subprocess.run(["python3", f"{ROOT}/controller/controller.py", "--send", cmd],
                   capture_output=True, text=True)

def main():
    setLogLevel("warning")
    os.makedirs(STORE, exist_ok=True)
    if os.path.exists(f"{STORE}/{NAME}"):
        os.remove(f"{STORE}/{NAME}")
    mp = os.path.join(ROOT, "metadata.json")
    if os.path.exists(mp):
        m = json.load(open(mp)); m.pop(NAME, None); json.dump(m, open(mp, "w"), indent=2)
    shutil.rmtree("/tmp/rt", ignore_errors=True); os.makedirs("/tmp/rt")
    src = f"/tmp/rt/{NAME}"
    open(src, "wb").write(os.urandom(SIZE * 1000000))
    want = sha(src)

    net, srv = build(10, True, 100)
    net.start()
    if os.path.exists(SOCK): os.remove(SOCK)
    ctl = subprocess.Popen(["python3", f"{ROOT}/controller/controller.py", "--quiet"],
                           stdin=subprocess.DEVNULL, stdout=open(f"{ROOT}/logs/controller_rt.log", "w"),
                           stderr=subprocess.STDOUT)
    for _ in range(50):
        if os.path.exists(SOCK): break
        time.sleep(0.2)
    time.sleep(1)
    srv.cmd(f"cd {ROOT} && python3 src/server.py > logs/server_stdout.log 2>&1 &")
    time.sleep(1)
    h1 = net.get("h1")
    up = h1.popen(["python3", f"{ROOT}/src/client.py", "--host", SRV, "-u", "alice", "-p", "alice123",
                   "upload", src], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(3)
    t0 = time.time()
    print("== Phase 1: normal (alice, 10 Mbps limit) ==", flush=True)
    time.sleep(12)
    print("== Phase 2: throttle h1 to 1000 kbps ==", flush=True)
    send("throttle h1 1000"); time.sleep(12)
    print("== Phase 3: unthrottle h1 ==", flush=True)
    send("unthrottle h1"); time.sleep(12)
    print("== Phase 4: block h1 ==", flush=True)
    send("block h1"); time.sleep(8)
    print("== Phase 5: unblock h1 ==", flush=True)
    send("unblock h1"); time.sleep(16)
    out = up.communicate(timeout=600)[0]
    time.sleep(1)
    rows = series()
    p1 = mean(rows, t0 + 2, t0 + 12)
    p2 = mean(rows, t0 + 14, t0 + 24)
    p3 = mean(rows, t0 + 26, t0 + 36)
    p4 = mean(rows, t0 + 38, t0 + 44)
    p5 = mean(rows, t0 + 50, t0 + 60)
    ctltxt = open(f"{ROOT}/logs/controller_rt.log").read()
    checks = [
        ("Throttle lowers speed (phase 2 < 60% of phase 1 and <= 2 Mbps)", p2 < 0.6 * p1 and p2 <= 2.0),
        ("Unthrottle restores speed (phase 3 > 1.5x phase 2)", p3 > 1.5 * p2),
        ("Block stops traffic (phase 4 < 0.3 Mbps)", p4 < 0.3),
        ("Unblock resumes traffic (phase 5 > 1 Mbps)", p5 > 1.0),
        ("Controller logged THROTTLE, BLOCKED and UNBLOCKED",
         all(k in ctltxt for k in ("THROTTLE", "BLOCKED", "UNBLOCKED"))),
        ("Upload completed despite throttle and block", "UPLOAD OK" in out),
        ("Stored file identical to source (SHA-256)",
         os.path.exists(f"{STORE}/{NAME}") and sha(f"{STORE}/{NAME}") == want),
    ]
    lines = ["", "===== RESULTS =====",
             f"Mean h1 Mbps per phase: normal={p1:.2f}  throttled={p2:.2f}  unthrottled={p3:.2f}  blocked={p4:.2f}  unblocked={p5:.2f}"]
    lines += [f"[{'PASS' if ok else 'FAIL'}] {n}" for n, ok in checks]
    txt = "\n".join(lines)
    print(txt, flush=True)
    open(f"{ROOT}/results/runtime_policy.txt", "w").write(txt + "\n")
    srv.cmd("pkill -f src/server.py")
    ctl.send_signal(signal.SIGINT)
    try: ctl.wait(timeout=5)
    except subprocess.TimeoutExpired: ctl.kill()
    net.stop()

if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    main()
