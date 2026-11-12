// SPDX-License-Identifier: ISC
#include "transport_policy.hpp"
#include <roli_blocks_basics/protocol/roli_HostPacketBuilder.h>
#include "protocol.hpp"
#include <cassert>
#include <iostream>
#undef assert
#define assert(expr) do { if(!(expr)) { std::cerr << "check failed: " #expr << " line " << __LINE__ << std::endl; std::abort(); } } while(false)
std::vector<uint8_t> topology(std::string serial,int count=1,int connections=0) {
    std::vector<uint8_t> data(28,0); int cursor=0;
    auto put=[&](uint32_t v,int n){for(int i=0;i<n;++i,++cursor) if(v&(1u<<i)) data[cursor/7]|=1u<<(cursor%7);};
    put(0,32); put(1,7); put(1,8); put(count,7); put(connections,8); for(char c:serial) put(uint8_t(c),7); put(30,7); put(31,5);put(0,1); data.resize((cursor+6)/7);
    std::vector<uint8_t> packet{0xf0,0,0x21,0x10,0x77,0x5e}; packet.insert(packet.end(),data.begin(),data.end()); packet.push_back(bp::calculatePacketChecksum(data.data(),uint32_t(data.size()))); packet.push_back(0xf7); return packet;
}
int main() {
    std::string serial="LPB0000000000000"; assert(serial.size()==16); Identity id(serial);
    bp::HostPacketBuilder<200> request;request.writePacketSysexHeaderBytes(0);request.deviceControlMessage(bp::requestTopologyMessage);request.writePacketSysexFooter();assert(id.allow(request.getData(),request.size()));
    bp::HostPacketBuilder<200> begin;begin.writePacketSysexHeaderBytes(30);begin.deviceControlMessage(bp::beginAPIMode);begin.writePacketSysexFooter();assert(!id.allow(begin.getData(),begin.size()));
    auto packet=topology(serial); assert(id.observe(juce::MidiMessage(packet.data(),int(packet.size())))); assert(id.target==30 && id.ack==-1); assert(id.allow(begin.getData(),begin.size()));
    const uint8_t ack[]{0xf0,0,0x21,0x10,0x77,0x5e,0,0,0,0,0x20,0x10,6,0x23,0xf7}; assert(id.observe(juce::MidiMessage(ack,sizeof(ack))) && id.ack==30);
    bp::HostPacketBuilder<200> save;save.writePacketSysexHeaderBytes(30);save.deviceControlMessage(bp::saveProgramAsDefault);save.writePacketSysexFooter();assert(!id.allow(save.getData(),save.size()));
    Identity wrong(serial);auto other=topology("LPB1111111111111");assert(!wrong.observe(juce::MidiMessage(other.data(),int(other.size()))));assert(!wrong.allow(begin.getData(),begin.size()));
    Identity chain(serial);auto dna=topology(serial,1,1);assert(!chain.observe(juce::MidiMessage(dna.data(),int(dna.size()))));
{bridge::Unit unit={{176,33,127},{176,40,0},{176,41,1},{176,42,2},{176,43,3},{176,44,4},{176,45,5},{176,46,6},{176,47,7},{176,48,8},{176,49,9},{176,50,10},{176,51,11},{176,52,12},{176,53,13},{176,54,14},{176,55,127}};bp::HostPacketBuilder<200> packet;packet.writePacketSysexHeaderBytes(30);
for(const auto& words:bridge::events(unit)) assert(packet.addProgramEventMessage(words.data()));packet.writePacketSysexFooter();
assert(id.allow(packet.getData(),packet.size()));assert(juce::String::toHexString(packet.getData(),packet.size(),0).toStdString()=="f0002110771e0300020818402001030750027807600042040a16306860010054407e01003bf7");}
{bridge::Unit unit={{191,34,127},{191,40,0},{191,41,1},{191,42,2},{191,43,3},{191,44,4},{191,45,5},{191,46,6},{191,47,7},{191,48,8},{191,49,9},{191,50,10},{191,51,11},{191,52,12},{191,53,13},{191,54,14},{191,55,15},{191,56,16},{191,57,17},{191,58,127}};bp::HostPacketBuilder<200> packet;packet.writePacketSysexHeaderBytes(30);
for(const auto& words:bridge::events(unit)) assert(packet.addProgramEventMessage(words.data()));packet.writePacketSysexFooter();
assert(id.allow(packet.getData(),packet.size()));assert(juce::String::toHexString(packet.getData(),packet.size(),0).toStdString()=="f0002110771e0300020818402001030750067907600042040a16306860610354617e01180011020000000000000035683f000030f7");}
{bridge::Unit unit={{191,35,127},{191,40,0},{191,41,1},{191,42,2},{191,43,3},{191,44,4},{191,45,5},{191,46,6},{191,47,127}};bp::HostPacketBuilder<200> packet;packet.writePacketSysexHeaderBytes(30);
for(const auto& words:bridge::events(unit)) assert(packet.addProgramEventMessage(words.data()));packet.writePacketSysexFooter();
assert(id.allow(packet.getData(),packet.size()));assert(juce::String::toHexString(packet.getData(),packet.size(),0).toStdString()=="f0002110771e03000208184020010300500a7907006cf7");}
std::cout<<"Topology/ACK/no-default-write and Python-matched A8 SDK packets passed\n";
}
