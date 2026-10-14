"""Forwarded addresses are accepted only through explicitly trusted hops."""
from ipaddress import ip_address, ip_network


def peer(remote, forwarded, networks=()):
    try:
        trusted = [ip_network(n.strip()) for n in networks if n.strip()]
        direct = ip_address(remote)
    except ValueError:
        return remote or "?"
    if not any(direct in n for n in trusted):
        return str(direct)
    try:
        hops = [ip_address(s.strip()) for s in forwarded.split(",") if s.strip()] + [direct]
    except ValueError:
        return str(direct)
    # Walk from the known connection, never accept a forged leftmost hop.
    while len(hops) > 1 and any(hops[-1] in n for n in trusted):
        hops.pop()
    return str(hops[-1])
