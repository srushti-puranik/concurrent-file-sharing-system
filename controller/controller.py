#!/usr/bin/env python3
"""Team 19 SDN controller: OpenFlow 1.3 rules + meters on OVS bridge s1 via ovs-ofctl.
Policy: unauthenticated hosts may only reach srv:5000 at a tiny rate (login only).
LOGIN event -> rate by role. 3 failed logins -> host blocked 30s. Heavy users throttled.
Per-host byte counters are polled every second and logged to results/bandwidth_log.csv."""
import argparse, csv, json, os, re, socket, socketserver, subprocess, sys, threading, time

BR = "s1"
SRV_IP, SRV_PORT, TCP_PORT = "10.0.0.100", 1, 5000
SOCK = "/tmp/sdn_ctl.sock"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATES = {"unauth": 1000, "user": 10000, "admin": 20000, "heavy": 2000}   # kbps
BLOCK_SECS = 30
lock = threading.RLock()
hosts = {}

def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

def ofctl(*a):
    r = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", *a], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"[ofctl error] {' '.join(a)} -> {r.stderr.strip()}")
    return r.stdout

def meter(op, idx, kbps):
    ofctl(op, BR, f"meter={idx},kbps,burst,band=type=drop,rate={kbps},burst_size={max(100, kbps // 10)}")

def install_base(n):
    ofctl("del-flows", BR)
    ofctl("del-meter", BR, "meter=all")
    ofctl("add-flow", BR, "priority=0,actions=drop")
    ofctl("add-flow", BR, "priority=100,arp,actions=NORMAL")
    for i in range(1, n + 1):
        ip = f"10.0.0.{i}"
        hosts[ip] = dict(idx=i, user=None, role=None, sessions=0, state="unauth",
                         rate=RATES["unauth"], total=0, last=0, mbps=0.0,
                         blocked=False, throttled=False, tk=RATES["heavy"])
        meter("add-meter", i, RATES["unauth"])
        c = hex(0x100 + i)
        ofctl("add-flow", BR, f"cookie={c},priority=50,tcp,nw_src={ip},nw_dst={SRV_IP},"
                              f"tcp_dst={TCP_PORT},actions=meter:{i},output:{SRV_PORT}")
        ofctl("add-flow", BR, f"cookie={c},priority=50,tcp,nw_src={SRV_IP},nw_dst={ip},"
                              f"tcp_src={TCP_PORT},actions=meter:{i},output:{i + 1}")
    log(f"base rules installed for {n} clients (unauthenticated = {RATES['unauth']} kbps, login only)")

def apply(h):
    if h["blocked"]:
        h["state"] = "blocked"; return
    if h["user"] is None:
        r, st = RATES["unauth"], "unauth"
    elif h["throttled"]:
        r, st = h["tk"], "throttled"
    else:
        r, st = RATES.get(h["role"], RATES["user"]), "auth"
    if r != h["rate"]:
        meter("mod-meter", h["idx"], r)
        h["rate"] = r
    h["state"] = st

def block(ip, secs=None):
    with lock:
        h = hosts[ip]
        if h["blocked"]:
            return
        h["blocked"] = True
        c = hex(0xB000 + h["idx"])
        ofctl("add-flow", BR, f"cookie={c},priority=70,ip,nw_src={ip},actions=drop")
        ofctl("add-flow", BR, f"cookie={c},priority=70,ip,nw_dst={ip},actions=drop")
        apply(h)
        log(f"POLICY: BLOCKED h{h['idx']} ({ip})" + (f" for {secs}s" if secs else ""))
    if secs:
        threading.Timer(secs, unblock, [ip]).start()

def unblock(ip):
    with lock:
        h = hosts[ip]
        if not h["blocked"]:
            return
        ofctl("del-flows", BR, f"cookie={hex(0xB000 + h['idx'])}/-1")
        h["blocked"] = False
        apply(h)
        log(f"POLICY: UNBLOCKED h{h['idx']} ({ip})")

def status():
    rows = ["host ip          user     state      rate_kbps  Mbps"]
    for ip, h in sorted(hosts.items(), key=lambda kv: kv[1]["idx"]):
        rows.append(f"h{h['idx']:<3} {ip:<11} {str(h['user']):<8} {h['state']:<10} {h['rate']:<10} {h['mbps']:.2f}")
    return "\n".join(rows)

