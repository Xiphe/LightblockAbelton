// SPDX-License-Identifier: ISC
#pragma once
#include <array>
#include <cstdint>
#include <optional>

namespace modal {
enum class Click { single, doubleClick };

// The host owns recognition. A second press reserves a double-click before
// its release, so holding the second press cannot accidentally fire a single.
struct Clicks {
    bool down=false, waiting=false, second=false;
    double pressedAt=0, releasedAt=0;
    double windowMs=300;
    void reset() { down=false; waiting=false; second=false; }
    void press(double now) {
        if(down) return;
        down=true; pressedAt=now;
        second=waiting && now-releasedAt<=windowMs;
        if(second) waiting=false;
    }
    std::optional<Click> release(double now) {
        if(!down) return {};
        down=false;
        if(now-pressedAt<20 || now-pressedAt>=600) { reset(); return {}; }
        if(second) { reset(); return Click::doubleClick; }
        waiting=true; releasedAt=now; return {};
    }
    std::optional<Click> poll(double now) {
        if(waiting && !down && now-releasedAt>=windowMs) {
            waiting=false; return Click::single;
        }
        return {};
    }
};

// Observe only complete raw touch units. Live remains the authority that
// validates and executes gestures. This observer gates swaps and Close only.
struct Touches {
    struct Partial { std::array<uint8_t,7> values{}; int next=0; };
    std::array<Partial,16> partial{};
    std::array<bool,16> closeStart{};
    std::array<int,16> lastSeq{}, roles{};
    std::array<bool,16> seen{};
    uint16_t held=0;
    void reset() { partial={}; closeStart={}; seen={}; roles={}; held=0; }
    bool push(int channel,int cc,int value,bool metronome) {
        if(channel<0 || channel>15) return false;
        auto& p=partial[size_t(channel)];
        if(cc<20 || cc>26 || value<0 || value>127) { p.next=0; return false; }
        if(cc==20) { p.next=21; p.values[0]=uint8_t(value); return false; }
        if(p.next!=cc) { p.next=0; return false; }
        p.values[size_t(cc-20)]=uint8_t(value); ++p.next;
        if(cc!=26) return false;
        p.next=0;
        auto& v=p.values;
        int kind=v[1]&3, role=(v[1]>>2)&7;
        int view=(v[3]>>4)&3, group=v[5]>>4;
        bool moved=(v[1]&32)!=0, extra=(v[3]&64)!=0;
        if(v[0]!=v[6] || kind==0 || role==7
            || (moved && kind!=3) || (role==1 && kind==1 && extra)) return false;
        int x=v[2]+((v[3]&15)<<7), y=v[4]+((v[5]&15)<<7);
        if(x>1919 || y>1919) return false;
        auto bit=uint16_t(1u<<channel);
        auto index=size_t(channel);
        if(seen[index] && lastSeq[index]==v[0]) return false;
        if(kind!=1 && ((held&bit)==0 || (roles[index]!=role && !(roles[index]>=1 && roles[index]<=4 && role==5)))) return false;
        seen[index]=true; lastSeq[index]=v[0]; roles[index]=role;
        bool opened=(v[1]&64)!=0, overview=view==0;
        bool inside=x<640 && y>=1664;
        if(kind==1) {
            held|=bit;
            closeStart[index]=metronome && role==1 && opened && overview && group==0 && inside;
        }
        bool close=kind==3 && closeStart[index] && role==1 && !opened && overview && group==0 && inside;
        if(kind==3) { held&=uint16_t(~bit); closeStart[index]=false; }
        return close;
    }
};
}
