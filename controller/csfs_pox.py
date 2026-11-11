"""Team 19 POX controller (OpenFlow 1.0).
Rules: default deny, ARP flood, per-host TCP 5000 flows that enqueue into per-host OVS queues.
Rate limits = queue max-rate, changed at runtime (ovs-vsctl). Login events arrive over a Unix socket.
Flow statistics are requested every second and written to results/bandwidth_log.csv."""
import csv, json, os, re, socketserver, subprocess, threading, time
from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr
from pox.lib.recoco import Timer

log = core.getLogger()
SRV_IP, SRV_PORT, TCP_PORT = "10.0.0.100", 1, 5000
SOCK, BW_FILE = "/tmp/sdn_ctl.sock", "/tmp/csfs_bw"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATES = {"unauth": 1000, "user": 10000, "admin": 20000, "heavy": 2000}   # kbps
BLOCK_SECS = 30
lock = threading.RLock()
hosts = {}
state = {"conn": None, "ready": False, "quiet": False, "heavy": 0.0, "n": 10,
         "w": None, "f": None, "prev": 0.0}

def in_pox(fn, *a):
    try:
        core.callLater(fn, *a)
    except AttributeError:
        fn(*a)

def vsctl(*a):
    r = subprocess.run(["ovs-vsctl", "--timeout=5", *a], capture_output=True, text=True)
    if r.returncode != 0:
        log.warning("ovs-vsctl %s -> %s", " ".join(a)[:90], r.stderr.strip())
    return r.stdout.strip()

def first(s):
    return s.split()[0] if s else ""

def setup_queues(n, bw_bps):
    vsctl("--all", "destroy", "qos")
    vsctl("--all", "destroy", "queue")
    a = ["set", "port", "s1-eth1", "qos=@q", "--", "--id=@q", "create", "qos", "type=linux-htb",
         "other-config:max-rate=%d" % bw_bps]
    a += ["queues:%d=@u%d" % (i, i) for i in range(1, n + 1)]
    for i in range(1, n + 1):
        a += ["--", "--id=@u%d" % i, "create", "queue", "other-config:min-rate=100000",
              "other-config:max-rate=%d" % (RATES["unauth"] * 1000), "external_ids:csfs=up%d" % i]
    vsctl(*a)
    for i in range(1, n + 1):
        vsctl("set", "port", "s1-eth%d" % (i + 1), "qos=@q", "--", "--id=@q", "create", "qos",
              "type=linux-htb", "other-config:max-rate=100000000", "queues:1=@d", "--", "--id=@d",
              "create", "queue", "other-config:min-rate=100000",
              "other-config:max-rate=%d" % (RATES["unauth"] * 1000), "external_ids:csfs=down%d" % i)
    for ip, h in hosts.items():
        i = h["idx"]
        h["uq"] = first(vsctl("--bare", "--columns=_uuid", "find", "queue", "external_ids:csfs=up%d" % i))
        h["dq"] = first(vsctl("--bare", "--columns=_uuid", "find", "queue", "external_ids:csfs=down%d" % i))

def set_rate(h, kbps):
    bps = int(kbps * 1000)
    vsctl("set", "queue", h["uq"], "other-config:max-rate=%d" % bps, "--",
          "set", "queue", h["dq"], "other-config:max-rate=%d" % bps)
    h["rate"] = kbps

def init_hosts(n):
    for i in range(1, n + 1):
        hosts["10.0.0.%d" % i] = dict(idx=i, user=None, role=None, sessions=0, state="unauth",
                                      rate=RATES["unauth"], total=0, last=0, mbps=0.0, blocked=False,
                                      throttled=False, tk=RATES["heavy"], uq="", dq="")

