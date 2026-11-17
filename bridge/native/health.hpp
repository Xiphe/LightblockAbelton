// SPDX-License-Identifier: ISC
#pragma once
#include <array>
#include <cstdint>
#include <optional>
#include <string>

namespace health {
enum class State : uint8_t { connecting=0, bridgeUnavailable=1, liveUnavailable=2, noGroups=3, failure=4, ready=5 };
enum class Program : uint8_t { main, metronome, status, patch };
inline Program modeTarget(Program current,bool longPress,bool patchEnabled) {
    if(current==Program::status) return current;
    if(!patchEnabled) return longPress?current:(current==Program::metronome?Program::main:Program::metronome);
    if(longPress) return current==Program::metronome?Program::patch:Program::metronome;
    return current==Program::main?Program::patch:Program::main;
}
enum Reason : uint8_t { none=0, waitingForLive=1, noRemoteGroups=2, hostFailure=3, starting=4,
    deviceDisconnected=5, deviceBusy=6, programLoadFailed=7, deviceRejected=8, staleFeedback=9, protocolMismatch=10 };
struct Frame { uint8_t sequence=0; State state=State::connecting; uint8_t reason=none, flags=0; };
// Helper-only channel16 lane. A partial/replayed unit cannot update readiness.
struct Framer {
    std::array<uint8_t,5> values{};
    int next=0, last=-1;
    void reset() { next=0; last=-1; }
    std::optional<Frame> message(bool controller,int channel,int cc,int value) {
        if(!controller || channel!=16) { next=0; return {}; }
        return push(cc,value);
    }
    std::optional<Frame> push(int cc,int value) {
        if(cc<114 || cc>118 || value<0 || value>127) { next=0; return {}; }
        if(cc==114) { values[0]=uint8_t(value); next=115; return {}; }
        if(cc!=next) { next=0; return {}; }
        values[size_t(cc-114)]=uint8_t(value); ++next;
        if(cc!=118) return {};
        next=0;
        if(values[0]!=values[4] || values[1]>5 || values[2]>protocolMismatch || values[3]>3 || int(values[0])==last) return {};
        // Bridge-link failure is local, not a statement Live can make.
        if(values[1]==uint8_t(State::bridgeUnavailable)) return {};
        last=values[0]; return Frame{values[0],State(values[1]),values[2],values[3]};
    }
};
struct Receipt { Frame frame; double at=0; };
// Host liveness is independent of pad program upload/readiness. The MIDI callback
// keeps the latest complete receipt even while the main thread or SDK is busy.
// Callers synchronize access; taking a receipt never refreshes its timestamp.
struct Inbox {
    Framer framer;
    std::optional<Receipt> pending;
    void reset() { framer.reset(); pending.reset(); }
    bool message(bool controller,int channel,int cc,int value,double at) {
        if(auto frame=framer.message(controller,channel,cc,value)) {
            pending=Receipt{*frame,at}; return true;
        }
        return false;
    }
    std::optional<Receipt> take() { auto result=pending; pending.reset(); return result; }
};
struct Monitor {
    std::optional<Frame> observed;
    double at=0;
    void reset() { observed.reset(); at=0; }
    void update(Frame frame,double now) { observed=frame; at=now; }
    bool fresh(double now) const { return observed && now>=at && now-at<3000; }
    bool busy() const { return observed && (observed->flags&1)!=0; }
    State state(double now) const { return fresh(now)?observed->state:State::liveUnavailable; }
    uint8_t reason(double now) const { return fresh(now)?observed->reason:waitingForLive; }
    bool usable(double now) const { return fresh(now) && observed->state==State::ready; }
    // A lost heartbeat cannot erase the last known active recording view.
    bool needsStatus(Program program,double now) const {
        return program!=Program::status && !busy() && !usable(now);
    }
};
enum class Transition { none, main, status };
inline Transition transition(Program current,bool loaded,bool hasStatus,uint16_t held,bool permissionPending,
                             const Monitor& monitor,double now,double changedAt,bool allocationProtects=false,bool allocationExpired=false) {
    if(!hasStatus || !loaded || held!=0 || permissionPending) return Transition::none;
    if(allocationExpired) return current==Program::status?Transition::none:Transition::status;
    if(current==Program::status) return monitor.usable(now)?Transition::main:Transition::none;
    if(allocationProtects) return Transition::none;
    return monitor.needsStatus(current,now) && now-changedAt>=250?Transition::status:Transition::none;
}
inline const char* programName(Program p) { return p==Program::main?"main":p==Program::metronome?"metronome":p==Program::patch?"patch":"status"; }
inline const char* stateName(State s) {
    switch(s) {
        case State::connecting:return "connecting";
        case State::bridgeUnavailable:return "bridge_unavailable";
        case State::liveUnavailable:return "live_unavailable";
        case State::noGroups:return "no_remote_groups";
        case State::failure:return "failure";
        case State::ready:return "ready";
    } return "failure";
}
struct Explanation { std::string title,body; };
inline Explanation explanation(State state,uint8_t reason,bool audioPerformance=false) {
    if(reason==deviceBusy) return {"Lightpad is in use", "Close BLOCKS Code, Dashboard or another Lightpad uploader, then retry the bridge."};
    if(reason==programLoadFailed) return {"Controller could not load", "Retry Lightpad Bridge. If this continues, repair the controller installation. Existing recordings in Live are not changed by loading the pad program."};
    if(reason==deviceRejected) return {"Lightpad connection is not supported", "Use the configured Lightpad as a standalone block. Check the bridge device selection."};
    if(reason==protocolMismatch) return {"Controller versions do not match", "Install the matching Live controller script and Lightpad Bridge, then reconnect."};
    switch(state) {
        case State::connecting:return {"Connecting Lightpad", "Connecting or restoring the controller. Please wait; Live's playback is unchanged."};
        case State::bridgeUnavailable:return {"Lightpad disconnected", "Check USB or reconnect Bluetooth MIDI, and make sure Lightpad Bridge is running."};
        case State::liveUnavailable:return {"Waiting for Live",
            std::string("Open Ableton Live. In Link, Tempo & MIDI settings, select ")
            + (audioPerformance?"LightpadAudioInputs":"LightpadController")
            + " and set both Input and Output to Lightpad Bridge."};
        case State::noGroups:
            if(audioPerformance) return {"Audio Set is not ready", "Open the configured, prepared Audio Set. It needs unique [R1] through [R8] Group Track prefixes and a rightmost [Remote] subgroup in each recording group. A different Set must first be configured for this controller."};
            return {"No remote groups in this Set", "Add [R1] through [R8] to a Group Track name to assign a Lightpad tile. Existing recordings are preserved."};
        case State::failure:return {"Controller needs attention", "Live could not initialize the controller. Check the bridge's status details for the detected cause; no repeated recording command is sent automatically."};
        case State::ready:return {"Lightpad is ready", "The controller is connected to Live and usable remote groups were found."};
    } return {"Controller needs attention", "Check the bridge's status details."};
}
inline bool diagnosticTap(bool controller,int channel,int cc,int value,Program program,bool loaded) {
    return controller && loaded && program==Program::status && channel==16 && cc==107 && value>=0 && value<=5;
}
struct NotificationGate {
    double last=-10000;
    bool accept(double now) { if(now-last<750) return false; last=now; return true; }
};
inline std::array<int32_t,3> event(State state,uint8_t reason) {
    return {int32_t(0x53540000u | uint32_t(state) | (uint32_t(reason)<<8)),0,0xa8 | (3<<8) | (8<<10)};
}
}
