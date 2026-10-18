/*
 * gebpdcm.cc -- full NS-3 C++ port of GEbPDCM + GEO + LTPM (PROJECT_SPEC.md).
 *
 * Built on the proven-working scaffold from gebpdcm-baseline.cc (topology,
 * mobility, WiFi/AODV, traffic, FlowMonitor -- unchanged). Adds a per-node
 * congestion agent that runs once per simulated second, porting the same
 * math as src/status.py (Eq2-5), src/migrate.py (Eq6-8), src/geo.py and
 * src/ltpm.py -- so the congestion-control decisions themselves now act on
 * real per-node queue state and real packets, not an abstracted model.
 *
 * Migration ("share load with a neighbour", Eq6-8) has no direct NS-3
 * equivalent -- AODV computes its own routes. We implement it as a real,
 * observable network effect: install a per-node Ipv4 static host-route
 * override that forces this node's traffic to the base station through the
 * chosen neighbour instead of AODV's own next hop, for half the 1s tick
 * (approximating "50% of this tick's load moves"), then let it lapse so
 * AODV resumes. Static routes are consulted before AODV in ns-3's
 * Ipv4ListRouting by default priority, so this genuinely reroutes packets.
 *
 * Scheme dispatch mirrors src/migrate.py::select_target:
 *   gebpdcm      -> nearest neighbour whose status isn't congested
 *   gebpdcm_geo  -> geo.py's attack/cruise vector search, ported verbatim
 *   gebpdcm_ltpm -> geo search filtered to trust-eligible neighbours
 *                   (LTPM not yet wired in this revision)
 *
 * Usage:
 *   ./ns3 run "gebpdcm --scheme=gebpdcm --speed=10 --packetSize=1024 --simTime=20"
 * Add --verbose=true for the full per-tick status/migration log; default output
 * is a handful of progress checkpoints plus a clean final summary box.
 */

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <map>
#include <string>
#include <vector>
#include "tiny_tts_weights.h"

#include "ns3/aodv-module.h"
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/ipv4-l3-protocol.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/wifi-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("GEbPDCM");

// ---------------------------------------------------------------------------
// config.py mirror -- keep values identical to the Python side so this is a
// real port, not a re-tuned model.
// ---------------------------------------------------------------------------
static const double AREA_WIDTH = 1000.0;
static const double AREA_HEIGHT = 1000.0;
static const uint32_t NUM_NODES = 100;
static const double TX_RANGE = 300.0;
static const double DATA_RATE_MIN = 3.1086;
static const double DATA_RATE_MAX = 49.0946;
static const double MONITOR_COEFF_H = 0.8;
static const double OPTIMAL_LOAD = 325.0;
static const double CONGESTION_THRESHOLD = 0.5;
static const double CONGESTED_LEVEL = 1.0;
static const uint32_t FORECAST_WINDOW = 5;
static const double SEVERITY_REFERENCE_LOAD = 90.0; // same reference geo.py/status.py use for the aggregate model; here it scales the real queue-Mbps signal

static const double TX_ENERGY_PER_HOP = 0.05;
static const double RX_ENERGY_PER_HOP = 0.03;

// GEO (Golden Eagle Optimizer) -- geo.py port
static const uint32_t GEO_POPULATION = 20;
static const uint32_t GEO_ITERATIONS = 30;
static const double FIT_W_THROUGHPUT = 0.3;
static const double FIT_W_PDR = 0.3;
static const double FIT_W_DELAY = 0.2;
static const double FIT_W_LOSS = 0.1;
static const double FIT_W_ENERGY = 0.1;

// LTPM (Lightweight Trust and Predictive Module) -- ltpm.py port
static const double TRUST_INIT = 0.5;
static const double TRUST_MIN = 0.6;
static const double TRUST_ALPHA = 0.9;

enum class NodeStatusEnum
{
    SAFE,
    THRESHOLD,
    CONGESTED
};

struct NodeAgentState
{
    double load = 0.0;                 // Mbps, real signal: MAC queue bytes/sec this tick
    std::vector<double> loadHistory;
    std::vector<std::array<float, 5>> telemetryHistory; // multivariate lookback for Tiny-TTS
    NodeStatusEnum status = NodeStatusEnum::SAFE;
    double trust = TRUST_INIT;         // LTPM: EWMA trust, fed by real Ipv4L3Protocol Tx/Drop traces
    uint32_t txThisTick = 0;
    uint32_t dropThisTick = 0;
    float predLoad = 0.0f;
    float predRisk = 0.0f;
};