def install_base(conn):
    conn.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))
    conn.send(of.ofp_flow_mod(match=of.ofp_match(), priority=1))          # default deny (no actions)
    fm = of.ofp_flow_mod(match=of.ofp_match(dl_type=0x0806), priority=100)
    fm.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    conn.send(fm)
    for ip, h in hosts.items():
        i = h["idx"]
        up = of.ofp_flow_mod(match=of.ofp_match(dl_type=0x0800, nw_proto=6, nw_src=IPAddr(ip),
                             nw_dst=IPAddr(SRV_IP), tp_dst=TCP_PORT), priority=50)
        up.actions.append(of.ofp_action_enqueue(port=SRV_PORT, queue_id=i))
        conn.send(up)
        dn = of.ofp_flow_mod(match=of.ofp_match(dl_type=0x0800, nw_proto=6, nw_src=IPAddr(SRV_IP),
                             nw_dst=IPAddr(ip), tp_src=TCP_PORT), priority=50)
        dn.actions.append(of.ofp_action_enqueue(port=i + 1, queue_id=1))
        conn.send(dn)

def send_block(ip, add):
    c = state["conn"]
    if c is None:
        return
    for field in ("nw_src", "nw_dst"):
        m = of.ofp_match(dl_type=0x0800, **{field: IPAddr(ip)})
        if add:
            c.send(of.ofp_flow_mod(match=m, priority=70))                 # drop
        else:
            c.send(of.ofp_flow_mod(command=of.OFPFC_DELETE_STRICT, match=m, priority=70))

def apply(h):
    if h["blocked"]:
        h["state"] = "blocked"
        return
    if h["user"] is None:
        r, st = RATES["unauth"], "unauth"
    elif h["throttled"]:
        r, st = h["tk"], "throttled"
    else:
        r, st = RATES.get(h["role"], RATES["user"]), "auth"
    if r != h["rate"]:
        set_rate(h, r)
    h["state"] = st

def block(ip, secs=None):
    with lock:
        h = hosts[ip]
        if h["blocked"]:
            return
        h["blocked"] = True
        in_pox(send_block, ip, True)
        apply(h)
        log.info("POLICY: BLOCKED h%d (%s)%s", h["idx"], ip, (" for %ds" % secs) if secs else "")
    if secs:
        threading.Timer(secs, unblock, [ip]).start()

def unblock(ip):
    with lock:
        h = hosts[ip]
        if not h["blocked"]:
            return
        h["blocked"] = False
        in_pox(send_block, ip, False)
        apply(h)
        log.info("POLICY: UNBLOCKED h%d (%s)", h["idx"], ip)

def status():
    if not state["ready"]:
        return "controller not ready (switch not connected yet)"
    rows = ["host ip          user     state      rate_kbps  Mbps"]
    for ip, h in sorted(hosts.items(), key=lambda kv: kv[1]["idx"]):
        rows.append("h%-3d %-11s %-8s %-10s %-10s %.2f" % (h["idx"], ip, h["user"], h["state"], h["rate"], h["mbps"]))
    return "\n".join(rows)

def command(text):
    p = text.split()
    if not p:
        return ""
    try:
        c = p[0]
        if c == "status":
            return status()
        if c == "flows":
            return subprocess.run(["ovs-ofctl", "dump-flows", "s1"], capture_output=True, text=True).stdout
        ip = "10.0.0.%d" % int(re.sub(r"[^0-9]", "", p[1]))
        if ip not in hosts:
            return "unknown host"
        with lock:
            h = hosts[ip]
            if c == "block":
                block(ip); return "blocked " + ip
            if c == "unblock":
                unblock(ip); return "unblocked " + ip
            if c == "throttle":
                if len(p) > 2:
                    h["tk"] = int(p[2])
                h["throttled"] = True; apply(h)
                log.info("POLICY: THROTTLE h%d to %d kbps", h["idx"], h["tk"]); return "ok"
            if c == "unthrottle":
                h["throttled"] = False; apply(h)
                log.info("POLICY: UNTHROTTLE h%d", h["idx"]); return "ok"
        return "commands: status | flows | block hN | unblock hN | throttle hN [kbps] | unthrottle hN"
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
            log.info("LOGIN user=%s role=%s host=h%d -> %s at %d kbps", h["user"], h["role"], h["idx"], h["state"], h["rate"])
        elif e == "LOGOUT":
            h["sessions"] = max(0, h["sessions"] - 1)
            if h["sessions"] == 0:
                h["user"] = h["role"] = None; h["throttled"] = False; h["total"] = 0
                apply(h)
                log.info("LOGOUT host=h%d -> back to restricted (%d kbps)", h["idx"], h["rate"])
        elif e == "FAILS":
            log.info("3 failed logins from h%d", h["idx"])
            block(ip, BLOCK_SECS)
    return "ok"

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            ev = json.loads(self.rfile.readline())
            self.wfile.write((on_event(ev) + "\n").encode())
        except Exception as ex:
            log.warning("handler error: %s", ex)

