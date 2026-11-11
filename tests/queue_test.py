#!/usr/bin/env python3
"""Phase 1: do OVS queues cap speed, and can the cap change at runtime? (OpenFlow 1.0)
Uses plain Python TCP for the speed test, with time limits so nothing can hang."""
import subprocess, sys, time, os
from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.log import setLogLevel

SINK = """import socket,sys,time
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('10.0.0.2',int(sys.argv[1]))); s.listen(1); s.settimeout(15)
c,_=s.accept(); t0=time.time(); n=0
while True:
    d=c.recv(65536)
    if not d: break
    n+=len(d)
print(n, time.time()-t0)
"""
SEND = """import socket,sys,time
s=socket.create_connection(('10.0.0.2',int(sys.argv[1])),timeout=10)
end=time.time()+float(sys.argv[2]); buf=b'x'*65536
while time.time()<end: s.sendall(buf)
s.close()
"""

def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print("[error]", cmd, "->", r.stderr.strip())
    return r.stdout.strip()

def measure(h1, h2, port, secs=5):
    sink = h2.popen(["python3", "/tmp/q_sink.py", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.7)
    send = h1.popen(["python3", "/tmp/q_send.py", str(port), str(secs)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = sink.communicate(timeout=secs + 20)
        send.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        sink.kill(); send.kill()
        return "TIMEOUT (no connection)"
    try:
        n, dt = out.split()
        return f"{int(n) * 8 / float(dt) / 1e6:.2f} Mbps"
    except ValueError:
        return f"no result (sink said: {out.strip()} {err.strip()[:150]})"

def main():
    open("/tmp/q_sink.py", "w").write(SINK)
    open("/tmp/q_send.py", "w").write(SEND)
    setLogLevel("warning")
    sh("ovs-vsctl --all destroy qos"); sh("ovs-vsctl --all destroy queue")
    net = Mininet(controller=None, switch=OVSSwitch, autoSetMacs=True, waitConnected=False)
    s1 = net.addSwitch("s1", protocols="OpenFlow10", failMode="secure")
    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    net.addLink(h1, s1)   # switch port 1
    net.addLink(h2, s1)   # switch port 2
    net.start()
    sh("ovs-vsctl set port s1-eth2 qos=@q -- --id=@q create qos type=linux-htb "
       "other-config:max-rate=100000000 queues:1=@q1 -- --id=@q1 create queue "
       "other-config:min-rate=500000 other-config:max-rate=5000000")
    sh("ovs-ofctl del-flows s1")
    sh("ovs-ofctl add-flow s1 priority=100,arp,actions=flood")
    sh("ovs-ofctl add-flow s1 priority=50,ip,nw_dst=10.0.0.1,actions=output:1")
    sh("ovs-ofctl add-flow s1 priority=50,ip,nw_dst=10.0.0.2,actions=enqueue:2:1")
    print("queue config:", sh("ovs-vsctl list queue | grep -E 'other_config|_uuid'"))
    print("\n== Test A: queue capped at 5 Mbps (expect about 4 to 5) ==", flush=True)
    a = measure(h1, h2, 5001); print("result:", a, flush=True)
    print("flow counters:", sh("ovs-ofctl dump-flows s1 | grep -E 'nw_dst' | sed 's/duration=[^,]*, //'"), flush=True)
    uuid = sh("ovs-vsctl --bare --columns=_uuid find queue other-config:max-rate=5000000").split()[0]
    sh(f"ovs-vsctl set queue {uuid} other-config:max-rate=20000000")
    print("\n== Test B: SAME queue changed to 20 Mbps at runtime (expect about 15 to 20) ==", flush=True)
    b = measure(h1, h2, 5002); print("result:", b, flush=True)
    sh(f"ovs-vsctl set queue {uuid} other-config:max-rate=1000000")
    print("\n== Test C: SAME queue changed to 1 Mbps at runtime (expect about 0.8 to 1) ==", flush=True)
    c = measure(h1, h2, 5003); print("result:", c, flush=True)
    print("\n===== SUMMARY =====")
    print("5 Mbps cap :", a)
    print("20 Mbps cap:", b)
    print("1 Mbps cap :", c)
    net.stop()
    sh("ovs-vsctl --all destroy qos"); sh("ovs-vsctl --all destroy queue")

if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    main()
