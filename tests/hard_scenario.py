#!/usr/bin/env python3
"""Team 19 hard scenario: 9 big uploads saturate the server link, an intruder
tries to get files, and one client is killed mid-transfer."""
import os, sys, json, time, glob, signal, shutil, hashlib, argparse, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "topology"))
from topo import build
from mininet.log import setLogLevel

SRV, SOCK = "10.0.0.100", "/tmp/sdn_ctl.sock"
STORE = os.path.join(ROOT, "storage")
LOG = os.path.join(ROOT, "logs", "server.log")

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

def speed(out):
    for line in out.splitlines():
        if line.startswith("UPLOAD OK") and "Mbps" in line:
            return float(line.split("= ")[1].split(" ")[0])
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=30, help="file size MB")
    ap.add_argument("--bw", type=float, default=30, help="server link Mbps (low = saturated)")
    ap.add_argument("--kill-after", type=int, default=15)
    a = ap.parse_args()
    setLogLevel("warning")
    os.makedirs(STORE, exist_ok=True)
    for f in glob.glob(f"{STORE}/big*.bin") + glob.glob(f"{STORE}/.tmp_*"):
        os.remove(f)
    mp = os.path.join(ROOT, "metadata.json")
    if os.path.exists(mp):
        m = json.load(open(mp))
        json.dump({k: v for k, v in m.items() if not k.startswith("big")}, open(mp, "w"), indent=2)
    shutil.rmtree("/tmp/hard", ignore_errors=True); os.makedirs("/tmp/hard")
    src = "/tmp/hard/src.bin"
    open(src, "wb").write(os.urandom(a.size * 1000000))
    for i in range(1, 10):
        shutil.copyfile(src, f"/tmp/hard/big{i}.bin")
    want = sha(src)
    log_start = os.path.getsize(LOG) if os.path.exists(LOG) else 0

    net, srv = build(10, True, a.bw)
    net.start()
    if os.path.exists(SOCK): os.remove(SOCK)
    ctl = subprocess.Popen(["python3", f"{ROOT}/controller/controller.py", "--quiet"],
                           stdin=subprocess.DEVNULL, stdout=open(f"{ROOT}/logs/controller_hard.log", "w"),
                           stderr=subprocess.STDOUT)
    for _ in range(50):
        if os.path.exists(SOCK): break
        time.sleep(0.2)
    time.sleep(1)
    srv.cmd(f"cd {ROOT} && python3 src/server.py > logs/server_stdout.log 2>&1 &")
    time.sleep(1)
    H = {i: net.get(f"h{i}") for i in range(1, 11)}
    base = ["python3", f"{ROOT}/src/client.py", "--host", SRV]
    def run(h, args, timeout=300):
        p = h.popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return p.communicate(timeout=timeout)[0]

    print(f"\n== Starting 9 uploads of {a.size} MB (server link {a.bw} Mbps) ==", flush=True)
    procs = {}
    for i in range(1, 10):
        procs[i] = H[i].popen(base + ["-u", "alice", "-p", "alice123", "upload", f"/tmp/hard/big{i}.bin"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(5)
    print("== Intruder: wrong password from h10 ==", flush=True)
    intr = run(H[10], base + ["-u", "alice", "-p", "wrong", "download", "big1.bin"])
    print(intr.strip(), flush=True)
    code = ("import socket,struct,json;s=socket.create_connection(('10.0.0.100',5000));"
            "d=json.dumps({'cmd':'LIST'}).encode();s.sendall(struct.pack('>I',len(d))+d);print(s.recv(200))")
    raw = run(H[10], ["python3", "-c", code])
    print("== Unauthenticated raw LIST from h10 ==\n" + raw.strip(), flush=True)
    time.sleep(max(0, a.kill_after - 5))
    print("== Killing client h9 mid-upload ==", flush=True)
    procs[9].send_signal(signal.SIGKILL)

    speeds, outs = {}, {}
    for i in range(1, 9):
        outs[i] = procs[i].communicate(timeout=900)[0]
        speeds[i] = speed(outs[i])
    xs = [v for v in speeds.values() if v]
    jain = (sum(xs) ** 2) / (len(xs) * sum(x * x for x in xs)) if xs else 0
    time.sleep(1)
    after = run(H[1], base + ["-u", "alice", "-p", "alice123", "list"])

    checks = []
    checks.append(("Eight surviving uploads completed", len(xs) == 8))
    checks.append(("Surviving files identical to source (SHA-256)",
                   all(os.path.exists(f"{STORE}/big{i}.bin") and sha(f"{STORE}/big{i}.bin") == want for i in range(1, 9))))
    checks.append(("big9.bin was NOT stored", not os.path.exists(f"{STORE}/big9.bin")))
    checks.append(("No leftover .tmp_ file", not glob.glob(f"{STORE}/.tmp_*")))
    logtxt = open(LOG).read()[log_start:] if os.path.exists(LOG) else ""
    checks.append(("Server logged UPLOAD INTERRUPTED for big9.bin", "UPLOAD INTERRUPTED" in logtxt and "big9.bin" in logtxt))
    checks.append(("Intruder refused", "LOGIN FAILED" in intr))
    checks.append(("Unauthenticated LIST refused", "login required" in raw))
    checks.append(("Server still serving after the kill", "big1.bin" in after))
    checks.append(("Fairness among survivors (Jain >= 0.9)", jain >= 0.9))

    lines = ["", "===== RESULTS =====",
             "Per-client upload speeds (Mbps): " + ", ".join(f"h{i}={speeds[i]}" for i in speeds),
             f"Aggregate of survivors: {sum(xs):.2f} Mbps   Jain fairness: {jain:.3f}"]
    lines += [f"[{'PASS' if ok else 'FAIL'}] {n}" for n, ok in checks]
    txt = "\n".join(lines)
    print(txt, flush=True)
    os.makedirs(f"{ROOT}/results", exist_ok=True)
    open(f"{ROOT}/results/hard_scenario.txt", "w").write(txt + "\n")

    srv.cmd("pkill -f src/server.py")
    ctl.send_signal(signal.SIGINT)
    try: ctl.wait(timeout=5)
    except subprocess.TimeoutExpired: ctl.kill()
    net.stop()

if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    main()
