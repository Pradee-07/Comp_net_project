"""
metrics.py -- QoS metrics from the paper's Eq 9-12, plus a simple energy model.

Each run of simulator.run() accumulates raw counters (packets sent/received,
total delay, bytes delivered, energy spent) and these functions turn those
counters into the reported metrics.
"""


def delay(total_delivery_time, packets_received):
    """Eq 9: average end-to-end delay = total delivery time / packets received (ms)."""
    if packets_received == 0:
        return 0.0
    return total_delivery_time / packets_received


def throughput(packets_received, packet_size_bits, total_time_s):
    """Eq 10: throughput = data delivered / total time (Mbps)."""
    if total_time_s == 0:
        return 0.0
    bits_delivered = packets_received * packet_size_bits
    return (bits_delivered / total_time_s) / 1e6


def packet_drop_ratio(packets_sent, packets_received):
    """Eq 11: (sent - received) / sent, as a fraction in [0, 1]."""
    if packets_sent == 0:
        return 0.0
    return (packets_sent - packets_received) / packets_sent


def pdr(packets_sent, packets_received):
    """Eq 12: packet delivery ratio = received / sent, as a fraction in [0, 1]."""
    if packets_sent == 0:
        return 0.0
    return packets_received / packets_sent


# --- Energy model (not numbered in the paper; a standard per-hop radio model) ---
TX_ENERGY_PER_HOP = 0.05    # mJ, energy spent transmitting one packet one hop
RX_ENERGY_PER_HOP = 0.03    # mJ, energy spent receiving one packet one hop
RETRY_ENERGY_PER_DROP = 0.08  # mJ, extra cost of a failed attempt (the radio still
                               # spends the airtime/power budget on a packet that
                               # gets dropped -- this is what makes congestion, not
                               # just delivery, the thing that burns energy)


def energy(hops_transmitted, hops_received):
    """Total energy spent (mJ) = hops_transmitted * TX cost + hops_received * RX
    cost + dropped_hops * RETRY cost. Dropped hops (hops_transmitted -
    hops_received) are charged extra: a failed transmission still spends
    airtime/power, so a scheme that drops more should cost more energy, not
    less -- without this term a scheme that merely *delivers more* looks
    like it costs more energy than one that drops everything, which inverts
    the paper's own claim that congestion control saves energy."""
    dropped = max(hops_transmitted - hops_received, 0)
    return (
        hops_transmitted * TX_ENERGY_PER_HOP
        + hops_received * RX_ENERGY_PER_HOP
        + dropped * RETRY_ENERGY_PER_DROP
    )