def command(text):
    p = text.split()
    if not p:
        return ""
    ipof = lambda t: f"10.0.0.{int(re.sub(r'[^0-9]', '', t))}"
    try:
        c = p[0]
        if c == "status": return status()
        if c == "flows": return ofctl("dump-flows", BR)
        if c == "meters": return ofctl("dump-meters", BR)
        ip = ipof(p[1])
        if ip not in hosts: return "unknown host"
        with lock:
            h = hosts[ip]
            if c == "block": block(ip); return f"blocked {ip}"
            if c == "unblock": unblock(ip); return f"unblocked {ip}"
            if c == "throttle":
                if len(p) > 2: h["tk"] = int(p[2])
                h["throttled"] = True; apply(h)
                log(f"POLICY: THROTTLE h{h['idx']} to {h['tk']} kbps"); return "ok"
            if c == "unthrottle":
                h["throttled"] = False; apply(h)
                log(f"POLICY: UNTHROTTLE h{h['idx']}"); return "ok"
        return "commands: status | flows | meters | block hN | unblock hN | throttle hN [kbps] | unthrottle hN"
    except (IndexError, ValueError):
        return "bad command"

def on_event(ev):
    e, ip = ev.get("event"), ev.get("ip")
    if e == "CMD":
        return command(ev.get("cmd", ""))
    with lock:
        h = hosts.get(ip)
        if h is None:
            return "unknown host"
        if e == "LOGIN":
            h["sessions"] += 1; h["user"] = ev["user"]; h["role"] = ev["role"]
            apply(h)
            log(f"LOGIN user={h['user']} role={h['role']} host=h{h['idx']} -> {h['state']} at {h['rate']} kbps")
        elif e == "LOGOUT":
            h["sessions"] = max(0, h["sessions"] - 1)
            if h["sessions"] == 0:
                h["user"] = h["role"] = None; h["throttled"] = False; h["total"] = 0
                apply(h)
                log(f"LOGOUT host=h{h['idx']} -> back to restricted ({h['rate']} kbps)")
        elif e == "FAILS":
            log(f"3 failed logins from h{h['idx']}")
            block(ip, BLOCK_SECS)
    return "ok"

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            ev = json.loads(self.rfile.readline())
            self.wfile.write((on_event(ev) + "\n").encode())
        except Exception as ex:
            log(f"[handler error] {ex}")

class Srv(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

def poller(n, heavy_bytes, quiet):
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    f = open(os.path.join(ROOT, "results", "bandwidth_log.csv"), "a", newline="")
    w = csv.writer(f)
    w.writerow(["time", "ip", "user", "state", "rate_kbps", "bytes_interval", "mbps"])
    prev = time.time()
    while True:
        time.sleep(1)
        out = ofctl("dump-flows", BR)
        now = time.time(); dt = now - prev; prev = now
        cur = {}
        for line in out.splitlines():
            m = re.search(r"cookie=0x([0-9a-f]+).*?n_bytes=(\d+)", line)
            if m:
                ck = int(m.group(1), 16)
                if 0x101 <= ck <= 0x100 + n:
                    cur[ck - 0x100] = cur.get(ck - 0x100, 0) + int(m.group(2))
        with lock:
            for ip, h in hosts.items():
                b = cur.get(h["idx"], 0)
                d = b - h["last"]
                if d < 0: d = b
                h["last"] = b; h["total"] += d
                h["mbps"] = d * 8 / dt / 1e6
                w.writerow([f"{now:.2f}", ip, h["user"], h["state"], h["rate"], d, f"{h['mbps']:.3f}"])
                if h["mbps"] > 0.05 and not quiet:
                    log(f"[BW] h{h['idx']} user={h['user']} state={h['state']} {h['mbps']:.2f} Mbps (limit {h['rate']/1000:.1f})")
                if (heavy_bytes and h["role"] == "user" and not h["throttled"]
                        and h["total"] > heavy_bytes):
                    h["throttled"] = True; apply(h)
                    log(f"POLICY: heavy user h{h['idx']} ({h['user']}) used {h['total']/1e6:.0f} MB -> throttled to {h['rate']} kbps")
        f.flush()

def send(cmd):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(SOCK)
    c.sendall(json.dumps({"event": "CMD", "cmd": cmd}).encode() + b"\n")
    data = b""
    while True:
        ch = c.recv(4096)
        if not ch: break
        data += ch
    print(data.decode().strip())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument("--heavy-mb", type=float, default=0, help="auto-throttle users after this many MB (0=off)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--send", help="send a command to a running controller")
    a = ap.parse_args()
    if a.send:
        send(a.send); return
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    if subprocess.run(["ovs-vsctl", "br-exists", BR]).returncode != 0:
        sys.exit("bridge s1 not found - start the topology first")
    install_base(a.clients)
    if os.path.exists(SOCK): os.remove(SOCK)
    srv = Srv(SOCK, Handler); os.chmod(SOCK, 0o666)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=poller, args=(a.clients, a.heavy_mb * 1e6, a.quiet), daemon=True).start()
    log("controller ready. commands: status | flows | meters | block hN | unblock hN | throttle hN [kbps] | unthrottle hN")
    try:
        while True:
            try:
                line = input("sdn> ")
            except EOFError:
                threading.Event().wait()
            out = command(line)
            if out: print(out)
    except KeyboardInterrupt:
        pass
    finally:
        if os.path.exists(SOCK): os.remove(SOCK)

if __name__ == "__main__":
    main()
