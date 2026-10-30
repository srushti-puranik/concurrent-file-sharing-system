#!/usr/bin/env python3
"""Team 19 topology: srv + h1..hN clients on one OVS switch (s1)."""
import os, sys, time, argparse
from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def build(n, sdn, bw):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True, waitConnected=False)
    s1 = net.addSwitch("s1", protocols="OpenFlow13",
                       failMode="secure" if sdn else "standalone")
    srv = net.addHost("srv", ip="10.0.0.100/24")
    net.addLink(srv, s1, bw=bw)              # must be first: switch port 1
    for i in range(1, n + 1):                # h1 -> port 2, h2 -> port 3 ...
        h = net.addHost(f"h{i}", ip=f"10.0.0.{i}/24")
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
    srv.cmd(f"cd {ROOT} && python3 src/server.py > logs/server_stdout.log 2>&1 &")
    time.sleep(1)
    print("*** Mode:", "NO SDN (baseline)" if a.nosdn else "SDN (start controller in another window)")
    print("*** Server 10.0.0.100:5000 running on host 'srv'")
    CLI(net)
    srv.cmd("pkill -f src/server.py")
    net.stop()

if __name__ == "__main__":
    main()
