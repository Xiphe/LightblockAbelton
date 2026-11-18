// Bounded semantic transport. Native objects and gesture state stay elsewhere.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <optional>

namespace performance {
constexpr int rejected = 16383;
constexpr int noSequence = 127;
struct Intent { int seq=0, key=0, value=0, generation=0; };
struct Ack { int generation=0, seq=0, key=0, value=0; };
struct Observation { int generation=0, seq=0, kind=0; uint32_t a=0,b=0; };
struct Dispatch { int generation=0, seq=0, key=0, value=0, deviceSeq=0; uint64_t serial=0; };
struct Feedback {
    int kind=0; uint32_t a=0,b=0; int epoch=1,revision=0;
    int header() const { return 0xf2 | (kind<<8) | (epoch<<11) | (revision<<18); }
};
inline bool byte(int n) { return n>=0 && n<128; }
inline int patchGroup(int key) { return key>=48&&key<56?key-48:key>=116&&key<124?key-116:-1; }
constexpr int parameterCapacity=152;
inline bool constantParameter(int key) { return key>=128 && key<152; }
inline int wireChannel(int key) { return constantParameter(key)?2:1; }
inline int wireKey(int key) { return key&127; }
inline int feedbackKey(uint32_t a) { return int(a&127)+int((a>>14)&1)*128; }
inline int parameterFact(int key) { return constantParameter(key)?key-80:key==57?88:key; }
inline int factParameter(uint32_t a) { return a<48?int(a):a<72?int(a)+80:a>=80&&a<88?int(a)-32:a==88?57:int(a); }
inline int mixerFactIndex(int group) { return group<8?50+group:345+group-8; }
inline bool parameter(int key) { return (key>=0 && key<56) || key==57 || key==114 || key==115 || (key>=116&&key<124) || constantParameter(key); }
inline bool sourceName(int kind,uint32_t a) { return kind==6&&a>=64&&a<264; }
inline bool valid(Intent v) {
    if(!byte(v.seq)||v.key<0||v.key>=parameterCapacity||v.value<0||v.value>16383) return false;
    if(v.key<48 || constantParameter(v.key)) return v.value<=127;
    if(v.key<56) return v.value<=8191;
    if(v.key==56) return v.value>0 && v.value<=4095;
    if(v.key==57) return v.value<8;
    if(v.key==58) return v.value>0 && v.value<rejected;
    if(v.key>=64 && v.key<=103) {
        int action=(v.key-64)%5;
        return (action!=0 && action!=1 && action!=4) || v.value<128;
    }
    if(v.key>=104 && v.key<=112) return v.value==0;
    if(v.key==113) return v.value==0 || v.value==127;
    if(v.key==114) return v.value>=1 && v.value<=99;
    if(v.key==115) return v.value==1||v.value==2||v.value==4||v.value==8||v.value==16;
    if(v.key>=116 && v.key<124) return v.value<=4095;
    return false;
}
struct BankFrameState { int stage=0,generation=0,seq=0,key=0,value=0; };
struct IntentFrame {
    std::array<BankFrameState,2> banks{};
    void reset() { banks={}; }
    std::optional<Intent> push(int channel,int cc,int data) {
        if(channel<1 || channel>2) return {};
        auto& f=banks[size_t(channel-1)];
        if(!byte(data)) { f.stage=0;return {}; }
        if(cc==69) { f.generation=data;f.stage=data?1:0;return {}; }
        if(f.stage==1 && cc==70) { f.seq=data;f.stage=2;return {}; }
        if(f.stage==2 && cc==71) { f.key=data+(channel==2?128:0);f.stage=3;return {}; }
        if(f.stage==3 && cc==72) { f.value=data;f.stage=4;return {}; }
        if(f.stage==4 && cc==73) { f.value|=data<<7;f.stage=5;return {}; }
        if(f.stage==5 && cc==74 && data==f.seq) {
            f.stage=0;Intent result{f.seq,f.key,f.value,f.generation};
            if(valid(result)) return result;
            return {};
        }
        f.stage=0;return {};
    }
};
struct AckFrame {
    std::array<BankFrameState,2> banks{};
    void reset() { banks={}; }
    std::optional<Ack> push(int channel,int cc,int data) {
        if(channel<1 || channel>2) return {};
        auto& f=banks[size_t(channel-1)];
        if(!byte(data)) { f.stage=0;return {}; }
        if(cc==69) { f.generation=data;f.stage=data?1:0;return {}; }
        if(f.stage==1 && cc==75) { f.seq=data;f.stage=2;return {}; }
        if(f.stage==2 && cc==76) { f.key=data+(channel==2?128:0);f.stage=3;return {}; }
        if(f.stage==3 && cc==77) { f.value=data;f.stage=4;return {}; }
        if(f.stage==4 && cc==78) { f.value|=data<<7;f.stage=5;return {}; }
        if(f.stage==5 && cc==79 && f.seq==data) {
            f.stage=0;
            if(channel==1 || constantParameter(f.key)) return Ack{f.generation,f.seq,f.key,f.value};
            return {};
        }
        f.stage=0;return {};
    }
};
struct ObservationFrame {
    int stage=0,generation=0,seq=0,kind=0; uint32_t a=0,b=0;
    void reset() { stage=0; }
    std::optional<Observation> push(int channel,int cc,int data) {
        if(channel!=1) return {};
        if(!byte(data)) { reset();return {}; }
        if(cc==89) { generation=data;stage=data?1:0;return {}; }
        if(stage==1 && cc==90) { seq=data;stage=2;return {}; }
        if(stage==2 && cc==91 && data<=7) { kind=data;a=b=0;stage=3;return {}; }
        if(stage>=3 && stage<=10 && cc==89+stage) {
            if(stage<7) a|=uint32_t(data)<<(7*(stage-3));
            else b|=uint32_t(data)<<(7*(stage-7));
            ++stage;return {};
        }
        if(stage==11 && cc==100 && data==seq) { reset();return Observation{generation,seq,kind,a,b}; }
        reset();return {};
    }
};

class Reconciler {
    struct Parameter { bool known=false, dirty=false; int desired=0,confirmed=0,deviceSeq=0; uint64_t serial=0; double received=0; };
    struct Command { Intent intent; uint64_t serial=0; double received=0; };
    struct Flight { Dispatch wire; double at=0; };
    struct Fact { bool known=false,dirty=false; int kind=0; uint32_t a=0,b=0; };
    struct Seen { bool known=false; Intent intent; bool completed=false; int actual=0; };
    std::array<Parameter,parameterCapacity> parameters{};
    static constexpr size_t allocationFact=320;
    std::array<Fact,349> facts{};
    // Availability is a presentation gate, separate from confirmed Native
    // group state. A fresh program receives all6 field facts before it can act.
    std::array<uint8_t,12> initializedFields{};
    std::array<Seen,128> seen{};
    std::array<Command,16> commands{};
    std::array<Feedback,64> terminals{};
    // Parameter ACKs can supersede earlier ACKs of the same control. Discrete
    // admitted results retain their own reserved terminal slot until sent.
    std::array<std::optional<Feedback>,parameterCapacity> parameterReplies{};
    std::optional<Feedback> overflowReply;
    // One extra slot retains the synchronous SDK send currently being retried.
    // Concurrent Native replies cannot consume this reserved transport slot.
    std::optional<Feedback> retryTerminal;
    std::optional<Feedback> issuedFeedback;
    uint64_t issuedParameterSerial=0;
    std::optional<Flight> flight;
    // One internal grant is separate from ordinary input capacity. Before it
    // is dispatched all already-admitted commands and fader values must drain.
    std::optional<Command> allocationGrant;
    int allocationTicket=0;
    bool allocationGate=false;
    double allocationAt=0;
    static constexpr double allocationLeaseMs=10000;
    std::array<bool,128> quarantined{};
    int commandHead=0,commandCount=0,terminalHead=0,terminalCount=0;
    int lastDeviceSeq=-1,nextDispatch=0,observationRevision=-1,modelRevision=0;
    int parameterCursor=0,factCursor=0,replyCursor=0;
    uint64_t intentSerial=0;
    bool seedPending=false;
    static constexpr double timeoutMs=2000;