static std::vector<NodeAgentState> g_agents;
static std::vector<Ptr<Node>> g_mobileNodes;
static std::vector<Ptr<NetDevice>> g_devices; // mobile nodes' wifi devices, index-aligned
static std::vector<Ipv4Address> g_nodeAddrs;   // mobile nodes' wifi IPs, index-aligned
static Ipv4Address g_bsAddr;
static std::string g_scheme = "baseline";
static double g_simTime = 100.0;
static double g_speed = 10.0;
static uint32_t g_migrations = 0;
static bool g_verbose = false;

// --- status.py Eq2-5 port -------------------------------------------------

double
PredictDataLoad(const NodeAgentState& n)
{
    // Eq 2 -> P. Paper form P = Nc + h(sum Dl) is dimensionally meaningless
    // as written; same interpretation as status.py: P = h * sum(recent loads).
    double sum = 0.0;
    size_t start = n.loadHistory.size() > FORECAST_WINDOW ? n.loadHistory.size() - FORECAST_WINDOW : 0;
    for (size_t i = start; i < n.loadHistory.size(); ++i)
    {
        sum += n.loadHistory[i];
    }
    return MONITOR_COEFF_H * sum;
}

double
NodeDensity(const NodeAgentState& n, uint32_t neighbourCount)
{
    // Eq 3 -> Nd. Same interpretation as status.py: Nn = neighbour count,
    // (Pmax/Pt) = interference ratio, Pt floored at DATA_RATE_MIN.
    double pt = std::max(n.load, DATA_RATE_MIN);
    double interferenceRatio = DATA_RATE_MAX / pt;
    return n.load + neighbourCount * interferenceRatio;
}

double
NodeStatusScore(double P, double Nd)
{
    // Eq 4 -> Ns. Same 1.6 calibration factor as status.py::node_status.
    return (P + Nd) / (1.6 * OPTIMAL_LOAD);
}

NodeStatusEnum
Classify(double cs)
{
    if (cs < CONGESTION_THRESHOLD)
    {
        return NodeStatusEnum::SAFE;
    }
    if (cs < CONGESTED_LEVEL)
    {
        return NodeStatusEnum::THRESHOLD;
    }
    return NodeStatusEnum::CONGESTED;
}

const char*
StatusName(NodeStatusEnum s)
{
    switch (s)
    {
    case NodeStatusEnum::SAFE:
        return "safe";
    case NodeStatusEnum::THRESHOLD:
        return "threshold";
    default:
        return "congested";
    }
}

// --- real neighbour lookup, same TX_RANGE rule as topology.py ------------

std::vector<uint32_t>
NeighboursWithin(uint32_t nodeIdx)
{
    std::vector<uint32_t> result;
    Ptr<MobilityModel> mm = g_mobileNodes[nodeIdx]->GetObject<MobilityModel>();
    Vector pos = mm->GetPosition();
    for (uint32_t j = 0; j < g_mobileNodes.size(); ++j)
    {
        if (j == nodeIdx)
        {
            continue;
        }
        Ptr<MobilityModel> mmj = g_mobileNodes[j]->GetObject<MobilityModel>();
        double d = CalculateDistance(pos, mmj->GetPosition());
        if (d <= TX_RANGE)
        {
            result.push_back(j);
        }
    }
    return result;
}

// --- geo.py::fitness port. proxies built from each candidate's real state ---

double
GeoFitness(uint32_t candidateIdx, uint32_t sourceIdx)
{
    const NodeAgentState& c = g_agents[candidateIdx];
    double headroom = std::max(0.0, SEVERITY_REFERENCE_LOAD - c.load) / SEVERITY_REFERENCE_LOAD;
    double pdrProxy = (c.status == NodeStatusEnum::SAFE) ? 1.0
                     : (c.status == NodeStatusEnum::THRESHOLD) ? 0.5
                     : 0.0;
    Ptr<MobilityModel> mmSrc = g_mobileNodes[sourceIdx]->GetObject<MobilityModel>();
    Ptr<MobilityModel> mmCand = g_mobileNodes[candidateIdx]->GetObject<MobilityModel>();
    double delayProxy = CalculateDistance(mmSrc->GetPosition(), mmCand->GetPosition()) / TX_RANGE;
    double lossProxy = std::min(c.load / (2 * SEVERITY_REFERENCE_LOAD), 1.0);
    // energy_proxy: fraction of this candidate's queue-based load headroom already spent,
    // real per-node energy accounting is not tracked per-node in this scenario (only the
    // aggregate FlowMonitor-derived total is), so this uses the same load-based proxy geo.py
    // falls back to when no full-flow simulation is available to score a hypothetical target.
    double energyProxy = std::min(c.load / SEVERITY_REFERENCE_LOAD, 1.0);

    return FIT_W_THROUGHPUT * headroom + FIT_W_PDR * pdrProxy - FIT_W_DELAY * delayProxy
         - FIT_W_LOSS * lossProxy - FIT_W_ENERGY * energyProxy;
}

