#!/usr/bin/env python3
"""Team 19 topology: srv + h1..hN on one OVS switch (s1).
SDN mode: remote POX controller (OpenFlow 1.0). --nosdn: plain learning switch."""
import os, sys, time, argparse
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def build(n, sdn, bw):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True, waitConnected=False)
    if sdn:
        net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6633)
        s1 = net.addSwitch("s1", protocols="OpenFlow10", failMode="secure")
    else:
        s1 = net.addSwitch("s1", protocols="OpenFlow13", failMode="standalone")
    srv = net.addHost("srv", ip="10.0.0.100/24")
    if sdn:
        # shaping on the host side only; the switch side is limited by OVS queues
        net.addLink(srv, s1, params1={"bw": bw})            # switch port 1
        for i in range(1, n + 1):                           # h1 -> port 2, h2 -> port 3 ...
            h = net.addHost("h%d" % i, ip="10.0.0.%d/24" % i)
            net.addLink(h, s1, params1={"bw": 100})
        open("/tmp/csfs_bw", "w").write(str(bw))
    else:
        net.addLink(srv, s1, bw=bw)
        for i in range(1, n + 1):
            h = net.addHost("h%d" % i, ip="10.0.0.%d/24" % i)
            net.addLink(h, s1, bw=100)
    return net, srv

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument("--bw", type=float, default=100, help="server link Mbps")
    ap.add_argument("--nosdn", action="store_true", help="plain learning switch")
    a = ap.parse_args()
    setLogLevel("info")
    net, srv = build(a.clients, not a.nosdn, a.bw)
    net.start()
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    srv.cmd("cd %s && python3 src/server.py > logs/server_stdout.log 2>&1 &" % ROOT)
    time.sleep(1)
    print("*** Mode:", "NO SDN (baseline)" if a.nosdn else "SDN: now start POX in another window: sudo python3 controller/controller.py")
    print("*** Server 10.0.0.100:5000 running on host 'srv'")
    CLI(net)
    srv.cmd("pkill -f src/server.py")
    net.stop()

if __name__ == "__main__":
    main()
