// SPDX-License-Identifier: ISC
#pragma once
#include <roli_blocks_basics/roli_blocks_basics.h>
#include <roli_blocks_basics/protocol/roli_BitPackingUtilities.h>
#include <roli_blocks_basics/protocol/roli_BlocksProtocolDefinitions.h>
#include <atomic>
namespace bp = roli::BlocksProtocol;
// Check only pure read-only topology responses before the SDK may enter API mode.
struct Identity {
    std::atomic<int> target{-1}, ack{-1};
    std::atomic<bool> rejected{false};
    std::string serial;
    explicit Identity(std::string s):serial(std::move(s)) {}
    bool observe(const juce::MidiMessage& message) {
        auto* p=message.getRawData(); int n=message.getRawDataSize();
        if(n<9 || memcmp(p,bp::roliSysexHeader,5) || p[n-1]!=0xf7 || !(p[5]&64) || !bp::Packed7BitArrayReader::checksumIsOK(p+6,n-7)) return false;
        if(p[5]>127 || std::any_of(p+6,p+n-1,[](uint8_t b){return b>127;})) return false;
        if(rejected.load()) return false;
        bp::Packed7BitArrayReader bits(p+6,n-8);
        if(bits.getRemainingBits()<39) return false;
        bits.readBits(32); auto kind=bits.readBits(7);
        if(kind==1) {
            if(bits.getRemainingBits()<23+125) return false;
            int version=int(bits.readBits(8)), count=int(bits.readBits(7)), links=int(bits.readBits(8));
            std::string found; for(int i=0;i<16;++i) found.push_back(char(bits.readBits(7)));
            int index=int(bits.readBits(7));
            if(version>1 || count!=1 || links!=0 || found!=serial || index>62) { rejected=true; target=-1; ack=-1; return false; }
            if(target.exchange(index)!=index) ack=-1;
        }
        if(kind==2 && target.load()==(p[5]&63) && n==15) ack=p[5]&63;
        return target.load()>=0;
    }
    bool allow(const void* bytes,size_t size) const {
        const auto* p=static_cast<const uint8_t*>(bytes);
        const uint8_t serialRequest[]{0xf0,0,0x21,0x10,0x78,0x3f,0xf7};
        if(size==sizeof(serialRequest) && !memcmp(p,serialRequest,size)) return true;
        const uint8_t versionPrefix[]{0xf0,0,0x21,0x10,0x47,3};
        if(size==8 && !memcmp(p,versionPrefix,6) && (p[6]==0 || p[6]==1 || p[6]==3) && p[7]==0xf7) return true;
        if(size<9 || memcmp(p,bp::roliSysexHeader,5) || p[size-1]!=0xf7 || p[5]>62 || !bp::Packed7BitArrayReader::checksumIsOK(p+6,uint32_t(size-7))) return false;
        bp::Packed7BitArrayReader bits(p+6,int(size-8));
        if(bits.getRemainingBits()<7) return false;
        int kind=int(bits.readBits(7));
        if(kind==1) { if(bits.getRemainingBits()<9) return false; auto cmd=bits.readBits(9); if(cmd==1) return true; if(cmd>3) return false; }
        else if(kind!=2 && kind!=3) return false;
        return target.load()>=0 && p[5]==target.load() && !rejected.load();
    }
};
