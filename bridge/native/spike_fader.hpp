// PRIVATE throwaway R1-volume spike. No Native objects or gesture state here.
#pragma once
#include <cstdint>
#include <optional>

namespace spike_fader {
struct Value { int seq=0, value=0; };
struct IntentFrame {
    int stage=0, seq=0, value=0;
    void reset() { stage=0; }
    std::optional<Value> push(int channel,int cc,int data) {
        if(channel!=1) return {};
        if(cc==70) { seq=data; stage=1; return {}; }
        if(stage==1 && cc==71) { value=data; stage=2; return {}; }
        if(stage==2 && cc==72 && data==seq) { stage=0; return Value{seq,value}; }
        stage=0; return {};
    }
};
struct LiveValue { int kind=0, generation=0, seq=0, value=0; };
struct LiveFrame {
    int stage=0, generation=0, kind=0, seq=0, value=0;
    void reset() { stage=0; }
    std::optional<LiveValue> push(int channel,int cc,int data) {
        if(channel!=1) return {};
        if(cc==69) { generation=data; stage=1; return {}; }
        if(stage==1 && (cc==73 || cc==76)) { kind=cc==73?2:1; seq=data; stage=2; return {}; }
        if(stage==2 && cc==(kind==2?74:77)) { value=data; stage=3; return {}; }
        if(stage==3 && cc==(kind==2?75:78) && data==seq) { stage=0; return LiveValue{kind,generation,seq,value}; }
        stage=0; return {};
    }
};
struct Dispatch { int generation=0, seq=0, value=0; uint64_t serial=0; };
struct Feedback {
    int kind=0, seq=0, value=0, epoch=1, revision=0;
    uint64_t intentSerial=0;
    double due=0;
    int header() const { return 0xf1|(kind<<8)|(epoch<<10)|(revision<<17); }
};
class Reconciler {
    struct Flight { Dispatch wire; int deviceSeq=0; double dispatchedAt=0; };
    std::optional<Flight> flight;
    std::optional<Feedback> feedback;
    int nextDispatch=0, observationRevision=-1, modelRevision=0;
    int lastDeviceSeq=-1, lastDeviceValue=-1;
    uint64_t intentSerial=0, completedSerial=0;
    void model(int kind,int seq,int value,double due) {
        modelRevision=(modelRevision+1)&16383;
        feedback=Feedback{kind,seq,value,epoch,modelRevision,intentSerial,due};
    }
public:
    int epoch=1, desiredSeq=0, desiredValue=64, confirmedValue=64;
    bool liveReady=false;
    uint64_t receivedIntents=0, dispatched=0, acknowledged=0, ignoredReplies=0;
    double lastIntentAt=0, lastDispatchAt=0, lastAckAt=0;
    double maxIntentToDispatchMs=0, maxNativeAckMs=0;
    void reset(int generation,double now) {
        epoch=generation; liveReady=false; flight.reset(); feedback.reset();
        nextDispatch=0; observationRevision=-1; modelRevision=0;
        lastDeviceSeq=lastDeviceValue=-1; intentSerial=completedSerial=0;
        desiredValue=confirmedValue; desiredSeq=0;
        model(0,0,confirmedValue,now);
    }
    void handshake(int generation) { if(generation==epoch) liveReady=true; }
    void intent(Value value,double at=0) {
        if(value.seq==lastDeviceSeq && value.value==lastDeviceValue) return;
        lastDeviceSeq=desiredSeq=value.seq; lastDeviceValue=desiredValue=value.value;
        ++intentSerial; ++receivedIntents; lastIntentAt=at;
        // A deferred confirmation belongs to an older intent. Never relabel it.
        if(feedback && feedback->kind!=0) feedback.reset();
    }
    std::optional<Dispatch> takeDispatch(double at=0) {
        if(!liveReady || flight || intentSerial==completedSerial) return {};
        Dispatch result{epoch,nextDispatch,desiredValue,intentSerial};
        nextDispatch=(nextDispatch+1)&127;
        flight=Flight{result,desiredSeq,at}; ++dispatched; lastDispatchAt=at;
        if(lastIntentAt>0 && at-lastIntentAt>maxIntentToDispatchMs) maxIntentToDispatchMs=at-lastIntentAt;
        return result;
    }
    void receive(LiveValue value,double now,double delay) {
        if(value.generation!=epoch) { ++ignoredReplies; return; }
        if(value.kind==2) {
            if(!flight || flight->wire.seq!=value.seq) { ++ignoredReplies; return; }
            auto finished=*flight; flight.reset();
            confirmedValue=value.value; completedSerial=finished.wire.serial; ++acknowledged;
            lastAckAt=now;
            if(finished.dispatchedAt>0 && now-finished.dispatchedAt>maxNativeAckMs)
                maxNativeAckMs=now-finished.dispatchedAt;
            if(completedSerial==intentSerial)
                model(2,finished.deviceSeq,value.value,now+delay);
            return;
        }
        if(value.kind!=1) return;
        if(observationRevision>=0) {
            int difference=(value.seq-observationRevision)&127;
            if(difference==0 || difference>=64) { ++ignoredReplies; return; }
        }
        observationRevision=value.seq;
        bool changed=value.value!=confirmedValue;
        confirmedValue=value.value;
        if(changed && !flight && intentSerial==completedSerial) {
            // Coalesced state includes both acknowledgement progress and the
            // latest actual value. A newer observation must not erase an ACK
            // which the device has not received yet.
            int kind=feedback && feedback->kind==2?2:1;
            model(kind,desiredSeq,value.value,now+delay);
        }
    }
    std::optional<Feedback> takeFeedback(double now) {
        if(!feedback || now<feedback->due) return {};
        auto result=*feedback; feedback.reset(); return result;
    }
    bool pending() const { return intentSerial!=completedSerial || bool(flight); }
    int inflightSeq() const { return flight?flight->wire.seq:-1; }
    uint64_t serial() const { return intentSerial; }
};
}
