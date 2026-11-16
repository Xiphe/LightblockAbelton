#include "health.hpp"
#include <cassert>
int main() {
    health::Framer f;
    auto frame=[&](int seq,int state,int reason,int flags,int end=-1) {
        assert(!f.push(114,seq));assert(!f.push(115,state));assert(!f.push(116,reason));assert(!f.push(117,flags));return f.push(118,end<0?seq:end);
    };
    auto good=frame(0,5,0,2);assert(good && good->state==health::State::ready);
    assert(!frame(0,5,0,2)); // replay cannot keep stale readiness alive
    assert(!frame(1,5,11,0));assert(!frame(1,6,0,0));assert(!frame(1,1,0,0));assert(!frame(1,5,0,4));assert(!frame(1,5,0,0,2));
    assert(!f.push(114,2));assert(!f.push(116,0));assert(!f.push(117,0));assert(!f.push(118,2));
    assert(!f.push(114,2));assert(!f.push(115,5));assert(!f.push(-1,-1));
    assert(!f.push(116,0));assert(!f.push(117,0));assert(!f.push(118,2));
    // Same ingress seam as the live callback: notes/other channels interrupt.
    assert(!f.message(true,16,114,2));assert(!f.message(true,16,115,5));
    assert(!f.message(false,16,116,0));
    assert(!f.message(true,16,116,0));assert(!f.message(true,16,117,0));assert(!f.message(true,16,118,2));
    assert(!f.message(true,16,114,2));assert(!f.message(true,16,115,5));
    assert(!f.message(true,1,10,0));assert(!f.message(true,16,116,0));
    good=frame(3,5,0,1);assert(good);
    health::Monitor m;m.update(*good,100);
    assert(m.usable(100));assert(!m.usable(3100));assert(m.busy());
    assert(!m.needsStatus(health::Program::main,5000)); // preserve ongoing record screen across missing host
    m.update(*frame(4,3,2,0),5100);assert(m.needsStatus(health::Program::main,5100));
    assert(!m.needsStatus(health::Program::status,5100));
    m.reset();assert(!m.busy());assert(m.state(6000)==health::State::liveUnavailable);
    using health::Program;using health::Transition;
    assert(health::modeTarget(Program::main,false,true)==Program::patch);
    assert(health::modeTarget(Program::main,true,true)==Program::metronome);
    assert(health::modeTarget(Program::patch,false,true)==Program::main);
    assert(health::modeTarget(Program::patch,true,true)==Program::metronome);
    assert(health::modeTarget(Program::metronome,false,true)==Program::main);
    assert(health::modeTarget(Program::metronome,true,true)==Program::patch);
    assert(health::modeTarget(Program::status,true,true)==Program::status);
    assert(health::modeTarget(Program::main,false,false)==Program::metronome);
    assert(health::modeTarget(Program::main,true,false)==Program::main);
    m.update(*frame(5,5,0,0),6000);
    assert(health::transition(Program::status,true,true,0,false,m,6000,5900)==Transition::main);
    assert(health::transition(Program::status,true,true,1,false,m,6000,5900)==Transition::none);
    assert(health::transition(Program::main,true,true,0,false,m,6000,5900)==Transition::none);
    assert(health::transition(Program::main,true,true,0,false,m,9000,6000)==Transition::status);
    assert(health::transition(Program::main,true,false,0,false,m,9000,6000)==Transition::none);
    m.update(*frame(6,3,2,0),9000);
    assert(health::transition(Program::main,true,true,0,false,m,9249,9000)==Transition::none);
    assert(health::transition(Program::main,true,true,0,false,m,9250,9000)==Transition::status);
    assert(health::transition(Program::main,true,true,0,true,m,9250,9000)==Transition::none);
    m.update(*frame(7,5,0,1),9300);
    assert(health::transition(Program::main,true,true,0,false,m,20000,9300)==Transition::none);
    // Replay Bluetooth's >3s main-program upload. Host health continues on its
    // own MIDI lane while device-loaded is false, including a blocked main loop.
    health::Inbox inbox;
    auto heartbeat=[&](int sequence,int state,int flags,double at) {
        assert(!inbox.message(true,16,114,sequence,at));
        assert(!inbox.message(true,16,115,state,at));
        assert(!inbox.message(true,16,116,0,at));
        assert(!inbox.message(true,16,117,flags,at));
        assert(inbox.message(true,16,118,sequence,at));
    };
    m.reset();
    for(int i=0;i<8;++i) heartbeat(i,5,0,500.0*i);
    auto receipt=inbox.take();assert(receipt && receipt->at==3500);
    assert(!inbox.take());m.update(receipt->frame,receipt->at);
    assert(health::transition(Program::main,true,true,0,false,m,3415+500,0)==Transition::none);
    assert(m.usable(3915));
    // The same upload cannot hide a real host loss by dating an old receipt
    // at drain time, or extending the existing 3s deadline.
    inbox.reset();heartbeat(0,5,0,4000);receipt=inbox.take();
    m.update(receipt->frame,receipt->at);
    assert(health::transition(Program::main,true,true,0,false,m,7415,4000)==Transition::status);
    // Explicit host failure still takes effect, and old recording-busy state
    // continues to protect an active take across a missing heartbeat.
    heartbeat(1,4,0,7500);receipt=inbox.take();m.update(receipt->frame,receipt->at);
    assert(health::transition(Program::main,true,true,0,false,m,7750,7500)==Transition::status);
    heartbeat(2,5,1,8000);receipt=inbox.take();m.update(receipt->frame,receipt->at);
    assert(health::transition(Program::main,true,true,0,false,m,20000,8000)==Transition::none);
    // Ingress retains atomicity/replay checks and a session reset discards old pending state.
    assert(!inbox.message(true,16,114,3,21000));
    assert(!inbox.message(true,16,115,5,21000));
    assert(!inbox.message(true,1,1,0,21000));
    assert(!inbox.message(true,16,116,0,21000));
    assert(!inbox.message(true,16,117,0,21000));
    assert(!inbox.message(true,16,118,3,21000));
    heartbeat(4,5,0,22000);inbox.reset();assert(!inbox.take());
    assert(health::diagnosticTap(true,16,107,3,Program::status,true));
    assert(!health::diagnosticTap(false,16,107,3,Program::status,true));
    assert(!health::diagnosticTap(true,16,107,3,Program::main,true));
    assert(!health::diagnosticTap(true,16,107,3,Program::status,false));
    health::NotificationGate g;assert(g.accept(0));assert(!g.accept(749));assert(g.accept(750));
    auto words=health::event(health::State::noGroups,2);assert(words[0]==int32_t(0x53540203));assert(words[1]==0);assert(words[2]==0x23a8);
    // Setup explanations must name the installed matched adapter, not the legacy script.
    auto legacy=health::explanation(health::State::liveUnavailable,health::waitingForLive);
    auto audio=health::explanation(health::State::liveUnavailable,health::waitingForLive,true);
    assert(legacy.body.find("LightpadController")!=std::string::npos);
    assert(legacy.body.find("LightpadAudioInputs")==std::string::npos);
    assert(audio.body.find("LightpadAudioInputs")!=std::string::npos);
    assert(audio.body.find("LightpadController")==std::string::npos);
    assert(audio.body.find("Lightpad Bridge")!=std::string::npos);
    // Audio readiness also depends on an explicitly configured, prepared Set.
    auto setup=health::explanation(health::State::noGroups,health::noRemoteGroups,true);
    assert(setup.body.find("configured")!=std::string::npos);
    assert(setup.body.find("[Remote]")!=std::string::npos);
    assert(health::explanation(health::State::noGroups,0).body.find("[R1]")!=std::string::npos);
    assert(health::explanation(health::State::failure,health::programLoadFailed).body.find("repair")!=std::string::npos);
}
