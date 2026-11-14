// SPDX-License-Identifier: ISC
#pragma once
#include <array>
#include <cstdint>
#include <stdexcept>
#include <vector>
namespace bridge {
using CC = std::array<uint8_t, 3>;
using Unit = std::vector<CC>;
inline bool allowed(uint8_t status) { return (status >= 0xb0 && status <= 0xbb) || status == 0xbf; }
// Protocol19 A9. Only complete validated logical units can become Native events.
struct Events {
    std::array<std::array<int32_t,3>,3> values{};
    size_t count=0;
    bool empty() const { return count==0; }
    const std::array<int32_t,3>* begin() const { return values.data(); }
    const std::array<int32_t,3>* end() const { return values.data()+count; }
};
inline Events events(const Unit& unit) {
    if(unit.empty() || unit.size()>20) throw std::runtime_error("invalid word unit");
    for(auto m:unit) if(!allowed(m[0]) || m[1]>127 || m[2]>127) throw std::runtime_error("invalid word CC");
    auto first=unit.front();
    if(first[0]==0xbf && first[1]>=110 && first[1]<=113) {
        for(auto m:unit) if(m[0]!=0xbf || m[1]<110 || m[1]>113) throw std::runtime_error("mixed metadata");
        if(first[1]==110) {
            if(unit.size()<2 || first[2]!=19 || unit[1]!=CC{0xbf,111,1}) throw std::runtime_error("invalid word handshake");
            for(size_t i=2;i<unit.size();++i) if(unit[i][1]<112) throw std::runtime_error("duplicate handshake");
            Events result; result.values[0]={274,0,0xa9 | (3<<8) | (12<<11)}; result.count=1; return result;
        }
        for(auto m:unit) if(m[1]<112) throw std::runtime_error("incomplete handshake");
        return {};
    }
    int kind=first[1]==33 ? 0 : first[1]==34 ? 1 : first[1]==35 ? 2 : first[1]==36 ? 4 : -1;
    int group=first[0]==0xbf ? 12 : first[0]-0xb0;
    if(kind<0 || (kind==0 && group>=12) || (kind==1 && group>=8 && group!=12) || ((kind==2 || kind==4) && group!=12)) throw std::runtime_error("invalid word record");
    int length=kind==0 ? 15 : kind==2 ? 8 : 18;
    if(unit.size()!=size_t(length+2) || unit.back()[2]!=first[2]) throw std::runtime_error("invalid word length/revision");
    for(size_t i=1;i<unit.size();++i) if(unit[i][0]!=first[0] || unit[i][1]!=39+i) throw std::runtime_error("invalid word order");
    Events result;
    for(int offset=0;offset<length;offset+=8) {
        uint32_t a=0,b=0;
        for(int i=0;i<8 && offset+i<length;++i) {
            uint32_t value=unit[1+offset+i][2];
            if(i<4) a|=value<<(i*8); else b|=value<<((i-4)*8);
        }
        int32_t header=0xa9 | (kind<<8) | (group<<11) | ((offset/8)<<15) | (first[2]<<17);
        result.values[result.count++]={int32_t(a),int32_t(b),header};
    }
    return result;
}
// One independent assembler per channel. Never emit an incomplete frame.
struct Framer {
    struct Partial { Unit data; int body=0; uint8_t revision=0; };
    std::array<Partial,16> channels;
    Unit metadata;
    void reset() { for(auto& p:channels) p=Partial{}; metadata.clear(); }
    bool push(CC m, Unit& ready) {
        ready.clear(); if(!allowed(m[0]) || m[1]>127 || m[2]>127) throw std::runtime_error("unsupported host MIDI");
        auto ch=m[0]&15; auto& p=channels[ch];
        if(ch==15 && m[1]>=110 && m[1]<=113) {
            if(m[1]==110) { if(m[2]!=19) throw std::runtime_error("unsupported host version"); reset(); m[2]=19; metadata={m}; return false; }
            p=Partial{};
            if(!metadata.empty()) { if(m[1]!=110+metadata.size()) throw std::runtime_error("malformed handshake"); metadata.push_back(m); if(m[1]==112) { ready=std::move(metadata); metadata.clear(); return true; } return false; }
            if(m[1]==111) throw std::runtime_error("readiness without handshake");
            ready={m}; return true;
        }
        int body=m[1]==33 && ch<12 ? 15 : m[1]==34 && (ch<8 || ch==15) ? 18 : m[1]==35 && ch==15 ? 8 : m[1]==36 && ch==15 ? 18 : 0;
        if(body) { if(!p.data.empty()) throw std::runtime_error("interrupted frame"); p.body=body; p.revision=m[2]; p.data={m}; return false; }
        if(p.data.empty() || m[1]!=39+p.data.size()) throw std::runtime_error("malformed frame order");
        p.data.push_back(m);
        if(int(p.data.size())==p.body+2) { if(m[2]!=p.revision) throw std::runtime_error("frame revision mismatch"); ready=std::move(p.data); p=Partial{}; return true; }
        return false;
    }
};
}
