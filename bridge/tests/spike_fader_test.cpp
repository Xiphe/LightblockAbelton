#include "spike_fader.hpp"
#include <cassert>
using namespace spike_fader;
int main() {
    IntentFrame physical;
    assert(!physical.push(1,70,5)); assert(!physical.push(1,71,99));
    auto intent=physical.push(1,72,5); assert(intent && intent->value==99);
    physical.push(1,70,6); physical.push(1,71,22); assert(!physical.push(1,72,7));
    LiveFrame native;
    native.push(1,69,4);native.push(1,73,3);native.push(1,74,90);
    auto actual=native.push(1,75,3); assert(actual && actual->generation==4 && actual->kind==2);
    Reconciler r; r.reset(4,0); auto seed=r.takeFeedback(0); assert(seed && seed->kind==0);
    r.intent({1,80});assert(!r.takeDispatch());r.handshake(3);assert(!r.takeDispatch());r.handshake(4);
    auto first=r.takeDispatch(); assert(first && first->value==80 && first->seq==0);
    for(int i=2;i<=20;++i) r.intent({i,80+i});
    assert(!r.takeDispatch());
    r.receive({2,4,0,80},10,1000);assert(!r.takeFeedback(5000));
    auto latest=r.takeDispatch();assert(latest && latest->value==100 && latest->serial==20 && latest->seq==1);
    r.receive({2,3,1,100},20,1000);assert(r.pending());
    r.receive({2,4,0,80},21,1000);assert(r.pending());
    r.receive({2,4,1,100},22,1000);assert(!r.pending());assert(!r.takeFeedback(1021));
    // A new intent invalidates the old delayed ACK; final End value must survive.
    r.intent({21,0});auto end=r.takeDispatch();assert(end && end->value==0);
    assert(!r.takeFeedback(2000));
    r.receive({1,4,10,100},30,1000);assert(!r.takeFeedback(2000));
    r.receive({2,4,end->seq,0},40,1000);auto final=r.takeFeedback(1040);
    assert(final && final->kind==2 && final->seq==21 && final->value==0);
    assert((final->header()&255)==0xf1 && ((final->header()>>10)&127)==4);
    r.receive({1,4,11,60},50,1000);r.receive({1,4,12,70},60,1000);
    r.receive({1,4,11,60},70,1000);auto observation=r.takeFeedback(1060);
    assert(observation && observation->kind==1 && observation->value==70);
    assert(((observation->revision-final->revision)&16383)<8192);
    // An external observation while the latest ACK is delayed must not lose
    // the acknowledgement needed to settle that device intent.
    Reconciler merged;merged.reset(5,0);merged.takeFeedback(0);merged.handshake(5);
    merged.intent({7,90});auto write=merged.takeDispatch();
    merged.receive({2,5,write->seq,90},10,1000);
    merged.receive({1,5,1,80},20,1000);
    auto ackAndActual=merged.takeFeedback(1020);
    assert(ackAndActual && ackAndActual->kind==2 && ackAndActual->seq==7 && ackAndActual->value==80);
    assert(r.dispatched==3 && r.acknowledged==3 && r.receivedIntents==21);
}
