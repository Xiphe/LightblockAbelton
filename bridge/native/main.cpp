// SPDX-License-Identifier: ISC
// One temporary-program SDK owner. No persistence or firmware commands.
#include <roli_blocks_basics/roli_blocks_basics.h>
#include <CoreMIDI/CoreMIDI.h>
#include "protocol.hpp"
#include "spike_fader.hpp"
#include "modal.hpp"
#include "short_long.hpp"
#include "health.hpp"
#include "notifications.h"
#include "event_loop.h"
#include "transport_policy.hpp"
#include <roli_blocks_basics/protocol/roli_HostPacketBuilder.h>
// The SDK has three unchecked internal casts. Derive from its exact pinned
// inline connection definition, so all three casts have the correct layout.
#include <roli_blocks_basics/topology/internal/roli_MidiDeviceConnection.cpp>
#include <atomic>
#include <csignal>
#include <deque>
#include <fcntl.h>
#include <iostream>
#include <mutex>
#include <set>
#include <sys/file.h>
#include <unistd.h>
namespace bp = roli::BlocksProtocol;
static volatile sig_atomic_t stopping=0;
static void signalHandler(int) { stopping=1; }
struct Options { std::string serial, source, modalSource, statusSource, status, lock; int trialCycles=0; bool optimisticFaderSpike=false; int spikeAckDelayMs=0; };
struct Singleton {
    int fd=-1;
    explicit Singleton(const std::string& path) { fd=open(path.c_str(),O_CREAT|O_RDWR|O_NOFOLLOW,0600); if(fd<0 || flock(fd,LOCK_EX|LOCK_NB)) throw std::runtime_error("bridge already running or lock unavailable"); }
    ~Singleton() { if(fd>=0) close(fd); }
};
struct Pair { juce::MidiDeviceInfo input,output; bool bluetooth=false; };
static MIDIEndpointRef endpoint(const juce::String& identifier,bool input) {
    auto uid=identifier.fromLastOccurrenceOf(" ",false,false).getIntValue();
    for(ItemCount i=0;i<(input?MIDIGetNumberOfSources():MIDIGetNumberOfDestinations());++i) {
        auto ep=input?MIDIGetSource(i):MIDIGetDestination(i); SInt32 value=0;
        if(MIDIObjectGetIntegerProperty(ep,kMIDIPropertyUniqueID,&value)==noErr && value==uid) return ep;
    } return 0;
}
static MIDIEntityRef entity(MIDIEndpointRef ep) { MIDIEntityRef e=0; if(ep) MIDIEndpointGetEntity(ep,&e); return e; }
static MIDIDeviceRef device(MIDIEndpointRef ep) { MIDIEntityRef entity=0; MIDIDeviceRef d=0; if(ep) MIDIEndpointGetEntity(ep,&entity); if(entity) MIDIEntityGetDevice(entity,&d); return d; }
static bool bluetooth(MIDIDeviceRef d) {
    CFStringRef value=nullptr; bool result=false;
    if(MIDIObjectGetStringProperty(d,kMIDIPropertyDriverOwner,&value)==noErr && value) { result=juce::String::fromCFString(value).containsIgnoreCase("bluetooth"); CFRelease(value); }
    return result;
}
struct Transport : roli::MIDIDeviceConnection {
    Identity identity;
    std::shared_ptr<juce::InterProcessLock> sdkLock;
    std::function<void(Transport*,const juce::MidiMessage&)> raw;
    std::function<void(Transport*)> closed;
    std::function<void()> wake;
    Pair pair;
    uint64_t generation=0;
    Transport(std::string serial,Pair selected):identity(std::move(serial)),pair(std::move(selected)) {}
    ~Transport() override { if(midiInput) midiInput->stop(); midiInput.reset(); midiOutput.reset(); if(closed) closed(this); }
    bool sendMessageToDevice(const void* data,size_t size) override {
        if(!midiOutput || !identity.allow(data,size)) return false;
        midiOutput->sendMessageNow(juce::MidiMessage(data,int(size))); return true;
    }
    void handleIncomingMidiMessage(juce::MidiInput*,const juce::MidiMessage& m) override {
        if(m.isController()) { if(identity.target.load()>=0 && !identity.rejected.load() && raw) raw(this,m); return; }
        if(identity.observe(m)) { roli::MIDIDeviceConnection::handleIncomingMidiMessage(nullptr,m); if(wake) wake(); }
        else if(m.isSysEx() && m.getRawDataSize()>=7) {
            auto* bytes=m.getRawData();
            // SDK read-only legacy serial/version responses go to its listeners.
            if(bytes[1]==0 && bytes[2]==0x21 && bytes[3]==0x10 && (bytes[4]==0x78 || bytes[4]==0x47)) roli::MIDIDeviceConnection::handleIncomingMidiMessage(nullptr,m);
        }
    }
};
struct Detector : roli::PhysicalTopologySource::DeviceDetector {
    std::string serial;
    std::atomic<Transport*> active{nullptr};
    Pair selected;
    std::atomic<uint64_t> activeGeneration{0};
    uint64_t nextGeneration=0;
    std::set<std::string> rejectedPairs;
    bool locked=false;
    std::function<void(Transport*,const juce::MidiMessage&)> raw;
    std::function<void()> wake;
    explicit Detector(std::string s):serial(std::move(s)) {}
    juce::StringArray scanForDevices() override {
        auto ins=juce::MidiInput::getAvailableDevices(); auto outs=juce::MidiOutput::getAvailableDevices();
        std::vector<Pair> pairs;
        for(auto& in:ins) for(auto& out:outs) {
            auto di=device(endpoint(in.identifier,true)), dout=device(endpoint(out.identifier,false));
            if(di && di==dout && entity(endpoint(in.identifier,true))==entity(endpoint(out.identifier,false)) && in.name.trim().equalsIgnoreCase(out.name.trim()) && in.name.containsIgnoreCase("Lightpad")) { auto key=(in.identifier+" & "+out.identifier).toStdString(); if(!rejectedPairs.count(key)) pairs.push_back({in,out,bluetooth(di)}); }
        }
        if(auto* current=active.load()) if(current->identity.rejected.load()) {
            auto key=(current->pair.input.identifier+" & "+current->pair.output.identifier).toStdString(); rejectedPairs.insert(key);
            pairs.erase(std::remove_if(pairs.begin(),pairs.end(),[&](const Pair& p){return (p.input.identifier+" & "+p.output.identifier).toStdString()==key;}),pairs.end());
        }
        // A healthy connection is never preempted by a newly connected USB port.
        if(auto* current=active.load()) for(auto& p:pairs) if(!current->identity.rejected.load() && p.input.identifier==current->pair.input.identifier && p.output.identifier==current->pair.output.identifier) { selected=p; return {p.input.identifier+" & "+p.output.identifier}; }
        std::stable_sort(pairs.begin(),pairs.end(),[](const Pair&a,const Pair&b){return a.bluetooth<b.bluetooth;});
        // Prefer serial-bearing names where available, without trusting names as identity.
        auto suffix=juce::String(serial).getLastCharacters(4);
        auto match=std::find_if(pairs.begin(),pairs.end(),[&](const Pair&p){return p.bluetooth==pairs.front().bluetooth && p.input.name.containsIgnoreCase(suffix);});
        if(!pairs.empty()) { selected=match!=pairs.end()?*match:pairs.front(); return {selected.input.identifier+" & "+selected.output.identifier}; }
        return {};
    }
    roli::PhysicalTopologySource::DeviceConnection* openDevice(int index) override {
        if(index!=0) return nullptr;
        auto c=std::make_unique<Transport>(serial,selected);
        c->sdkLock=std::make_shared<juce::InterProcessLock>("blocks_sdk_"+juce::File::createLegalFileName(selected.input.name)+"_"+juce::File::createLegalFileName(selected.output.name));
        if(!c->sdkLock->enter(0)) { locked=true; return nullptr; } locked=false; c->setLockAgainstOtherProcesses(c->sdkLock);
        c->midiOutput=juce::MidiOutput::openDevice(selected.output.identifier);
        c->midiInput=juce::MidiInput::openDevice(selected.input.identifier,c.get());
        if(!c->midiInput || !c->midiOutput) return nullptr;
        c->generation=++nextGeneration; c->raw=raw; c->wake=wake; c->closed=[this](Transport* p){if(active.load()==p) { active=nullptr; activeGeneration=0; }}; active=c.get(); activeGeneration=c->generation; c->midiInput->start(); return c.release();
    }
    bool isLockedFromOutside() const override { return locked; }
};
struct SourceProgram : roli::Block::Program {
    juce::String source; juce::File directory;
    SourceProgram(roli::Block& b,juce::String s,juce::File d):Program(b),source(std::move(s)),directory(std::move(d)) {}
    juce::String getLittleFootProgram() override { return source; }
    juce::Array<juce::File> getSearchPaths() override { return {directory}; }
};
struct Service : juce::MidiInputCallback, juce::AsyncUpdater, juce::Timer, roli::TopologySource::Listener, roli::Block::ProgramLoadedListener, roli::ControlButton::Listener {
    Options options;
    Detector detector;
    roli::PhysicalTopologySource topology;
    std::unique_ptr<juce::MidiInput> hostOutput;
    std::unique_ptr<juce::MidiOutput> hostInput;
    roli::Block::Ptr block;
    roli::Block::Program* expected=nullptr;
    Transport* loadedTransport=nullptr;
    juce::String source, modalSource, statusSource;
    std::atomic<health::Program> programKind{health::Program::main};
    std::atomic<bool> closeRequested{false}, readySignal{false}, streamResetRequested{false};
    health::Inbox healthInbox;
    health::Monitor healthMonitor;
    std::atomic<int> detailRequested{-1};
    health::NotificationGate notificationGate;
    std::shared_ptr<int> notificationLife=std::make_shared<int>(0);
    std::string notificationResult;
    double lastStatusSeed=-10000, healthChangedAt=0;
    bool metronomeActive() const { return programKind.load()==health::Program::metronome; }
    bool statusActive() const { return programKind.load()==health::Program::status; }
    bool statusEnabled() const { return !statusSource.isEmpty(); }
    std::mutex touchMutex;
    modal::Touches touchObserver;
    std::atomic<uint16_t> heldTouches{0};
    std::atomic<uint64_t> inputEpoch{0};
    // PRIVATE spike: semantic fader reconciliation, no gesture/view ownership.
    std::mutex spikeMutex;
    spike_fader::IntentFrame spikeInputFrame;
    spike_fader::LiveFrame spikeLiveFrame;
    spike_fader::Reconciler spike;
    int spikeEpochCounter=0;
    double spikeHandshakeAt=-10000;
    void spikePump() {
        if(!loaded || fault || !block || !block->isConnected()
            || block->getProgram()!=expected || detector.active.load()!=loadedTransport
            || detector.activeGeneration.load()!=loadedGeneration.load()) return;
        auto* transport=detector.active.load(); if(!transport) return;
        int index=transport->identity.ack.load();
        if(index<0 || index!=transport->identity.target.load()) return;
        std::optional<spike_fader::Dispatch> command;
        std::optional<spike_fader::Feedback> feedback;
        int request=-1;
        { std::lock_guard<std::mutex> lock(spikeMutex);
          if(!spike.liveReady && now()-spikeHandshakeAt>=500) {
              request=spike.epoch; spikeHandshakeAt=now();
          }
          command=spike.takeDispatch(now()); feedback=spike.takeFeedback(now()); }
        if(request>=0) hostInput->sendMessageNow(juce::MidiMessage::controllerEvent(16,79,request));
        if(command) {
            for(auto data:std::array<std::array<int,2>,4>{{{69,command->generation},{70,command->seq},{71,command->value},{72,command->seq}}})
                hostInput->sendMessageNow(juce::MidiMessage::controllerEvent(1,data[0],data[1]));
            forwarded+=4;
        }
        if(feedback) {
            std::array<int32_t,3> words{{feedback->seq,feedback->value,feedback->header()}};
            bp::HostPacketBuilder<200> packet; packet.writePacketSysexHeaderBytes(uint8_t(index));
            if(!packet.addProgramEventMessage(words.data())) { fail("spike SDK packet capacity"); return; }
            packet.writePacketSysexFooter();
            if(!transport->sendMessageToDevice(packet.getData(),size_t(packet.size()))) {
                fail("spike SDK feedback rejected"); return;
            }
            ++units; ++sent;
        }
    }
    std::atomic<double> readyMarkerAt{0};
    button_hold::Recognizer clicks;
    std::vector<roli::ControlButton*> modeButtons;
    std::atomic<int> permissionReply{-1};
    int permissionNonce=0, pendingNonce=-1;
    uint64_t permissionEpoch=0;
    double permissionAt=0, swapAt=0, lastSwapMs=0, lastReadyMs=0, readyAt=0;
    bool readyReported=false;
    uint8_t faultReason=health::programLoadFailed;
    uint64_t singleClicks=0, longPresses=0, swapCount=0;
    int trialLeft=0;
    double now() const { return juce::Time::getMillisecondCounterHiRes(); }
    void event(const juce::String& kind,const juce::String& detail={}) {
        if(options.status.empty()) return;
        juce::DynamicObject::Ptr o=new juce::DynamicObject;
        o->setProperty("event",kind); o->setProperty("at_ms",now());
        o->setProperty("program",health::programName(programKind.load()));
        o->setProperty("detail",detail);
        juce::File(options.status+".events.jsonl").appendText(juce::JSON::toString(juce::var(o.get()),true)+"\n");
    }
    void detachButtons() {
        for(auto* b:modeButtons) b->removeListener(this);
        modeButtons.clear(); clicks.reset();
    }
    void attachButtons() {
        if(!block) return;
        for(auto* b:block->getButtons()) if(b->getType()==roli::ControlButton::mode) {
            b->addListener(this); modeButtons.push_back(b);
        }
    }
    void buttonPressed(roli::ControlButton&,roli::Block::Timestamp) override {
        processControl();
        event("button_down");
        if(loaded && !fault) clicks.press(now());
    }
    void buttonReleased(roli::ControlButton&,roli::Block::Timestamp) override {
        processControl();
        event("button_up");
        if(auto click=clicks.release(now())) handleClick(*click);
    }
    void handleClick(button_hold::Action click) {
        if(options.optimisticFaderSpike) return;
        if(statusActive()) return; // Status details are requested only by tapping its icon.
        if(click==button_hold::Action::longPress) {
            ++longPresses; event("long_press","input selection reserved; no input changes"); status(); return;
        }
        ++singleClicks; event("single_click");
        if(!loaded || fault || heldTouches.load()!=0 || pendingNonce>=0) {
            event("switch_ignored","not idle, not loaded, or permission pending"); return;
        }
        if(metronomeActive()) { switchProgram(health::Program::main); return; }
        requestMetronome();
    }
    void requestMetronome() {
        if(statusActive() || modalSource.isEmpty() || !loaded || heldTouches.load()!=0 || pendingNonce>=0) return;
        pendingNonce=(++permissionNonce)&127;
        permissionEpoch=inputEpoch.load(); permissionAt=now(); permissionReply=-1;
        event("permission_request",juce::String(pendingNonce));
        hostInput->sendMessageNow(juce::MidiMessage::controllerEvent(16,109,pendingNonce));
    }
    void switchProgram(health::Program target,bool closeCancelsHeld=false) {
        if(!block || !block->isConnected() || !loaded || (target==health::Program::metronome && modalSource.isEmpty()) || (target==health::Program::status && !statusEnabled())) return;
        {
            std::lock_guard<std::mutex> touchLock(touchMutex);
            if((!closeCancelsHeld && heldTouches.load()!=0) || (target==health::Program::metronome && inputEpoch.load()!=permissionEpoch)) {
                event("switch_ignored","new pad input after permission query"); return;
            }
            loaded=false; readySignal=false;
            clicks.reset();
            touchObserver.reset(); heldTouches=0;
        }
        pendingNonce=-1; permissionReply=-1; pendingHello=false; awaitingHello=true;
        { std::lock_guard<std::mutex> lock(mutex); queue.clear(); framer.reset(); }
        programKind=target; lastStatusSeed=-10000; readyReported=false; swapAt=now(); ++swapCount;
        event("swap_begin");
        auto text=target==health::Program::status?statusSource:target==health::Program::metronome?modalSource:source;
        auto path=target==health::Program::status?options.statusSource:target==health::Program::metronome?options.modalSource:options.source;
        auto program=std::make_unique<SourceProgram>(*block,text,juce::File(path).getParentDirectory());
        expected=program.get();
        auto result=block->setProgram(std::move(program),roli::Block::ProgramPersistency::setAsTemp);
        if(result.failed()) fail("temporary modal upload failed: "+result.getErrorMessage().toStdString());
        status();
    }
    health::State displayState() const {
        if(fault.load()) return health::State::failure;
        if(!block || !block->isConnected()) return health::State::bridgeUnavailable;
        if(!loaded.load() || (!statusActive() && !readyReported)) return health::State::connecting;
        return healthMonitor.state(now());
    }
    uint8_t displayReason() const {
        if(fault.load()) return faultReason;
        if(!block || !block->isConnected()) return detector.locked?health::deviceBusy:health::deviceDisconnected;
        if(!loaded.load()) return health::starting;
        return healthMonitor.reason(now());
    }
    void processHealth() {
        std::optional<health::Receipt> incoming;
        { std::lock_guard<std::mutex> lock(mutex); incoming=healthInbox.take(); }
        if(incoming) {
            if(!healthMonitor.observed || healthMonitor.observed->state!=incoming->frame.state
                || healthMonitor.observed->reason!=incoming->frame.reason) healthChangedAt=incoming->at;
            healthMonitor.update(incoming->frame,incoming->at);
            if(incoming->frame.state==health::State::ready && !fault.load() && !awaitingHello.load()) {
                std::lock_guard<std::mutex> lock(mutex); error.clear();
            }
        }
        if(detailRequested.exchange(-1)>=0 && statusActive() && notificationGate.accept(now())) {
            auto why=health::explanation(displayState(),displayReason());
            notificationResult="requesting";
            std::weak_ptr<int> life=notificationLife;
            requestBridgeNotification(why.title,why.body,[this,life](std::string result) {
                if(life.expired()) return;
                notificationResult=std::move(result); event("status_notification",juce::String(notificationResult)); status();
            });
        }
    }
    void seedStatus() {
        if(!loaded || !statusActive() || now()-lastStatusSeed<500 || !block || !block->isConnected()
            || block->getProgram()!=expected || detector.active.load()!=loadedTransport
            || detector.activeGeneration.load()!=loadedGeneration.load()) return;
        auto* transport=detector.active.load(); if(!transport) return;
        int index=transport->identity.ack.load();
        if(index<0 || index!=transport->identity.target.load()) return;
        auto words=health::event(displayState(),displayReason());
        bp::HostPacketBuilder<200> packet;packet.writePacketSysexHeaderBytes(uint8_t(index));
        if(!packet.addProgramEventMessage(words.data())) { fail("SDK status packet capacity"); return; }
        packet.writePacketSysexFooter(); lastStatusSeed=now();
        if(!transport->sendMessageToDevice(packet.getData(),size_t(packet.size()))) recoverStream("SDK rejected status feedback; reconnecting");
    }
    void updateProgramForHealth() {
        if(!statusEnabled() || !loaded || fault || !block || !block->isConnected()) return;
        auto target=health::transition(programKind.load(),loaded.load(),statusEnabled(),heldTouches.load(),
            pendingNonce>=0,healthMonitor,now(),healthChangedAt);
        if(target==health::Transition::main) { switchProgram(health::Program::main); return; }
        if(target==health::Transition::status) { switchProgram(health::Program::status); return; }
        seedStatus();
    }
    void processControl() {
        if(streamResetRequested.exchange(false)) {
            pendingNonce=-1; permissionReply=-1; clicks.reset();
        }
        if(closeRequested.exchange(false) && metronomeActive() && loaded) {
            event("close_button"); switchProgram(health::Program::main,true);
        }
        int reply=permissionReply.exchange(-1);
        if(pendingNonce>=0 && reply>=0) {
            int token=reply&127; bool allowed=(reply&128)!=0;
            if(token==pendingNonce) {
                bool fresh=now()-permissionAt<=1000;
                event(allowed?"permission_allowed":"permission_denied",juce::String(token));
                pendingNonce=-1;
                if(allowed && fresh) switchProgram(health::Program::metronome);
            }
        }
        if(pendingNonce>=0 && now()-permissionAt>1000) {
            event("permission_timeout"); pendingNonce=-1;
        }
    }
    std::atomic<bool> loaded{false}, pendingHello{false}, awaitingHello{false}, fault{false};
    std::atomic<uint64_t> loadedGeneration{0};
    std::string error;
    std::mutex mutex;
    bridge::Framer framer;
    struct Pending { bridge::Unit messages; double arrival; uint64_t generation; };
    std::deque<Pending> queue;
    std::atomic<uint64_t> received{0}, forwarded{0}, units{0}, sent{0}, errors{0};
    size_t highWater=0;
    unsigned statusTicks=0;
    explicit Service(Options o):options(std::move(o)),detector(options.serial),topology(detector,true) {
        if(options.optimisticFaderSpike) { options.modalSource.clear(); options.statusSource.clear(); options.trialCycles=0; }
        juce::File file(options.source); if(!file.existsAsFile() || file.getSize()>1024*1024) throw std::runtime_error("source is missing or too large");
        source=file.loadFileAsString(); if(source.isEmpty()) throw std::runtime_error("source is empty");
        if(!options.modalSource.empty()) {
            juce::File modalFile(options.modalSource);
            if(!modalFile.existsAsFile() || modalFile.getSize()>1024*1024) throw std::runtime_error("modal source is missing or too large");
            modalSource=modalFile.loadFileAsString();
        }
        if(!options.statusSource.empty()) {
            juce::File statusFile(options.statusSource);
            if(!statusFile.existsAsFile() || statusFile.getSize()>1024*1024) throw std::runtime_error("status source is missing or too large");
            statusSource=statusFile.loadFileAsString();
            if(statusSource.isEmpty()) throw std::runtime_error("status source is empty");
        }
        trialLeft=options.trialCycles;

        hostInput=juce::MidiOutput::createNewDevice("Lightpad Bridge"); hostOutput=juce::MidiInput::createNewDevice("Lightpad Bridge",this);
        if(!hostInput || !hostOutput) throw std::runtime_error("cannot create virtual MIDI ports");
        detector.raw=[this](Transport* sender,const juce::MidiMessage& message) {
            if(detector.active.load()!=sender || !message.isController()) return;
            auto m=message;
            if(options.optimisticFaderSpike) {
                if(!loaded.load() || fault.load() || loadedGeneration.load()!=sender->generation) return;
                std::lock_guard<std::mutex> lock(spikeMutex);
                if(auto wanted=spikeInputFrame.push(m.getChannel(),m.getControllerNumber(),m.getControllerValue())) {
                    spike.intent(*wanted,now()); triggerAsyncUpdate();
                }
                return;
            }
            if(m.getChannel()==16 && m.getControllerNumber()==108 && (m.getControllerValue()==34 || m.getControllerValue()==35 || m.getControllerValue()==36)) {
                // This is model-ready instrumentation, not an optical timestamp.
                std::lock_guard<std::mutex> touchLock(touchMutex);
                if(loaded && !fault && loadedGeneration.load()==sender->generation
                    && m.getControllerValue()==(statusActive()?36:metronomeActive()?35:34)) {
                    readyMarkerAt=now(); readySignal=true; triggerAsyncUpdate();
                }
                return;
            }

            if(m.getChannel()==16 && statusActive() && loaded && !fault
                && loadedGeneration.load()==sender->generation) {
                if(m.getControllerNumber()==106 && m.getControllerValue()<=16) {
                    heldTouches=uint16_t(m.getControllerValue()); triggerAsyncUpdate(); return;
                }
                if(health::diagnosticTap(m.isController(),m.getChannel(),m.getControllerNumber(),
                    m.getControllerValue(),programKind.load(),loaded.load())) {
                    detailRequested=m.getControllerValue(); triggerAsyncUpdate(); return;
                }
            }
            bool hello=m.getChannel()==16 && m.getControllerNumber()==110 && m.getControllerValue()==18;
            if(hello) pendingHello=true;
            if(!loaded.load() || fault.load() || loadedGeneration.load()!=sender->generation) return;
            if(hello) awaitingHello=false;
            if(hello) { pendingHello=false; m=juce::MidiMessage::controllerEvent(16,110,18); }
            {
                std::lock_guard<std::mutex> touchLock(touchMutex);
                if(!loaded.load()) return;
                if(hello) { ++inputEpoch; touchObserver.reset(); heldTouches=0; streamResetRequested=true; triggerAsyncUpdate(); }
                else if(m.getControllerNumber()>=20 && m.getControllerNumber()<=26) ++inputEpoch;
                bool close=touchObserver.push(m.getChannel()-1,m.getControllerNumber(),m.getControllerValue(),metronomeActive());
                heldTouches=touchObserver.held;
                hostInput->sendMessageNow(m); ++forwarded;
                if(close) { closeRequested=true; triggerAsyncUpdate(); }
            }

        };
        detector.wake=[this]{triggerAsyncUpdate();};
        hostOutput->start(); topology.addListener(this); topology.setActive(true); startTimer(10); status();
    }
    ~Service() override {
        notificationLife.reset(); stopTimer(); hostOutput->stop(); cancelPendingUpdate(); detachButtons();
        if(block) block->removeProgramLoadedListener(this);
        topology.removeListener(this); topology.setActive(false); detector.raw=nullptr;
        status("stopped");
    }
    void fail(std::string why,uint8_t reason=health::programLoadFailed) { faultReason=reason; fault=true; loaded=false; ++errors; std::lock_guard<std::mutex> lock(mutex); error=std::move(why); queue.clear(); framer.reset(); }
    void recoverStream(std::string why) { awaitingHello=true; ++errors; std::lock_guard<std::mutex> lock(mutex); error=std::move(why); queue.clear(); framer.reset(); }
    void handleIncomingMidiMessage(juce::MidiInput*,const juce::MidiMessage& m) override {
        if(options.optimisticFaderSpike) {
            if(!m.isController()) return;
            std::lock_guard<std::mutex> lock(spikeMutex);
            if(m.getChannel()==16 && m.getControllerNumber()==79) spike.handshake(m.getControllerValue());
            else if(auto actual=spikeLiveFrame.push(m.getChannel(),m.getControllerNumber(),m.getControllerValue()))
                spike.receive(*actual,now(),options.spikeAckDelayMs);
            triggerAsyncUpdate(); return;
        }
        if(!m.isController()) {
            std::lock_guard<std::mutex> lock(mutex); healthInbox.message(false,0,0,0,now()); return;
        }
        if(m.getChannel()==16 && m.getControllerNumber()>=114 && m.getControllerNumber()<=118) {
            std::lock_guard<std::mutex> lock(mutex);
            // This lane describes Live, not device upload readiness. Bluetooth
            // uploads exceed the health TTL, so gating on loaded starves liveness
            // and makes main immediately swap back to status after every upload.
            if(healthInbox.message(true,m.getChannel(),m.getControllerNumber(),m.getControllerValue(),now()))
                triggerAsyncUpdate();
            return;
        }
        { std::lock_guard<std::mutex> lock(mutex);
          // The health unit is atomic on its host MIDI callback lane.
          // Any unrelated message interrupts a partially assembled heartbeat.
          healthInbox.message(true,m.getChannel(),m.getControllerNumber(),m.getControllerValue(),now());
          if(m.getChannel()==16 && m.getControllerNumber()==110) {
              healthInbox.reset();
          }
        }
        if(m.getChannel()==16 && (m.getControllerNumber()==108 || m.getControllerNumber()==109)) {
            permissionReply=m.getControllerValue() | (m.getControllerNumber()==109?128:0);
            triggerAsyncUpdate(); return;
        }
        if(!loaded.load() || fault.load() || awaitingHello.load()) return;
        auto generation=loadedGeneration.load();
        ++received;
        auto p=m.getRawData();
        { std::lock_guard<std::mutex> lock(mutex);
          if(!loaded.load() || fault.load() || awaitingHello.load() || generation!=loadedGeneration.load() || generation!=detector.activeGeneration.load()) return;
          try { bridge::Unit unit; if(framer.push({p[0],p[1],p[2]},unit)) { if(queue.size()>=64) throw std::runtime_error("host FIFO overflow"); queue.push_back({std::move(unit),juce::Time::getMillisecondCounterHiRes(),generation}); highWater=std::max(highWater,queue.size()); } }
          catch(const std::exception& e) { error=e.what(); ++errors; awaitingHello=true; queue.clear(); framer.reset(); }
        } triggerAsyncUpdate();
    }
    void handleAsyncUpdate() override {
        if(options.optimisticFaderSpike) { spikePump(); return; }
        processHealth();
        if(readySignal.exchange(false) && loaded && !readyReported) {
            readyReported=true; readyAt=now(); lastReadyMs=swapAt>0?readyMarkerAt.load()-swapAt:0;
            event("device_model_ready",juce::String(lastReadyMs,3)); status();
        }
        processControl();
        if(fault) return;
        if(!loaded || detector.active.load()!=loadedTransport || !block || !block->isConnected() || block->getProgram()!=expected) return;
        auto* transport=detector.active.load(); int index=transport->identity.ack.load(); if(index<0 || index!=transport->identity.target.load()) return;
        for(;;) {
            Pending job; { std::lock_guard<std::mutex> lock(mutex); if(queue.empty()) break; job=std::move(queue.front()); queue.pop_front();

            }
            if(job.generation!=loadedGeneration.load() || juce::Time::getMillisecondCounterHiRes()-job.arrival>1000) { recoverStream("stale feedback FIFO; awaiting fresh device Hello"); break; }
            bp::HostPacketBuilder<200> packet; packet.writePacketSysexHeaderBytes(uint8_t(index));
            bridge::Events events;
            try { events=bridge::events(job.messages); }
            catch(const std::exception& e) { recoverStream(e.what()); break; }
            if(statusActive() || events.empty()) { sent+=job.messages.size(); continue; }
            for(const auto& values:events) { if(!packet.addProgramEventMessage(values.data())) { fail("SDK packet capacity"); return; } }
            packet.writePacketSysexFooter();
            if(!transport->sendMessageToDevice(packet.getData(),size_t(packet.size()))) { recoverStream("SDK transport rejected feedback; awaiting fresh Hello"); break; }
            ++units; sent+=job.messages.size();
        }
    }
    void topologyChanged() override { select(); }
    void blockAdded(const roli::Block::Ptr) override { select(); }
    void blockRemoved(const roli::Block::Ptr) override { select(); }
    void blockUpdated(const roli::Block::Ptr) override { select(); }
    void select() {
        if(fault.load()) return;
        roli::Block::Ptr found;
        for(auto b:topology.getCurrentTopology().blocks) if(b->serialNumber.toStdString()==options.serial && b->isConnected()) { if(found) { fail("duplicate serial topology",health::deviceRejected); return; } found=b; }
        if(!found && !block && !loadedTransport) return;
        if(found==block && found && expected && found->getProgram()==expected && detector.active.load()==loadedTransport && detector.activeGeneration.load()==loadedGeneration.load()) return;
        detachButtons();
        if(block) block->removeProgramLoadedListener(this);
        block=found; expected=nullptr; loaded=false; pendingHello=false; awaitingHello=false; loadedGeneration=0; loadedTransport=nullptr;
        { std::lock_guard<std::mutex> lock(mutex); queue.clear(); framer.reset(); }
        { std::lock_guard<std::mutex> lock(touchMutex); touchObserver.reset(); heldTouches=0; }
        pendingNonce=-1; permissionReply=-1; programKind=statusEnabled()?health::Program::status:health::Program::main;
        healthMonitor.reset(); lastStatusSeed=-10000; healthChangedAt=now();
        { std::lock_guard<std::mutex> lock(mutex); healthInbox.reset(); }
        readyReported=false; readySignal=false; swapAt=now();
        if(options.optimisticFaderSpike) {
            std::lock_guard<std::mutex> lock(spikeMutex);
            spikeInputFrame.reset(); spikeLiveFrame.reset();
            spike.reset((++spikeEpochCounter%127)+1,now()); spikeHandshakeAt=-10000;
        }
        if(!block || fault) { status(); return; }
        block->addProgramLoadedListener(this); attachButtons();
        auto initialSource=statusActive()?statusSource:source;
        auto initialPath=statusActive()?options.statusSource:options.source;
        auto program=std::make_unique<SourceProgram>(*block,initialSource,juce::File(initialPath).getParentDirectory()); expected=program.get(); loadedTransport=detector.active.load(); loadedGeneration=detector.activeGeneration.load();
        auto result=block->setProgram(std::move(program),roli::Block::ProgramPersistency::setAsTemp);
        if(result.failed()) fail("temporary compile/upload failed: "+result.getErrorMessage().toStdString());
        status();
    }
    void handleProgramLoaded(roli::Block& b) override {
        if(block.get()!=&b || b.getProgram()!=expected || detector.active.load()!=loadedTransport || detector.activeGeneration.load()!=loadedGeneration.load()) return;
        loaded=true; lastSwapMs=now()-swapAt; event("sdk_loaded",juce::String(lastSwapMs,3));
        if(options.optimisticFaderSpike) {
            awaitingHello=false; pendingHello=false; readyReported=true;
            readyAt=now(); status(); triggerAsyncUpdate(); return;
        }
        if(pendingHello.exchange(false)) {
            awaitingHello=false;
            hostInput->sendMessageNow(juce::MidiMessage::controllerEvent(16,110,18)); ++forwarded;
        }
        if(statusActive()) awaitingHello=false;
        status(); triggerAsyncUpdate();
    }

