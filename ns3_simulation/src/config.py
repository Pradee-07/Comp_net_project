"""
config.py -- all simulation parameters in one place (GEbPDCM + LTPM project).

Values in section 1 come directly from Table 1 of the paper:
Sandhya S. V., S. M. Joshi, "An optimized congestion control protocol in
cellular network for improving quality of service", MTAP 84:26267-26292 (2025).

Values in section 2 are tunables we chose ourselves (not from the paper) for
the GEO optimizer and our LTPM addition.
"""

# ---------------------------------------------------------------------------
# 1. Paper's Table 1 -- simulation parameters
# ---------------------------------------------------------------------------
AREA_WIDTH = 1000          # metres
AREA_HEIGHT = 1000         # metres
NUM_NODES = 100
BASE_STATION = (500, 450)  # (x, y) in metres

TX_RANGE = 300             # metres, transmission / neighbour range
DATA_RATE_MIN = 3.1086     # Mbps
DATA_RATE_MAX = 49.0946    # Mbps
BANDWIDTH = 27.175         # Mbps
JITTER_MS = 6.4            # ms

DEFAULT_SPEED = 10         # m/s
SPEED_SWEEP = [10, 20, 30, 40, 50]      # m/s, paper Table 7 (priority sweep)

DEFAULT_PACKET_SIZE_KB = 1
PACKET_SIZE_SWEEP = [1, 2, 3, 4, 5]     # Kb, paper Table 8 (cut first if short on time)

SIM_TIME = 100              # seconds

# Converts a node's per-tick offered Mbps rate into a packet count for that
# tick (simulator.py). Calibrated (not from the paper) so 100 nodes at the
# paper's own data-rate range produce network-aggregate throughput in the
# same order of magnitude as the paper's reported 40-95 Mbps, rather than
# the multi-gigabit total that treating every node's full rate as delivered
# every single tick would imply.
PACKET_RATE_SCALE = 30

# ---------------------------------------------------------------------------
# 2. Our tunables -- congestion thresholds, GEO, LTPM
# ---------------------------------------------------------------------------

# Eq 5 boundaries for the congestion score CS (see status.py):
#   CS < CONGESTION_THRESHOLD          -> 'safe'
#   CONGESTION_THRESHOLD <= CS < CONGESTED_LEVEL -> 'threshold'
#   CS >= CONGESTED_LEVEL              -> 'congested'
CONGESTION_THRESHOLD = 0.5
CONGESTED_LEVEL = 1.0

OPTIMAL_LOAD = 325          # Mbps, paper's O_l (Eq 8 comparison point)

# Reference load (Mbps) for the *aggregate drop-rate/queuing-delay* model in
# simulator.py -- deliberately separate from OPTIMAL_LOAD/O_l (which only
# gates the Eq 8 should_share() migration decision). This is our own
# engineering constant, calibrated against this simulator's own traffic
# model so a typical node's load produces a believable range of packet loss
# and queuing delay, rather than never leaving the noise floor.
SEVERITY_REFERENCE_LOAD = 90

# Eq 2 coefficient h. The paper's equation "P = Nc + h(sum Dl)" adds a
# *set of nodes* Nc to a *data load* sum(Dl), which is dimensionally
# meaningless as written. We interpret P as a monitored/predicted load:
#   P = h * (sum of the node's recent load readings)
# h is therefore a monitoring/scaling coefficient, not a literal paper
# constant -- chosen so P stays in the same order of magnitude as OPTIMAL_LOAD.
MONITOR_COEFF_H = 0.8

# --- LTPM: trust scoring (see ltpm.py) ---
TRUST_INIT = 0.5
TRUST_MIN = 0.6      # below this, a node is not eligible as a migration target
TRUST_ALPHA = 0.9    # EWMA factor: success -> T = a*T + (1-a); failure -> T = a*T

# Fraction of nodes seeded as persistently unreliable (malicious/failing,
# see topology.py's Node.reliable flag) and how much extra,
# congestion-independent drop probability they carry. Without this, every
# node's forwarding success rate is high enough that trust converges above
# TRUST_MIN for everyone, and LTPM's trust filter never actually excludes a
# candidate -- this gives it something real to route around.
UNRELIABLE_FRACTION = 0.10
INTRINSIC_FAIL_RATE = 0.3

# --- LTPM: predictive forecasting (see ltpm.py) ---
FORECAST_WINDOW = 5   # number of recent load readings used for linear extrapolation

# --- GEO: Golden Eagle Optimizer (see geo.py) ---
GEO_POPULATION = 20
GEO_ITERATIONS = 30

# Fitness weights for GEO candidate scoring (must sum to 1.0):
#   F = w_throughput*throughput + w_pdr*pdr - w_delay*delay - w_loss*loss - w_energy*energy
FITNESS_WEIGHTS = dict(
    throughput=0.3,
    pdr=0.3,
    delay=0.2,
    loss=0.1,
    energy=0.1,
)

# ---------------------------------------------------------------------------
# 3. Scheme names, in the order they should appear on every plot
# ---------------------------------------------------------------------------
SCHEMES = ["baseline", "gebpdcm", "gebpdcm_geo", "gebpdcm_ltpm"]

# Random seed for reproducible topology / mobility when NS-3 output is absent
RANDOM_SEED = 42