    static bool same(Intent a,Intent b) { return a.seq==b.seq && a.key==b.key && a.value==b.value && a.generation==b.generation; }
    static int factIndex(int kind,uint32_t a) {
        if(kind==1) {
            if(a<48) return int(a);
            if(a<72) return 321+int(a)-48;
            if(a>=80 && a<88) return 78+int(a)-80;
            if(a==88) return 119;
            if(a==114||a==115) return int(a)-66;
        }
        if(kind==3 && a<12) return mixerFactIndex(int(a));
        if(kind==4 && (a&15)<=8 && (a>>4)<(1u<<21)) return 58+int(a&15);
        if(kind==5 && a<(1u<<26) && ((a>>10)&7)<=5) return 67+int(a&7);
        if(kind==6 && a<=2) return 75+int(a);
        if(kind==6 && a==3) return int(allocationFact);
        if(kind==6 && a>=8 && a<16) return 86+int(a)-8;
        if(kind==6 && a>=24 && a<49) return 94+int(a)-24;
        if(sourceName(kind,a)) return 120+int(a)-64;
        return -1;
    }
    bool setFact(int kind,uint32_t a,uint32_t b,bool force=false) {
        int index=factIndex(kind,a);
        if(index<0 || b>0xfffffff) return false;
        auto& value=facts[size_t(index)];
        bool changed=!value.known||value.kind!=kind||value.a!=a||value.b!=b;
        value.kind=kind;value.a=a;value.b=b;value.known=true;
        value.dirty=value.dirty||changed||force;
        return true;
    }
    Feedback reply(Intent v,int actual,int kind=2) const {
        return Feedback{kind,uint32_t(wireKey(v.key)|(v.seq<<7)|(constantParameter(v.key)?1<<14:0)),uint32_t(actual),epoch,0};
    }
    void terminal(Feedback value) {
        if(terminalCount<64) { terminals[size_t((terminalHead+terminalCount)%64)]=value;++terminalCount; }
        else { overflowReply=value;++feedbackBackpressure; }
    }
    void complete(Dispatch wire,int actual,bool timedOut=false) {
        Intent device{wire.deviceSeq,wire.key,wire.value,wire.generation};
        auto& history=seen[size_t(wire.deviceSeq)];
        if(history.known && same(history.intent,device)) { history.completed=true;history.actual=actual; }
        if(parameter(wire.key)) {
            auto& p=parameters[size_t(wire.key)];
            if(actual!=rejected) {
                p.confirmed=actual;p.known=true;
                // An old fact can update confirmed state but cannot become
                // acknowledgement of a superseding local intent.
                const int group=patchGroup(wire.key);
                if(group>=0) {
                    const auto& full=facts[size_t(78+group)];
                    const int shift=wire.key>=116?13:0;
                    const uint32_t mask=(wire.key>=116?4095u:8191u)<<shift;
                    setFact(1,uint32_t(80+group),((full.known?full.b:0u)&~mask)|(uint32_t(actual)<<shift));
                } else setFact(1,uint32_t(parameterFact(wire.key)),uint32_t(actual));
            }
            if(p.serial==wire.serial) parameterReplies[size_t(wire.key)]=reply(device,actual);
        } else terminal(reply(device,actual));
        if(actual!=rejected && wire.key==56) {
            for(int group=0;group<12;++group) {
                auto& f=facts[size_t(mixerFactIndex(group))];
                if(f.known) setFact(3,f.a,(f.b&~(1u<<23))|(((uint32_t(actual)>>group)&1u)<<23));
            }
        }
        // Meter/click also have a packed global representation. Patch an
        // existing confirmed snapshot from the Native ACK before sending it;
        // a dirty pre-command global must not rewind the just-confirmed value.
        if(actual!=rejected && wire.key>=113 && wire.key<=115) {
            auto& meter=facts[76];
            if(meter.known) {
                uint32_t value=meter.b;
                if(wire.key==113) value=(value&~(1u<<15))|(actual?1u<<15:0u);
                if(wire.key==114) value=(value&~127u)|uint32_t(actual);
                if(wire.key==115) value=(value&~(127u<<7))|(uint32_t(actual)<<7);
                setFact(6,1,value);
            }
        }
        if(actual!=rejected && wire.key==57 && actual<8) {
            auto& meter=facts[76];
            if(meter.known) setFact(6,1,(meter.b&0xffffffu)|(uint32_t(actual+1)<<24));
        }
        if(timedOut) ++timedOutCommands;
    }
    void expire(double at) {
        if(!flight || at-flight->at<timeoutMs) return;
        auto old=*flight;flight.reset();
        // A late ACK for this wire ID must never match a subsequent operation.
        // Timed-out IDs stay quarantined until a fresh generation reset.
        quarantined[size_t(old.wire.seq)]=true;
        complete(old.wire,rejected,true);
    }
    std::optional<int> wireSequence() {
        for(int n=0;n<128;++n) {
            int seq=nextDispatch;nextDispatch=(nextDispatch+1)&127;
            if(!quarantined[size_t(seq)]) return seq;
        }
        liveReady=false;return {};
    }
    void fieldDelivered(uint32_t key,bool delivered) {
        if(key>=72) return;
        const size_t group=size_t(key/6);
        const uint8_t bit=uint8_t(1u<<(key%6));
        const uint8_t before=initializedFields[group];
        initializedFields[group]=delivered?uint8_t(before|bit):uint8_t(before&~bit);
        if((before==63)!=(initializedFields[group]==63) && facts[size_t(mixerFactIndex(int(group)))].known)
            facts[size_t(mixerFactIndex(int(group)))].dirty=true;
    }
    Feedback stamp(Feedback value) {
        if(value.kind==1) fieldDelivered(value.a,true);
        if(value.kind==3 && value.a<12 && initializedFields[size_t(value.a)]!=63)
            value.b&=~(1u<<21);
        modelRevision=(modelRevision+1)&8191;
        value.epoch=epoch;value.revision=modelRevision;
        issuedFeedback=value;
        const int key=feedbackKey(value.a);
        issuedParameterSerial=value.kind==2&&parameter(key)?parameters[size_t(key)].serial:0;
        return value;
    }
    int reservedTerminals() const { return terminalCount+commandCount+(allocationGrant?1:0)+(retryTerminal?1:0)+(flight&&!parameter(flight->wire.key)?1:0); }
    bool reject(Intent value) {
        auto& history=seen[size_t(value.seq)];history.completed=true;history.actual=rejected;
        ++rejectedIntents;terminal(reply(value,rejected,7));return false;
    }
    void cancelAllocationGrant() {
        if(!allocationGrant) return;
        reject(allocationGrant->intent);allocationGrant.reset();
    }
    void expireAllocation(double at) {
        if(!allocationExpired(at)) return;
        allocationGate=true;cancelAllocationGrant();
    }
public:
    int epoch=1;
    bool liveReady=false;
    uint64_t receivedIntents=0,dispatched=0,acknowledged=0,ignoredReplies=0;
    uint64_t duplicateIntents=0,rejectedIntents=0,timedOutCommands=0,feedbackBackpressure=0,feedbackRetries=0;
    double lastLiveAt=0,lastIntentAt=0,lastDispatchAt=0,lastAckAt=0;
    double maxIntentToDispatchMs=0,maxNativeAckMs=0;