    void timerCallback() override {
        if(stopping) return;
        if(options.optimisticFaderSpike) {
            select(); spikePump(); if(++statusTicks%100==0) status(); return;
        }
        select();
        processHealth();
        processControl();
        if(auto click=clicks.poll(now())) handleClick(*click);
        processControl();
        if(trialLeft>0 && !statusActive() && loaded && readyReported && now()-readyAt>1500 && pendingNonce<0 && heldTouches.load()==0) {
            if(metronomeActive()) { --trialLeft; switchProgram(health::Program::main); }
            else requestMetronome();
        }
        updateProgramForHealth();
        if(++statusTicks%100==0) status();
    }
    void status(const std::string& overrideState={}) {
        if(options.status.empty()) return;
        juce::DynamicObject::Ptr object=new juce::DynamicObject;
        std::string state=overrideState.empty()?(fault?"fault":awaitingHello?"awaiting_hello":loaded?(statusEnabled()?(healthMonitor.usable(now()) && readyReported && !statusActive()?"ready":"waiting_for_live"):"ready"):block?"uploading":"waiting"):overrideState;
        object->setProperty("program",health::programName(programKind.load()));
        if(options.optimisticFaderSpike) {
            std::lock_guard<std::mutex> lock(spikeMutex);
            object->setProperty("optimistic_fader_spike",true);
            object->setProperty("spike_live_ready",spike.liveReady);
            object->setProperty("spike_epoch",spike.epoch);
            object->setProperty("spike_pending",spike.pending());
            object->setProperty("spike_desired",spike.desiredValue);
            object->setProperty("spike_confirmed",spike.confirmedValue);
            object->setProperty("spike_inflight_seq",spike.inflightSeq());
            object->setProperty("spike_intents",juce::int64(spike.receivedIntents));
            object->setProperty("spike_dispatches",juce::int64(spike.dispatched));
            object->setProperty("spike_acks",juce::int64(spike.acknowledged));
            object->setProperty("spike_ignored_replies",juce::int64(spike.ignoredReplies));
            object->setProperty("spike_ack_delay_ms",options.spikeAckDelayMs);
            object->setProperty("spike_last_intent_at_ms",spike.lastIntentAt);
            object->setProperty("spike_last_dispatch_at_ms",spike.lastDispatchAt);
            object->setProperty("spike_last_native_ack_at_ms",spike.lastAckAt);
            object->setProperty("spike_max_intent_to_dispatch_ms",spike.maxIntentToDispatchMs);
            object->setProperty("spike_max_native_ack_ms",spike.maxNativeAckMs);
        }
        object->setProperty("program_loaded",loaded.load()); object->setProperty("device_model_ready",readyReported);
        object->setProperty("playable",loaded.load() && readyReported && !statusActive() && (!statusEnabled() || healthMonitor.usable(now())));
        object->setProperty("host_health",health::stateName(healthMonitor.state(now())));
        object->setProperty("host_health_reason",int(healthMonitor.reason(now())));
        object->setProperty("host_health_fresh",healthMonitor.fresh(now()));
        object->setProperty("recording_busy",healthMonitor.busy());
        auto explanation=health::explanation(displayState(),displayReason());
        object->setProperty("explanation_title",juce::String(explanation.title));
        object->setProperty("explanation",juce::String(explanation.body));
        object->setProperty("last_notification",juce::String(notificationResult));
        object->setProperty("button_single_clicks",juce::int64(singleClicks)); object->setProperty("button_long_presses",juce::int64(longPresses));
        object->setProperty("button_hold_threshold_ms",400);
        object->setProperty("program_swaps",juce::int64(swapCount)); object->setProperty("last_sdk_load_ms",lastSwapMs); object->setProperty("last_model_ready_ms",lastReadyMs);
        object->setProperty("mode_button_count",int(modeButtons.size())); object->setProperty("held_touches",int(heldTouches.load())); object->setProperty("permission_pending",pendingNonce>=0);
        object->setProperty("trial_cycles_remaining",trialLeft);
        object->setProperty("pid",int(getpid())); object->setProperty("state",juce::String(state)); object->setProperty("serial",juce::String(options.serial)); { std::lock_guard<std::mutex> lock(mutex); object->setProperty("error",juce::String(fault.load() || awaitingHello.load()?error:std::string())); object->setProperty("last_error",juce::String(error)); object->setProperty("queue_high_water",int(highWater)); }
        object->setProperty("received_cc",juce::int64(received.load())); object->setProperty("forwarded_cc",juce::int64(forwarded.load())); object->setProperty("sent_cc",juce::int64(sent.load())); object->setProperty("sent_units",juce::int64(units.load())); object->setProperty("errors",juce::int64(errors.load())); object->setProperty("generation",juce::int64(loadedGeneration.load()));
        if(auto* current=detector.active.load()) { object->setProperty("input",current->pair.input.name); object->setProperty("output",current->pair.output.name); object->setProperty("connection",current->pair.bluetooth?"bluetooth":"usb"); object->setProperty("topology_index",current->identity.target.load()); object->setProperty("ack_index",current->identity.ack.load()); }
        juce::File destination(options.status), tmp(options.status+".tmp."+std::to_string(getpid()));
        if(tmp.replaceWithText(juce::JSON::toString(juce::var(object.get())))) tmp.moveFileTo(destination);
    }
};
int main(int argc,char** argv) {
    try {
        Options options;
        auto bundle=bridgeBundleConfiguration();
        options.serial=bundle.serial;options.source=bundle.source;options.modalSource=bundle.modalSource;
        options.statusSource=bundle.statusSource;options.status=bundle.status;options.lock=bundle.lock;
        for(int i=1;i<argc;++i) { std::string arg=argv[i];
            if(arg=="--bundle-info") {
                juce::DynamicObject::Ptr info=new juce::DynamicObject;
                info->setProperty("serial",juce::String(options.serial));
                info->setProperty("controller",juce::String(options.source));
                info->setProperty("metronome",juce::String(options.modalSource));
                info->setProperty("status_program",juce::String(options.statusSource));
                std::cout<<juce::JSON::toString(juce::var(info.get()),true)<<'\n';return 0;
            }
            if(arg=="--explain") {
                auto path=i+1<argc?std::string(argv[++i]):options.status;
                if(path.empty()) throw std::runtime_error("--explain requires a status file outside the app bundle");
                auto report=juce::JSON::parse(juce::File(path));
                if(!report.isObject()) throw std::runtime_error("No readable bridge status. Start Lightpad Bridge first.");
                std::cout<<report.getProperty("explanation_title", "Lightpad status").toString()<<"\n"
                         <<report.getProperty("explanation", "Check the bridge connection.").toString()<<"\n";
                return 0;
            }
            if(arg=="--optimistic-fader-spike") { options.optimisticFaderSpike=true; continue; }
            if(arg=="--spike-ack-delay-ms") {
                if(i+1>=argc) throw std::runtime_error("missing spike ACK delay");
                options.spikeAckDelayMs=std::stoi(argv[++i]);
                if(options.spikeAckDelayMs<0 || options.spikeAckDelayMs>10000) throw std::runtime_error("invalid spike ACK delay");
                continue;
            }
            if(arg=="--help") { std::cout<<"lightpad-bridge --device SERIAL --source FILE [--modal-source FILE] [--status-source FILE] [--trial-cycles N] [--status-file FILE] [--lock-file FILE]\n"; return 0; } if(arg=="--version") { std::cout<<"lightpad-bridge 0.2.0\n"; return 0; } if(i+1>=argc) throw std::runtime_error("missing argument value"); std::string value=argv[++i]; if(arg=="--device") options.serial=value; else if(arg=="--source") options.source=value; else if(arg=="--modal-source") options.modalSource=value; else if(arg=="--status-source") options.statusSource=value; else if(arg=="--trial-cycles") { options.trialCycles=std::stoi(value); if(options.trialCycles<0 || options.trialCycles>6) throw std::runtime_error("invalid trial cycles"); } else if(arg=="--status-file") options.status=value; else if(arg=="--lock-file") options.lock=value; else throw std::runtime_error("unknown argument: "+arg); }
        if(options.spikeAckDelayMs && !options.optimisticFaderSpike) throw std::runtime_error("spike ACK delay requires spike mode");
        if(options.serial.size()!=16 || options.source.empty()) throw std::runtime_error("--device requires exact 16-character serial and --source is required");
        if(options.lock.empty()) options.lock="/tmp/lightpad-bridge-"+std::to_string(getuid())+".lock";
        if(!juce::File(options.lock).getParentDirectory().createDirectory()) throw std::runtime_error("cannot create bridge support directory");
        Singleton lock(options.lock); juce::ScopedJuceInitialiser_GUI init;
        std::signal(SIGINT,signalHandler); std::signal(SIGTERM,signalHandler);
        Service service(std::move(options));
        while(!stopping) dispatchBridgeEvents();
        return 0;
    } catch(const std::exception& e) { std::cerr<<"lightpad-bridge: "<<e.what()<<'\n'; return 1; }
}