// GEO attack/cruise vector search over `candidates`, returns the best node index.
// Direct port of geo.py::select -- eagles seeded at candidate positions, scored via
// the nearest real candidate to their current continuous position.
int32_t
GeoSelect(uint32_t sourceIdx, const std::vector<uint32_t>& candidates)
{
    if (candidates.empty())
    {
        return -1;
    }
    Ptr<UniformRandomVariable> rv = CreateObject<UniformRandomVariable>();
    double stepSize = TX_RANGE / 10.0;

    std::vector<std::array<double, 2>> eagles;
    for (uint32_t i = 0; i < GEO_POPULATION; ++i)
    {
        uint32_t pick = candidates[static_cast<uint32_t>(rv->GetValue(0, candidates.size()))];
        Vector pos = g_mobileNodes[pick]->GetObject<MobilityModel>()->GetPosition();
        eagles.push_back({pos.x, pos.y});
    }

    uint32_t bestIdx = candidates[0];
    double bestFit = GeoFitness(bestIdx, sourceIdx);
    for (uint32_t c : candidates)
    {
        double f = GeoFitness(c, sourceIdx);
        if (f > bestFit)
        {
            bestFit = f;
            bestIdx = c;
        }
    }
    std::array<double, 2> bestPos = {
        g_mobileNodes[bestIdx]->GetObject<MobilityModel>()->GetPosition().x,
        g_mobileNodes[bestIdx]->GetObject<MobilityModel>()->GetPosition().y};

    for (uint32_t it = 0; it < GEO_ITERATIONS; ++it)
    {
        double pAttack = 0.5 + (2.0 - 0.5) * it / std::max<uint32_t>(GEO_ITERATIONS - 1, 1);
        double pCruise = 1.0 - (1.0 - 0.5) * it / std::max<uint32_t>(GEO_ITERATIONS - 1, 1);

        for (auto& eagle : eagles)
        {
            double ax = bestPos[0] - eagle[0];
            double ay = bestPos[1] - eagle[1];
            double alen = std::sqrt(ax * ax + ay * ay);
            alen = alen > 1e-9 ? alen : 1e-9;
            double aux = ax / alen, auy = ay / alen;

            double cux = -auy, cuy = aux;
            if (rv->GetValue(0, 1) < 0.5)
            {
                cux = -cux;
                cuy = -cuy;
            }

            double moveX = (pAttack * aux + pCruise * cux) * stepSize;
            double moveY = (pAttack * auy + pCruise * cuy) * stepSize;
            eagle[0] = std::min(std::max(eagle[0] + moveX, 0.0), AREA_WIDTH);
            eagle[1] = std::min(std::max(eagle[1] + moveY, 0.0), AREA_HEIGHT);

            uint32_t nearest = candidates[0];
            double nearestDist = 1e18;
            for (uint32_t c : candidates)
            {
                Vector p = g_mobileNodes[c]->GetObject<MobilityModel>()->GetPosition();
                double d = std::hypot(p.x - eagle[0], p.y - eagle[1]);
                if (d < nearestDist)
                {
                    nearestDist = d;
                    nearest = c;
                }
            }
            double f = GeoFitness(nearest, sourceIdx);
            if (f > bestFit)
            {
                bestFit = f;
                bestIdx = nearest;
                Vector p = g_mobileNodes[nearest]->GetObject<MobilityModel>()->GetPosition();
                bestPos = {p.x, p.y};
            }
        }
    }
    return static_cast<int32_t>(bestIdx);
}

// --- ltpm.py port: trust EWMA fed by real per-node Tx/Drop trace counts --

void
UpdateTrust(uint32_t nodeIdx, bool success)
{
    NodeAgentState& n = g_agents[nodeIdx];
    n.trust = success ? (TRUST_ALPHA * n.trust + (1 - TRUST_ALPHA)) : (TRUST_ALPHA * n.trust);
    n.trust = std::min(std::max(n.trust, 0.0), 1.0);
}

bool
Trusted(uint32_t nodeIdx)
{
    return g_agents[nodeIdx].trust >= TRUST_MIN;
}