    void reset(int generation,double at) {
        if(generation<1||generation>127) return;
        // Keep historical desired/confirmed facts, but issue no old-generation
        // Native writes. The device resends its latest safe desired parameter
        // after receiving the fresh seed. Discrete intent is never replayed.
        for(auto& p:parameters) p.dirty=false;
        flight.reset();allocationGrant.reset();allocationTicket=0;allocationGate=false;allocationAt=0;
        facts[allocationFact]={};
        commandHead=commandCount=terminalHead=terminalCount=0;
        parameterReplies={};overflowReply.reset();retryTerminal.reset();issuedFeedback.reset();seen={};quarantined={};
        epoch=generation;liveReady=false;lastLiveAt=at;
        lastDeviceSeq=-1;nextDispatch=0;observationRevision=-1;modelRevision=0;
        reseed(at);
    }
    void reseed(double /*at*/) {
        seedPending=true;
        initializedFields={};
        for(auto& f:facts) if(f.known) f.dirty=true;
    }
    void handshake(int generation,double at=0) {
        if(generation==epoch) { liveReady=true;if(at>0) lastLiveAt=at; }
        else ++ignoredReplies;
    }
    bool intent(Intent value,double at=0) {
        expireAllocation(at);
        if(value.generation!=epoch || !valid(value)) { ++rejectedIntents;return false; }
        auto& history=seen[size_t(value.seq)];
        int distance=lastDeviceSeq<0?1:(value.seq-lastDeviceSeq)&127;
        if(lastDeviceSeq>=0 && (distance==0||distance>=64)) {
            if(history.known && same(history.intent,value)) {
                ++duplicateIntents;
                if(history.completed) {
                    if(history.actual==rejected) terminal(reply(value,rejected,7));
                    else if(parameter(value.key)) {
                        const auto& p=parameters[size_t(value.key)];
                        if(p.deviceSeq==value.seq && p.desired==value.value)
                            parameterReplies[size_t(value.key)]=reply(value,history.actual);
                    } else if(reservedTerminals()<64) terminal(reply(value,history.actual));
                }
                return true;
            }
            ++rejectedIntents;overflowReply=reply(value,rejected,7);return false;
        }
        lastDeviceSeq=value.seq;
        history={true,value,false,0};
        ++receivedIntents;lastIntentAt=at;
        uint64_t serial=++intentSerial;
        if(value.key==58) {
            if(!allocationTicket || value.value!=allocationTicket || allocationGate || allocationExpired(at)) return reject(value);
            allocationGate=true;allocationGrant=Command{value,serial,at};return true;
        }
        if(allocationGate) return reject(value);
        if(parameter(value.key)) {
            auto& p=parameters[size_t(value.key)];
            p.desired=value.value;p.deviceSeq=value.seq;p.serial=serial;p.received=at;p.dirty=true;
            parameterReplies[size_t(value.key)].reset();
            return true;
        }
        if(commandCount==16 || reservedTerminals()>=48) {
            ++rejectedIntents;history.completed=true;history.actual=rejected;
            // Rejection is separate from admitted terminal results. Under a
            // hostile flood only the latest unadmitted rejection is retained;
            // intent() also returns false so the transport can report pressure.
            overflowReply=reply(value,rejected);return false;
        }
        commands[size_t((commandHead+commandCount)%16)]={value,serial,at};++commandCount;
        return true;
    }
    void receiveAck(Ack value,double at=0) {
        if(value.generation!=epoch || !flight || value.seq!=flight->wire.seq
           || value.key!=flight->wire.key || value.value<0 || value.value>16383
           || (value.key==56 && value.value!=rejected && value.value>4095)
           || (value.value!=rejected && parameter(value.key) && !valid(Intent{0,value.key,value.value}))) {
            ++ignoredReplies;return;
        }
        auto old=*flight;flight.reset();
        ++acknowledged;lastAckAt=lastLiveAt=at;
        maxNativeAckMs=std::max(maxNativeAckMs,at-old.at);
        complete(old.wire,value.value);
    }
    void receiveObservation(Observation value,double at=0) {
        // A new helper process can reuse the same7-bit generation. Old queued
        // facts must not advance revision tracking before the explicit Native
        // session-reset handshake has completed.
        if(!liveReady || value.generation!=epoch || value.seq<0||value.seq>127
           || factIndex(value.kind,value.a)<0 || value.b>0xfffffff
           || (value.kind==6 && value.a==3 && value.b>=uint32_t(rejected))
           || (value.kind==1 && !(value.a<72?value.b<=128:value.a>=80&&value.a<88?value.b<=0x1ffffffu:value.a==88?value.b<=8:
                parameter(factParameter(value.a))&&valid(Intent{0,factParameter(value.a),int(value.b)})))) {
            ++ignoredReplies;return;
        }
        int distance=observationRevision<0?1:(value.seq-observationRevision)&127;
        if(observationRevision>=0 && (distance==0||distance>=64)) { ++ignoredReplies;return; }
        observationRevision=value.seq;lastLiveAt=at;
        if(value.kind==6 && value.a==3) {
            if(int(value.b)!=allocationTicket) {
                cancelAllocationGrant();allocationTicket=int(value.b);allocationGate=false;allocationAt=at;
            } else if(allocationTicket && allocationGate && at>=allocationAt && !allocationExpired(at)) {
                // Progress from the same bounded bundle extends liveness only.
                // Never unlock, grant again, or revive an expired operation.
                allocationAt=at;
            }
        }
        if(value.kind==1) {
            auto& p=parameters[size_t(factParameter(value.a))];p.confirmed=int(value.b);p.known=true;
            if(value.a>=80&&value.a<88) {
                p.confirmed=int(value.b&8191u);
                auto& high=parameters[size_t(116+value.a-80)];high.confirmed=int(value.b>>13);high.known=true;
            }
        }
        setFact(value.kind,value.a,value.b);
    }
    std::optional<Dispatch> takeDispatch(double at=0) {
        expire(at);expireAllocation(at);
        if(!liveReady || flight) return {};
        int key=-1,deviceSeq=0,value=0;uint64_t serial=0;double received=at;
        if(commandCount) {
            auto cmd=commands[size_t(commandHead)];
            key=cmd.intent.key;deviceSeq=cmd.intent.seq;value=cmd.intent.value;serial=cmd.serial;received=cmd.received;
            // A just-selected source must reach Live before Begin for that
            // group, even though replaceable state normally follows commands.
            if(key>=64 && key<=103) {
                const int patchKey=48+(key-64)/5;
                auto& patch=parameters[size_t(patchKey)];
                const int prerequisite=patch.dirty?patchKey:116+(key-64)/5;
                auto& bank=parameters[size_t(prerequisite)];
                if(bank.dirty) {key=prerequisite;deviceSeq=bank.deviceSeq;value=bank.desired;serial=bank.serial;received=bank.received;}
            }
        } else {
            for(int n=0;n<parameterCapacity;++n) {
                int candidate=(parameterCursor+n)%parameterCapacity;
                auto& p=parameters[size_t(candidate)];
                if(parameter(candidate) && p.dirty) {key=candidate;deviceSeq=p.deviceSeq;value=p.desired;serial=p.serial;received=p.received;break;}
            }
        }
        if(key<0 && allocationGrant) {
            key=58;deviceSeq=allocationGrant->intent.seq;value=allocationGrant->intent.value;
            serial=allocationGrant->serial;received=allocationGrant->received;
        }
        if(key<0) return {};
        auto seq=wireSequence();if(!seq) return {};
        if(parameter(key)) {parameters[size_t(key)].dirty=false;parameterCursor=(key+1)%parameterCapacity;}
        else if(key==58) allocationGrant.reset();
        else {commandHead=(commandHead+1)%16;--commandCount;}
        Dispatch result{epoch,*seq,key,value,deviceSeq,serial};flight=Flight{result,at};
        ++dispatched;lastDispatchAt=at;maxIntentToDispatchMs=std::max(maxIntentToDispatchMs,at-received);
        return result;
    }
    bool feedbackFailed(const Feedback& value) {
        // The SDK send is synchronous. Reject an old/unrelated failure report,
        // including one crossing a generation reset, rather than resurrecting it.
        if(!issuedFeedback || value.epoch!=epoch || value.header()!=issuedFeedback->header()
           || value.kind!=issuedFeedback->kind || value.a!=issuedFeedback->a || value.b!=issuedFeedback->b)
            return false;
        ++feedbackRetries;
        if(value.kind==0) { seedPending=true;return true; }
        if(value.kind==2 || value.kind==7) {
            const int key=feedbackKey(value.a),seq=int((value.a>>7)&127);
            if(value.kind==2 && parameter(key)) {
                const auto& p=parameters[size_t(key)];
                if(p.serial!=issuedParameterSerial || p.deviceSeq!=seq) return false;
                parameterReplies[size_t(key)]=value;
            } else retryTerminal=value;
            return true;
        }
        if(value.kind==1) fieldDelivered(value.a,false);
        const int index=factIndex(value.kind,value.a);
        if(index<0 || !facts[size_t(index)].known) return false;
        // A newer cached clock/parameter fact may have replaced the failed one.
        // Requeue the current context, never the obsolete consumed payload.
        facts[size_t(index)].dirty=true;
        return true;
    }
    std::optional<Feedback> takeFeedback(double at=0,bool patchNames=false) {
        expire(at);expireAllocation(at);
        if(seedPending) { seedPending=false;return stamp(Feedback{0,uint32_t(lastDeviceSeq<0?127:lastDeviceSeq),0,epoch,0}); }
        // A known busy request/clear must reach the current program before
        // ordinary model traffic. Its receipt is separate from this send.
        auto& allocation=facts[allocationFact];
        if(allocation.dirty) { allocation.dirty=false;return stamp(Feedback{6,3,allocation.b,epoch,0}); }
        if(retryTerminal) {auto value=*retryTerminal;retryTerminal.reset();return stamp(value);}
        if(terminalCount) {auto value=terminals[size_t(terminalHead)];terminalHead=(terminalHead+1)%64;--terminalCount;return stamp(value);}
        if(overflowReply) { auto value=*overflowReply;overflowReply.reset();return stamp(value); }
        for(int n=0;n<parameterCapacity;++n) {
            int index=(replyCursor+n)%parameterCapacity;
            if(parameterReplies[size_t(index)]) {auto value=*parameterReplies[size_t(index)];parameterReplies[size_t(index)].reset();replyCursor=(index+1)%parameterCapacity;return stamp(value);}
        }
        // Musical facts always precede bulk labels, including while the patch
        // view is open. Label cache size cannot turn clocks into a name FIFO.
        for(int names=0;names<=(patchNames?1:0);++names) {
            for(size_t n=0;n<facts.size();++n) {
                auto index=(size_t(factCursor)+n)%facts.size();auto& f=facts[index];
                if(f.dirty && sourceName(f.kind,f.a)==bool(names)) {
                    f.dirty=false;factCursor=int((index+1)%facts.size());return stamp(Feedback{f.kind,f.a,f.b,epoch,0});
                }
            }
        }
        return {};
    }
    bool pending() const {
        if(flight || commandCount || allocationGrant) return true;
        return std::any_of(parameters.begin(),parameters.end(),[](const Parameter& p){return p.dirty;});
    }
    bool allocationRequested() const { return allocationTicket!=0; }
    bool allocationExpired(double at) const { return allocationTicket && at>=allocationAt && at-allocationAt>=allocationLeaseMs; }
    bool allocationProtectsHealth(double at) const { return allocationRequested() && !allocationExpired(at); }
    bool allocationAdmissionClosed(double at) const { return allocationGate || allocationExpired(at); }
    int inflightSeq() const { return flight?flight->wire.seq:-1; }
    int queuedDiscrete() const { return commandCount; }
    int terminalResults() const { return terminalCount+(retryTerminal?1:0); }
    int lastDeviceSequence() const { return lastDeviceSeq<0?127:lastDeviceSeq; }
    int confirmed(int key) const { return key>=0&&key<parameterCapacity&&parameters[size_t(key)].known?parameters[size_t(key)].confirmed:-1; }
    int desired(int key) const { return key>=0&&key<parameterCapacity?parameters[size_t(key)].desired:-1; }
};
}