class Srv(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

def start_socket():
    if os.path.exists(SOCK):
        os.remove(SOCK)
    srv = Srv(SOCK, Handler)
    os.chmod(SOCK, 0o666)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

def poll():
    c = state["conn"]
    if c is not None and state["ready"]:
        c.send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))

def _stats(event):
    cur = {}
    for s in event.stats:
        if s.priority != 50:
            continue
        a, b = str(s.match.nw_src), str(s.match.nw_dst)
        ip = b if a == SRV_IP else a
        if ip in hosts:
            cur[ip] = cur.get(ip, 0) + s.byte_count
    now = time.time()
    dt = (now - state["prev"]) if state["prev"] else 1.0
    state["prev"] = now
    if dt <= 0:
        dt = 1.0
    with lock:
        for ip, h in hosts.items():
            b = cur.get(ip, 0)
            d = b - h["last"]
            if d < 0:
                d = b
            h["last"] = b; h["total"] += d
            h["mbps"] = d * 8 / dt / 1e6
            if state["w"]:
                state["w"].writerow(["%.2f" % now, ip, h["user"], h["state"], h["rate"], d, "%.3f" % h["mbps"]])
            if h["mbps"] > 0.05 and not state["quiet"]:
                log.info("[BW] h%d user=%s state=%s %.2f Mbps (limit %.1f)", h["idx"], h["user"], h["state"], h["mbps"], h["rate"] / 1000.0)
            if state["heavy"] and h["role"] == "user" and not h["throttled"] and h["total"] > state["heavy"]:
                h["throttled"] = True; apply(h)
                log.info("POLICY: heavy user h%d (%s) used %.0f MB -> throttled to %d kbps", h["idx"], h["user"], h["total"] / 1e6, h["rate"])
    if state["f"]:
        state["f"].flush()

def _up(event):
    first_time = not state["ready"]
    state["conn"] = event.connection
    log.info("switch connected (dpid %s)", event.dpid)
    if first_time:
        init_hosts(state["n"])
    install_base(event.connection)
    if first_time:
        try:
            bw = float(open(BW_FILE).read().strip())
        except Exception:
            bw = 100.0
        setup_queues(state["n"], int(bw * 1e6))
        os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
        state["f"] = open(os.path.join(ROOT, "results", "bandwidth_log.csv"), "a", newline="")
        state["w"] = csv.writer(state["f"])
        state["w"].writerow(["time", "ip", "user", "state", "rate_kbps", "bytes_interval", "mbps"])
        state["ready"] = True
        start_socket()
        log.info("controller ready: %d clients, server link %.0f Mbps, unauthenticated = %d kbps", state["n"], bw, RATES["unauth"])

def _down(event):
    if os.path.exists(SOCK):
        os.remove(SOCK)

def launch(quiet=False, heavy_mb=0, clients=10):
    state["quiet"] = str(quiet).lower() not in ("false", "0", "no")
    state["heavy"] = float(heavy_mb) * 1e6
    state["n"] = int(clients)
    def start():
        core.openflow.addListenerByName("ConnectionUp", _up)
        core.openflow.addListenerByName("FlowStatsReceived", _stats)
        core.addListenerByName("GoingDownEvent", _down)
        Timer(1, poll, recurring=True)
        log.info("csfs_pox loaded, waiting for the switch")
    core.call_when_ready(start, ["openflow"])
