/*
 * gebpdcm-baseline.cc -- baseline AODV MANET scenario (PROJECT_SPEC.md section 7.1)
 *
 * Adapted (much simplified) from examples/routing/manet-routing-compare.cc.
 * Produces plain-AODV baseline QoS numbers for scheme 1 ("baseline" -- no
 * congestion control at all) over the paper's Table 1 parameter set.
 *
 * ENVIRONMENT BUG FOUND AND FIXED (see report Section 7.3): this ns-3.42
 * build's WiFi PHY registers HR/DSSS support during Configure80211b() but
 * then logs "Unsupported modulation received (HR/DSSS), consider as
 * noise" for every single HR/DSSS frame at receive time, silently
 * discarding it instead of decoding it -- confirmed via a debug-profile
 * rebuild with NS_LOG enabled on YansWifiPhy/WifiPhy/InterferenceHelper.
 * HR/DSSS covers 802.11b's 5.5/11 Mbps rates; plain DSSS (1/2 Mbps) is
 * unaffected. DataMode/ControlMode/NonUnicastMode are therefore all set to
 * DsssRate2Mbps (plain DSSS) below, not DsssRate11Mbps -- confirmed to
 * restore real packet delivery (a short test run went from 0% PDR, the
 * result for every prior configuration including ns-3's own unmodified
 * manet-routing-compare.cc reference example, to ~51% PDR in the first 3
 * seconds of a cold start, before AODV routes have even converged).
 *
 * Usage (run from the ns-3.42 root, this file lives in scratch/):
 *   ./ns3 run gebpdcm-baseline -- --speed=10 --packetSize=1024
 *
 * Writes two CSVs to $HOME/networks_project/data/ (an absolute path,
 * because ./ns3 run's working directory is the NS-3 root, not the
 * project folder):
 *   topology.csv            -- node_id,x,y      (the 100 mobile nodes' initial positions)
 *   baseline_speed<N>.csv   -- metric,value      (delay_ms, throughput_mbps, pdr, packet_drop_ratio, energy_mj)
 */

#include <cstdlib>
#include <fstream>
#include <map>
#include <string>

#include "ns3/aodv-module.h"
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/wifi-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("GEbPDCMBaseline");

// Per-hop radio energy costs (mJ), kept identical to metrics.py's Python-side
// model so the two legs would be directly comparable in the report, if the
// FlowMonitor numbers this file produces were usable (see file header).
static const double TX_ENERGY_PER_HOP = 0.05;
static const double RX_ENERGY_PER_HOP = 0.03;