// Load forecast: returns Tiny-TTS predicted load if available, or least-squares slope fallback.
double
ForecastLoad(const NodeAgentState& n)
{
    if (n.predLoad > 0.0f)
    {
        return static_cast<double>(n.predLoad);
    }
    const auto& h = n.loadHistory;
    if (h.size() < 2)
    {
        return h.empty() ? 0.0 : h.back();
    }
    uint32_t sz = h.size();
    double meanX = (sz - 1) / 2.0;
    double meanY = 0.0;
    for (double v : h)
    {
        meanY += v;
    }
    meanY /= sz;
    double num = 0.0, den = 0.0;
    for (uint32_t x = 0; x < sz; ++x)
    {
        num += (x - meanX) * (h[x] - meanY);
        den += (x - meanX) * (x - meanX);
    }
    den = den > 1e-9 ? den : 1e-9;
    double slope = num / den;
    double intercept = meanY - slope * meanX;
    return slope * sz + intercept; // one step past the last reading
}

// Preemptive migration: uses Tiny-TTS Transformer risk head and load forecast.
bool
ShouldPreemptivelyMigrate(const NodeAgentState& n)
{
    const double ref = SEVERITY_REFERENCE_LOAD;
    const double riskBar = 0.5;
    double currentSeverity = std::max(0.0, n.load / ref - 0.5);
    if (currentSeverity >= riskBar)
    {
        return false;
    }

    if (!n.telemetryHistory.empty())
    {
        double forecastSeverity = std::max(0.0, static_cast<double>(n.predLoad) / ref - 0.5);
        return (n.predRisk >= 0.5f) || (forecastSeverity >= riskBar);
    }

    double forecastSeverity = std::max(0.0, ForecastLoad(n) / ref - 0.5);
    return currentSeverity < riskBar && forecastSeverity >= riskBar;
}

// Real success/failure signal for trust, fed from Ipv4L3Protocol's own Tx
// and Drop trace sources (not a synthetic coin flip like the Python side's
// trust_rng.random() > drop_rate).
void
TxTraceCallback(uint32_t nodeIdx, Ptr<const Packet> packet, Ptr<Ipv4> ipv4, uint32_t iface)
{
    g_agents[nodeIdx].txThisTick++;
}

void
DropTraceCallback(uint32_t nodeIdx,
                   const Ipv4Header& header,
                   Ptr<const Packet> packet,
                   Ipv4L3Protocol::DropReason reason,
                   Ptr<Ipv4> ipv4,
                   uint32_t iface)
{
    g_agents[nodeIdx].dropThisTick++;
}

// --- migration target selection, mirrors migrate.py::select_target -------

int32_t
SelectMigrationTarget(uint32_t sourceIdx, const std::vector<uint32_t>& neighbours)
{
    std::vector<uint32_t> free;
    for (uint32_t n : neighbours)
    {
        if (g_agents[n].status != NodeStatusEnum::CONGESTED)
        {
            free.push_back(n);
        }
    }
    if (free.empty())
    {
        return -1;
    }

    if (g_scheme == "gebpdcm")
    {
        Vector srcPos = g_mobileNodes[sourceIdx]->GetObject<MobilityModel>()->GetPosition();
        uint32_t best = free[0];
        double bestDist = 1e18;
        for (uint32_t n : free)
        {
            double d = CalculateDistance(srcPos, g_mobileNodes[n]->GetObject<MobilityModel>()->GetPosition());
            if (d < bestDist)
            {
                bestDist = d;
                best = n;
            }
        }
        return static_cast<int32_t>(best);
    }

    if (g_scheme == "gebpdcm_ltpm")
    {
        std::vector<uint32_t> trustedFree;
        for (uint32_t n : free)
        {
            if (Trusted(n))
            {
                trustedFree.push_back(n);
            }
        }
        if (trustedFree.empty())
        {
            return -1;
        }
        return GeoSelect(sourceIdx, trustedFree);
    }

    // gebpdcm_geo
    return GeoSelect(sourceIdx, free);
}

// --- real migration mechanism: bounded-duration static host-route override

void
RemoveMigrationRoute(uint32_t srcIdx, uint32_t routeIdx)
{
    Ipv4StaticRoutingHelper srHelper;
    Ptr<Ipv4StaticRouting> sr = srHelper.GetStaticRouting(g_mobileNodes[srcIdx]->GetObject<Ipv4>());
    if (routeIdx < sr->GetNRoutes())
    {
        sr->RemoveRoute(routeIdx);
    }
}

