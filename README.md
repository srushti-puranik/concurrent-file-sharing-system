# Concurrent File Sharing System (TCP) with SDN Access Control - Team 19

A multi-threaded TCP file server where authenticated users upload and download files at the same time. An SDN controller (Open vSwitch, OpenFlow 1.3 rules and meters) limits hosts that are not logged in, sets each host's speed limit from its user's role, blocks hosts after 3 failed logins, lets an operator throttle or block hosts at runtime, and measures per-host bandwidth every second.

## Folders
- src/ - server.py and client.py
- controller/ - controller.py (SDN controller)
- topology/ - topo.py (srv + h1..h10 on switch s1; --nosdn for the baseline)
- tests/ - hard_scenario.py and runtime_policy.py (self-checking)
- perf/ - run_perf.py (experiments) and make_graphs.py
- results/ - measurements, graphs and test outputs
- docs/ - PROTOCOL.md, SRS, SAD, Test Plan

## Requirements
Ubuntu with Python 3, Mininet 2.3.0, Open vSwitch 3.7.1. Mininet and the controller run with sudo. Graphs need python3-matplotlib.

## Default accounts
alice / alice123 (user), bob / bob123 (user), admin / admin123 (admin)

## Run the SDN network (two terminals)
Terminal 1:

    sudo python3 topology/topo.py

Terminal 2 (after the mininet> prompt appears):

    sudo python3 controller/controller.py

At the mininet> prompt:

    h1 python3 src/client.py --host 10.0.0.100 -u alice -p alice123 upload /tmp/file.bin

Controller console: status, flows, meters, block hN, unblock hN, throttle hN [kbps], unthrottle hN.

## Automated tests and experiments
    sudo python3 tests/runtime_policy.py
    sudo python3 tests/hard_scenario.py
    sudo python3 perf/run_perf.py
    python3 perf/make_graphs.py
    ./demo.sh        # all of the above in order

If a run fails with "File exists", run: sudo mn -c

## Results
- All 152 transfers in the performance run passed their SHA-256 check.
- With SDN each client gets about 4.5 to 6.4 Mbps whatever the number of clients. The configured limit is a ceiling: meters drop packets and TCP settles below it (10 Mbps limit gives about 5 to 6 Mbps).
- Without SDN one client uses most of the 100 Mbps link; with 10 clients each gets about 4.5 to 9.6 Mbps, with large variation.
- Fairness (Jain's index): 0.83 to 1.00 without SDN, 0.94 to 1.00 with SDN.
- Runtime test: one upload throttled to 0.63 Mbps, restored to 6 Mbps, blocked and resumed, file intact.
- Hard scenario: 9 uploads over a 30 Mbps link, one intruder, one client killed. The 8 survivors got an even share (Jain 0.983), no partial file was stored.

## Limitations
No encryption. Users are identified by host IP. The controller drives Open vSwitch with ovs-ofctl and a Unix socket, not an OpenFlow controller channel (Ryu is unreliable on Python 3.14). Ping is dropped by the policy. Each performance point is one run on a virtual machine.

## Team
Team 19: [member names]