int
main(int argc, char* argv[])
{
    // --- Table 1 parameters (PROJECT_SPEC.md section 5) ---
    double areaWidth = 1000.0;
    double areaHeight = 1000.0;
    uint32_t numNodes = 100;
    double bsX = 500.0;
    double bsY = 450.0;
    double txRange = 300.0;
    double simTime = 100.0;

    // CommandLine-tunable sweep parameters
    double speed = 10.0;        // m/s
    uint32_t packetSize = 1024; // bytes

    CommandLine cmd(__FILE__);
    cmd.AddValue("speed", "Node speed in m/s (RandomWaypoint)", speed);
    cmd.AddValue("packetSize", "OnOff application packet size in bytes", packetSize);
    cmd.AddValue("simTime", "Simulation duration in seconds", simTime);
    cmd.Parse(argc, argv);

    // Reference pattern (examples/routing/manet-routing-compare.cc): set
    // NonUnicastMode via Config::SetDefault, applied globally before any
    // node/device is created, so broadcast frames (AODV floods) don't tie
    // up the channel at the slow default rate.
    //
    // ROOT CAUSE FOUND (debug-profile NS_LOG trace, see report Section 7.3):
    // this ns-3.42 build's WiFi PHY registers HR/DSSS support during
    // Configure80211b() but then logs "Unsupported modulation received
    // (HR/DSSS), consider as noise" for every single HR/DSSS frame at
    // receive time -- treating it as noise instead of attempting to decode
    // it. HR/DSSS covers 802.11b's 5.5/11 Mbps rates; plain DSSS (1/2 Mbps)
    // is unaffected. DsssRate11Mbps (used below for DataMode/ControlMode
    // too) is HR/DSSS, so every unicast frame we sent was silently
    // undecodable while broadcasts at the old default (DsssRate1Mbps,
    // plain DSSS) worked. Using DsssRate2Mbps everywhere keeps traffic on
    // the working DSSS path.
    Config::SetDefault("ns3::WifiRemoteStationManager::NonUnicastMode", StringValue("DsssRate2Mbps"));

    // Fixed RNG seed so topology.csv is identical across every speed run,
    // matching the seeded topology the Python control layer generates.
    RngSeedManager::SetSeed(42);
    RngSeedManager::SetRun(1);

    NodeContainer mobileNodes;
    mobileNodes.Create(numNodes);
    NodeContainer bsNode;
    bsNode.Create(1);

    NodeContainer allNodes;
    allNodes.Add(mobileNodes);
    allNodes.Add(bsNode);

    // --- Mobility FIRST (before Wifi/Internet/AODV setup) ---
    // Position allocation draws from ns-3's shared global RNG stream.
    // Running mobility first, before any other subsystem gets a chance to
    // consume that stream, makes the topology depend only on
    // RngSeedManager's seed/run, not on unrelated code elsewhere in main().
    ObjectFactory posFactory;
    posFactory.SetTypeId("ns3::RandomRectanglePositionAllocator");
    posFactory.Set("X", StringValue("ns3::UniformRandomVariable[Min=0.0|Max=" + std::to_string(areaWidth) + "]"));
    posFactory.Set("Y", StringValue("ns3::UniformRandomVariable[Min=0.0|Max=" + std::to_string(areaHeight) + "]"));
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

    // Capture initial positions for topology.csv before Run() moves them.
    std::map<uint32_t, Vector> initialPositions;
    for (uint32_t i = 0; i < mobileNodes.GetN(); ++i)
    {
        Ptr<MobilityModel> mm = mobileNodes.Get(i)->GetObject<MobilityModel>();
        initialPositions[i] = mm->GetPosition();
    }

    // --- Wi-Fi ad-hoc PHY/MAC, tx range hard-capped at txRange metres ---
    YansWifiChannelHelper wifiChannel;
    wifiChannel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    wifiChannel.AddPropagationLoss("ns3::FriisPropagationLossModel");
    wifiChannel.AddPropagationLoss("ns3::RangePropagationLossModel", "MaxRange", DoubleValue(txRange));

    YansWifiPhyHelper wifiPhy;
    wifiPhy.SetChannel(wifiChannel.Create());

    WifiMacHelper wifiMac;
    wifiMac.SetType("ns3::AdhocWifiMac");

    // DsssRate2Mbps (plain DSSS), not DsssRate11Mbps (HR/DSSS) -- see the
    // NonUnicastMode comment above for why.
    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211b);
    wifi.SetRemoteStationManager(
        "ns3::ConstantRateWifiManager", "DataMode", StringValue("DsssRate2Mbps"),
        "ControlMode", StringValue("DsssRate2Mbps"));

    NetDeviceContainer devices = wifi.Install(wifiPhy, wifiMac, allNodes);

    // --- AODV routing over all nodes (100 mobile + 1 base station) ---
    AodvHelper aodv;
    InternetStackHelper internet;
    internet.SetRoutingHelper(aodv);
    internet.Install(allNodes);

    Ipv4AddressHelper address;
    address.SetBase("10.1.1.0", "255.255.255.0");
    Ipv4InterfaceContainer interfaces = address.Assign(devices);

    // --- Traffic: every mobile node sends CBR "video" UDP traffic to the BS ---
    uint16_t port = 9;
    Ipv4Address bsAddr = interfaces.GetAddress(numNodes); // BS is the last device installed

    PacketSinkHelper sinkHelper("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), port));
    ApplicationContainer sinkApp = sinkHelper.Install(bsNode.Get(0));
    sinkApp.Start(Seconds(0.0));
    sinkApp.Stop(Seconds(simTime + 1.0));

    // 12kbps/node * 100 nodes = 1.2Mbps aggregate offered load, scaled down
    // from the original 60kbps tuning to match the channel capacity drop
    // from 11Mbps (DsssRate11Mbps, HR/DSSS -- broken in this ns-3 build,
    // see file header) down to 2Mbps (DsssRate2Mbps, the working DSSS
    // rate). Keeps the same offered-load-to-capacity ratio as the original
    // tuning instead of oversubscribing the now-smaller channel 3x over.
    OnOffHelper onoff("ns3::UdpSocketFactory", InetSocketAddress(bsAddr, port));
    onoff.SetAttribute("PacketSize", UintegerValue(packetSize));
    onoff.SetAttribute("DataRate", StringValue("12kbps"));
    onoff.SetAttribute("OnTime", StringValue("ns3::ConstantRandomVariable[Constant=1]"));
    onoff.SetAttribute("OffTime", StringValue("ns3::ConstantRandomVariable[Constant=0]"));

    // Spread app start times over 5s (not 0-1s) so AODV's initial route
    // discovery broadcasts don't all flood the channel simultaneously.
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

    // --- FlowMonitor for delay/throughput/PDR/drop ratio ---
    FlowMonitorHelper flowmonHelper;
    Ptr<FlowMonitor> monitor = flowmonHelper.InstallAll();

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

    Simulator::Destroy();

    // --- Write CSVs to $HOME/networks_project/data/ (absolute path) ---
    const char* home = std::getenv("HOME");
    std::string outDir = std::string(home ? home : ".") + "/networks_project/data/";

    // topology.csv is identical for every speed run (fixed RNG seed), so
    // any run overwriting it is fine. This is the file that actually
    // matters -- see the file header for why baseline_speed<N>.csv below
    // is not used by the rest of the pipeline.
    std::ofstream topoFile(outDir + "topology.csv");
    topoFile << "node_id,x,y\n";
    for (uint32_t i = 0; i < numNodes; ++i)
    {
        topoFile << i << "," << initialPositions[i].x << "," << initialPositions[i].y << "\n";
    }
    topoFile.close();

    std::ofstream metricsFile(outDir + "baseline_speed" + std::to_string(static_cast<int>(speed)) + ".csv");
    metricsFile << "metric,value\n";
    metricsFile << "delay_ms," << delayMs << "\n";
    metricsFile << "throughput_mbps," << throughputMbps << "\n";
    metricsFile << "pdr," << pdr << "\n";
    metricsFile << "packet_drop_ratio," << dropRatio << "\n";
    metricsFile << "energy_mj," << energyMj << "\n";
    metricsFile.close();

    return 0;
}