void
InstallMigrationRoute(uint32_t srcIdx, uint32_t targetIdx)
{
    Ipv4StaticRoutingHelper srHelper;
    Ptr<Ipv4StaticRouting> sr = srHelper.GetStaticRouting(g_mobileNodes[srcIdx]->GetObject<Ipv4>());
    const uint32_t wifiIfIndex = 1; // 0 = loopback, 1 = the single wifi interface installed
    sr->AddHostRouteTo(g_bsAddr, g_nodeAddrs[targetIdx], wifiIfIndex);
    uint32_t routeIdx = sr->GetNRoutes() - 1;
    g_migrations++;
    // Half the 1s tick -- approximates Eq6-8's "50% of load moves" as a
    // real, bounded-duration reroute rather than a literal byte split.
    Simulator::Schedule(Seconds(0.5), &RemoveMigrationRoute, srcIdx, routeIdx);
}

// --- real load sampling: WiFi MAC transmit queue bytes at this instant ---

double
SampleQueueLoadMbps(uint32_t nodeIdx)
{
    Ptr<WifiNetDevice> wifiDev = DynamicCast<WifiNetDevice>(g_devices[nodeIdx]);
    if (!wifiDev)
    {
        return 0.0;
    }
    Ptr<WifiMac> mac = wifiDev->GetMac();
    Ptr<WifiMacQueue> queue = mac->GetTxopQueue(AC_BE);
    if (!queue)
    {
        return 0.0;
    }
    uint32_t nBytes = queue->GetNBytes();
    // bytes currently queued -> instantaneous Mbps if drained over 1 tick
    return (nBytes * 8.0) / 1e6;
}

// --- tick loop -------------------------------------------------------------

void
Tick(uint32_t tickNum)
{
    uint32_t safeCount = 0, thresholdCount = 0, congestedCount = 0;

    for (uint32_t i = 0; i < g_mobileNodes.size(); ++i)
    {
        NodeAgentState& n = g_agents[i];
        double prevLoad = n.load;
        double sampled = SampleQueueLoadMbps(i);
        n.load = sampled;
        n.loadHistory.push_back(sampled);
        if (n.loadHistory.size() > FORECAST_WINDOW)
        {
            n.loadHistory.erase(n.loadHistory.begin());
        }

        std::vector<uint32_t> neighbours = NeighboursWithin(i);

        // Record multivariate telemetry for Tiny-TTS
        float delta = static_cast<float>(sampled - prevLoad);
        float n_count = static_cast<float>(neighbours.size());
        float drops = static_cast<float>(n.dropThisTick);
        float sp = static_cast<float>(g_speed);
        n.telemetryHistory.push_back({static_cast<float>(sampled), delta, n_count, drops, sp});
        if (n.telemetryHistory.size() > TinyTTS::SEQ_LEN)
        {
            n.telemetryHistory.erase(n.telemetryHistory.begin());
        }

        // Run Tiny-TTS Edge Transformer prediction for LTPM
        if (g_scheme == "gebpdcm_ltpm")
        {
            float window[TinyTTS::SEQ_LEN][TinyTTS::D_INPUT] = {{0}};
            size_t histSz = n.telemetryHistory.size();
            for (size_t t = 0; t < TinyTTS::SEQ_LEN; ++t)
            {
                size_t srcIdx = (t + histSz >= TinyTTS::SEQ_LEN) ? (t + histSz - TinyTTS::SEQ_LEN) : 0;
                for (int f = 0; f < TinyTTS::D_INPUT; ++f)
                {
                    window[t][f] = n.telemetryHistory[srcIdx][f];
                }
            }
            TinyTTS::Predict(window, n.predLoad, n.predRisk);
        }

        double P = PredictDataLoad(n);
        double Nd = NodeDensity(n, neighbours.size());
        double cs = NodeStatusScore(P, Nd);
        n.status = Classify(cs);

        bool needsMigration = n.status == NodeStatusEnum::CONGESTED
            || (g_scheme == "gebpdcm_ltpm" && ShouldPreemptivelyMigrate(n));
        if (needsMigration && g_scheme != "baseline")
        {
            int32_t target = SelectMigrationTarget(i, neighbours);
            if (target >= 0)
            {
                InstallMigrationRoute(i, static_cast<uint32_t>(target));
            }
        }

        if (g_scheme == "gebpdcm_ltpm")
        {
            // One trust update per node per tick, from this tick's real
            // Ipv4L3Protocol Tx/Drop trace counts -- did this node forward
            // cleanly, or did it see a drop this tick?
            bool success = n.txThisTick > 0 && n.dropThisTick == 0;
            if (n.txThisTick > 0 || n.dropThisTick > 0)
            {
                UpdateTrust(i, success);
            }
            n.txThisTick = 0;
            n.dropThisTick = 0;
        }

        switch (n.status)
        {
        case NodeStatusEnum::SAFE:
            safeCount++;
            break;
        case NodeStatusEnum::THRESHOLD:
            thresholdCount++;
            break;
        case NodeStatusEnum::CONGESTED:
            congestedCount++;
            break;
        }
    }

    if (g_verbose)
    {
        std::cout << "  [tick " << tickNum << "] safe=" << safeCount
                  << " threshold=" << thresholdCount << " congested=" << congestedCount
                  << " migrations=" << g_migrations
                  << std::endl;
    }
    else
    {
        // Default: a handful of clean checkpoints (0%, 25%, 50%, 75%) instead of
        // one line per simulated second -- keeps a 100-tick run's output readable
        // while still showing the run is alive and how congestion is trending.
        uint32_t total = static_cast<uint32_t>(g_simTime);
        uint32_t checkpoint = std::max<uint32_t>(total / 4, 1);
        if (tickNum % checkpoint == 0)
        {
            uint32_t pct = (tickNum * 100) / std::max<uint32_t>(total, 1);
            std::cout << "  " << std::setw(3) << pct << "%  tick " << std::setw(3) << tickNum
                      << "/" << total << "   congested " << std::setw(3) << congestedCount
                      << "/100   migrations so far: " << g_migrations << std::endl;
        }
    }

    if (tickNum + 1 < static_cast<uint32_t>(g_simTime))
    {
        Simulator::Schedule(Seconds(1.0), &Tick, tickNum + 1);
    }
}

// ---------------------------------------------------------------------------

int
main(int argc, char* argv[])
{
    double bsX = 500.0;
    double bsY = 450.0;
    double simTime = 100.0;
    double speed = 10.0;
    uint32_t packetSize = 1024;
    std::string scheme = "baseline";
    // Empty by default -- resolved after arg parsing to $HOME/computer_networks_project/ns3_simulation/data/
    // unless explicitly overridden, so callers never have to hardcode a path in sync with
    // wherever this scenario happens to be checked out.
    std::string outDir = "";
    bool verbose = false;

    CommandLine cmd(__FILE__);
    cmd.AddValue("scheme", "baseline|gebpdcm|gebpdcm_geo|gebpdcm_ltpm", scheme);
    cmd.AddValue("speed", "Node speed in m/s (RandomWaypoint)", speed);
    cmd.AddValue("packetSize", "OnOff application packet size in bytes", packetSize);
    cmd.AddValue("simTime", "Simulation duration in seconds", simTime);
    cmd.AddValue("outDir", "Directory to write topology.csv / ns3_<scheme>_speed<N>.csv into", outDir);
    cmd.AddValue("verbose", "Print full per-tick status instead of 4 progress checkpoints", verbose);
    cmd.Parse(argc, argv);
    g_verbose = verbose;

    if (outDir.empty())
    {
        const char* home = std::getenv("HOME");
        outDir = std::string(home ? home : ".") + "/computer_networks_project/ns3_simulation/data/";
    }

    g_scheme = scheme;
    g_simTime = simTime;
    g_speed = speed;

    Config::SetDefault("ns3::WifiRemoteStationManager::NonUnicastMode", StringValue("DsssRate2Mbps"));

    RngSeedManager::SetSeed(42);
    RngSeedManager::SetRun(1);

    NodeContainer mobileNodes;
    mobileNodes.Create(NUM_NODES);
    NodeContainer bsNode;
    bsNode.Create(1);

    NodeContainer allNodes;
    allNodes.Add(mobileNodes);
    allNodes.Add(bsNode);

    ObjectFactory posFactory;
    posFactory.SetTypeId("ns3::RandomRectanglePositionAllocator");
    posFactory.Set("X", StringValue("ns3::UniformRandomVariable[Min=0.0|Max=" + std::to_string(AREA_WIDTH) + "]"));
    posFactory.Set("Y", StringValue("ns3::UniformRandomVariable[Min=0.0|Max=" + std::to_string(AREA_HEIGHT) + "]"));
    Ptr<PositionAllocator> posAlloc = posFactory.Create()->GetObject<PositionAllocator>();

    MobilityHelper mobility;
    mobility.SetPositionAllocator(posAlloc);
    mobility.SetMobilityModel(
        "ns3::RandomWaypointMobilityModel",
        "Speed", StringValue("ns3::ConstantRandomVariable[Constant=" + std::to_string(speed) + "]"),
        "Pause", StringValue("ns3::ConstantRandomVariable[Constant=0.0]"),
        "PositionAllocator", PointerValue(posAlloc));
    mobility.Install(mobileNodes);

    MobilityHelper bsMobility;
    Ptr<ListPositionAllocator> bsPos = CreateObject<ListPositionAllocator>();
    bsPos->Add(Vector(bsX, bsY, 0.0));
    bsMobility.SetPositionAllocator(bsPos);
    bsMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    bsMobility.Install(bsNode);

    std::map<uint32_t, Vector> initialPositions;
    for (uint32_t i = 0; i < mobileNodes.GetN(); ++i)
    {
        Ptr<MobilityModel> mm = mobileNodes.Get(i)->GetObject<MobilityModel>();
        initialPositions[i] = mm->GetPosition();
    }

    YansWifiChannelHelper wifiChannel;
    wifiChannel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    wifiChannel.AddPropagationLoss("ns3::FriisPropagationLossModel");
    wifiChannel.AddPropagationLoss("ns3::RangePropagationLossModel", "MaxRange", DoubleValue(TX_RANGE));

    YansWifiPhyHelper wifiPhy;
    wifiPhy.SetChannel(wifiChannel.Create());

    WifiMacHelper wifiMac;
    wifiMac.SetType("ns3::AdhocWifiMac");

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211b);
    wifi.SetRemoteStationManager(
        "ns3::ConstantRateWifiManager", "DataMode", StringValue("DsssRate2Mbps"),
        "ControlMode", StringValue("DsssRate2Mbps"));

    NetDeviceContainer devices = wifi.Install(wifiPhy, wifiMac, allNodes);

    for (uint32_t i = 0; i < mobileNodes.GetN(); ++i)
    {
        g_mobileNodes.push_back(mobileNodes.Get(i));
        g_devices.push_back(devices.Get(i));
    }
    g_agents.resize(NUM_NODES);

    // Static routing (priority 10, higher wins) alongside AODV (priority 0)
    // -- AODV alone (the reference example's own wiring) has no static
    // routing companion, so our migration-route overrides need one added
    // explicitly, at a priority that beats AODV's own route for the same
    // destination while a migration override is active.
    AodvHelper aodv;
    Ipv4StaticRoutingHelper staticRoutingHelper;
    Ipv4ListRoutingHelper listRouting;
    listRouting.Add(staticRoutingHelper, 10);
    listRouting.Add(aodv, 0);
    InternetStackHelper internet;
    internet.SetRoutingHelper(listRouting);
    internet.Install(allNodes);

    Ipv4AddressHelper address;
    address.SetBase("10.1.1.0", "255.255.255.0");
    Ipv4InterfaceContainer interfaces = address.Assign(devices);

    for (uint32_t i = 0; i < NUM_NODES; ++i)
    {
        g_nodeAddrs.push_back(interfaces.GetAddress(i));
    }
    g_bsAddr = interfaces.GetAddress(NUM_NODES);

    if (scheme == "gebpdcm_ltpm")
    {
        for (uint32_t i = 0; i < NUM_NODES; ++i)
        {
            Ptr<Ipv4L3Protocol> ipv4l3 = mobileNodes.Get(i)->GetObject<Ipv4L3Protocol>();
            ipv4l3->TraceConnectWithoutContext("Tx", MakeBoundCallback(&TxTraceCallback, i));
            ipv4l3->TraceConnectWithoutContext("Drop", MakeBoundCallback(&DropTraceCallback, i));
        }
    }

    uint16_t port = 9;
    Ipv4Address bsAddr = g_bsAddr;

    PacketSinkHelper sinkHelper("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), port));
    ApplicationContainer sinkApp = sinkHelper.Install(bsNode.Get(0));
    sinkApp.Start(Seconds(0.0));
    sinkApp.Stop(Seconds(simTime + 1.0));

    OnOffHelper onoff("ns3::UdpSocketFactory", InetSocketAddress(bsAddr, port));
    onoff.SetAttribute("PacketSize", UintegerValue(packetSize));
    onoff.SetAttribute("DataRate", StringValue("12kbps"));
    onoff.SetAttribute("OnTime", StringValue("ns3::ConstantRandomVariable[Constant=1]"));
    onoff.SetAttribute("OffTime", StringValue("ns3::ConstantRandomVariable[Constant=0]"));

    Ptr<UniformRandomVariable> jitter = CreateObject<UniformRandomVariable>();
    jitter->SetAttribute("Min", DoubleValue(0.0));
    jitter->SetAttribute("Max", DoubleValue(5.0));

    ApplicationContainer senderApps;
    for (uint32_t i = 0; i < mobileNodes.GetN(); ++i)
    {
        ApplicationContainer app = onoff.Install(mobileNodes.Get(i));
        double start = jitter->GetValue();
        app.Start(Seconds(start));
        app.Stop(Seconds(simTime));
        senderApps.Add(app);
    }

    FlowMonitorHelper flowmonHelper;
    Ptr<FlowMonitor> monitor = flowmonHelper.InstallAll();

    if (g_scheme != "baseline")
    {
        Simulator::Schedule(Seconds(1.0), &Tick, 0);
    }

    Simulator::Stop(Seconds(simTime + 1.0));
    Simulator::Run();

    Ptr<Ipv4FlowClassifier> classifier =
        DynamicCast<Ipv4FlowClassifier>(flowmonHelper.GetClassifier());
    FlowMonitor::FlowStatsContainer stats = monitor->GetFlowStats();

    uint64_t txPackets = 0, rxPackets = 0;
    double totalDelaySeconds = 0.0;
    uint64_t rxBytes = 0;

    for (const auto& kv : stats)
    {
        txPackets += kv.second.txPackets;
        rxPackets += kv.second.rxPackets;
        rxBytes += kv.second.rxBytes;
        totalDelaySeconds += kv.second.delaySum.GetSeconds();
    }

    double delayMs = (rxPackets > 0) ? (totalDelaySeconds / rxPackets) * 1000.0 : 0.0;
    double throughputMbps = (rxBytes * 8.0) / simTime / 1e6;
    double pdr = (txPackets > 0) ? static_cast<double>(rxPackets) / txPackets : 0.0;
    double dropRatio = (txPackets > 0) ? static_cast<double>(txPackets - rxPackets) / txPackets : 0.0;
    double energyMj = txPackets * TX_ENERGY_PER_HOP + rxPackets * RX_ENERGY_PER_HOP;

    {
        const std::string bar(56, '=');
        std::cout << "\n" << bar << "\n";
        std::cout << "  " << std::left << std::setw(14) << "Scheme" << ": " << scheme << "\n";
        std::cout << "  " << std::left << std::setw(14) << "Speed" << ": " << speed << " m/s\n";
        std::cout << std::string(56, '-') << "\n";
        std::cout << std::fixed << std::setprecision(3);
        std::cout << "  " << std::left << std::setw(14) << "Delay" << ": " << std::right << std::setw(10) << delayMs << " ms\n";
        std::cout << "  " << std::left << std::setw(14) << "Throughput" << ": " << std::right << std::setw(10) << throughputMbps << " Mbps\n";
        std::cout << std::setprecision(4);
        std::cout << "  " << std::left << std::setw(14) << "PDR" << ": " << std::right << std::setw(10) << pdr << "\n";
        std::cout << "  " << std::left << std::setw(14) << "Drop ratio" << ": " << std::right << std::setw(10) << dropRatio << "\n";
        std::cout << std::setprecision(1);
        std::cout << "  " << std::left << std::setw(14) << "Energy" << ": " << std::right << std::setw(10) << energyMj << " mJ\n";
        std::cout << "  " << std::left << std::setw(14) << "Migrations" << ": " << std::right << std::setw(10) << g_migrations << "\n";
        std::cout << bar << "\n" << std::endl;
    }

    Simulator::Destroy();

    // topology.csv is identical for every scheme/speed run (fixed RNG seed, same
    // reasoning as gebpdcm-baseline.cc) -- any run overwriting it is fine, and this
    // means the full sweep is self-contained and doesn't depend on gebpdcm-baseline.cc
    // having been run first to produce it.
    std::ofstream topoFile(outDir + "topology.csv");
    topoFile << "node_id,x,y\n";
    for (uint32_t i = 0; i < NUM_NODES; ++i)
    {
        topoFile << i << "," << initialPositions[i].x << "," << initialPositions[i].y << "\n";
    }
    topoFile.close();

    std::ofstream metricsFile(outDir + "ns3_" + scheme + "_speed" + std::to_string(static_cast<int>(speed)) + ".csv");
    metricsFile << "metric,value\n";
    metricsFile << "delay_ms," << delayMs << "\n";
    metricsFile << "throughput_mbps," << throughputMbps << "\n";
    metricsFile << "pdr," << pdr << "\n";
    metricsFile << "packet_drop_ratio," << dropRatio << "\n";
    metricsFile << "energy_mj," << energyMj << "\n";
    metricsFile.close();

    return 0;
}
