#include "../native/performance.hpp"
#include <cassert>
#include <iostream>
#include <vector>
using namespace performance;

static Intent intent(int sequence,int key,int value,int generation=1) { return Intent{sequence,key,value,generation}; }
static Ack ack(const Dispatch& d,int value) { return Ack{d.generation,d.seq,d.key,value}; }
static std::vector<Feedback> drain(Reconciler& r,double time=0,bool patchNames=false) {
    std::vector<Feedback> out;
    for(int n=0;n<1024;++n) {auto value=r.takeFeedback(time,patchNames);if(!value) return out;out.push_back(*value);}
    assert(false);return out;
}
static void ready(Reconciler& r,int generation=1) {r.reset(generation,0);r.handshake(generation,1);auto s=r.takeFeedback(1);assert(s&&s->kind==0&&s->a==127);}

static void parsers() {
    IntentFrame input;
    assert(!input.push(1,70,2));assert(!input.push(1,71,56));
    int bytes[][2]={{69,4},{70,2},{71,56},{72,1},{73,1},{74,2}};
    for(int i=0;i<5;++i) assert(!input.push(1,bytes[i][0],bytes[i][1]));
    // 129 fits the8-bit mute mask and is reconstructed atomically.
    auto value=input.push(1,74,2);assert(value&&value->generation==4&&value->seq==2&&value->key==56&&value->value==129);
    assert(!input.push(1,69,4));assert(!input.push(1,70,3));assert(!input.push(1,71,0));
    assert(!input.push(1,72,90));assert(!input.push(1,73,0));assert(!input.push(1,74,4));
    assert(!input.push(1,69,0));assert(!input.push(1,70,3));
    assert(!input.push(1,69,4));assert(!input.push(1,70,128));
    assert(!input.push(1,71,56));assert(!input.push(1,72,1));assert(!input.push(1,73,0));assert(!input.push(1,74,3));
    assert(valid(intent(0,113,127)));assert(valid(intent(0,114,99)));assert(!valid(intent(0,115,3)));

    AckFrame replies;
    int ackBytes[][2]={{69,4},{75,3},{76,0},{77,127},{78,127},{79,3}};
    for(int i=0;i<5;++i) assert(!replies.push(1,ackBytes[i][0],ackBytes[i][1]));
    auto a=replies.push(1,79,3);assert(a&&a->value==16383&&a->generation==4&&a->seq==3&&a->key==0);
    assert(!replies.push(1,69,4));assert(!replies.push(1,75,2));assert(!replies.push(1,77,3));assert(!replies.push(1,79,2));

    ObservationFrame facts;uint32_t wordA=0xfedcba,wordB=0xabc1234;
    int prefix[][2]={{89,4},{90,7},{91,4}};
    for(auto& part:prefix) assert(!facts.push(1,part[0],part[1]));
    for(int i=0;i<4;++i) assert(!facts.push(1,92+i,int((wordA>>(7*i))&127)));
    for(int i=0;i<4;++i) assert(!facts.push(1,96+i,int((wordB>>(7*i))&127)));
    auto fact=facts.push(1,100,7);assert(fact&&fact->a==wordA&&fact->b==wordB&&fact->generation==4&&fact->seq==7);
    assert(!facts.push(1,89,4));assert(!facts.push(1,90,7));assert(!facts.push(1,91,8));assert(!facts.push(1,100,7));
    assert(!facts.push(1,89,4));assert(!facts.push(1,90,7));assert(!facts.push(1,91,3));assert(!facts.push(1,93,1));assert(!facts.push(1,100,7));
}
static void superseding_parameters() {
    Reconciler r;ready(r);
    assert(r.intent(intent(0,0,20),2));auto first=r.takeDispatch(3);assert(first&&first->key==0&&first->value==20);
    assert(r.intent(intent(1,0,80),4));assert(!r.takeDispatch(5));
    r.receiveAck(ack(*first,20),6);
    for(auto f:drain(r,6)) assert(f.kind!=2); // old ACK must not clear new intent
    auto next=r.takeDispatch(7);assert(next&&next->value==80&&next->deviceSeq==1);
    r.receiveAck(ack(*first,20),8);assert(r.inflightSeq()==next->seq);assert(r.ignoredReplies==1);
    r.receiveAck(ack(*next,80),9);auto feedback=r.takeFeedback(9);
    assert(feedback&&feedback->kind==2&&feedback->a==(0|(1<<7))&&feedback->b==80);
    assert(!r.pending());assert(r.confirmed(0)==80);assert(r.desired(0)==80);
    assert(r.maxNativeAckMs==3);assert(r.maxIntentToDispatchMs==3);

    for(int n=2;n<102;++n) assert(r.intent(intent(n&127,1,n%128),n));
    auto final=r.takeDispatch(105);assert(final&&final->key==1&&final->value==101&&final->deviceSeq==101);
    assert(r.dispatched==3);r.receiveAck(ack(*final,101),106);
    for(auto f:drain(r,106)) assert(f.revision>=1&&f.revision<8192&&((f.header()&255)==0xf2));
}
static void discrete_order_and_deduplication() {
    Reconciler r;ready(r);
    assert(r.intent(intent(0,0,90),2));
    assert(r.intent(intent(1,56,3),3));
    assert(r.intent(intent(2,64,9),4));
    auto first=r.takeDispatch(5);assert(first&&first->key==56);
    assert(r.intent(intent(1,56,3),6));assert(r.queuedDiscrete()==1);
    r.receiveAck(ack(*first,3),7);auto feedback=r.takeFeedback(7);assert(feedback&&feedback->kind==2&&feedback->a==(56|(1<<7)));
    auto second=r.takeDispatch(8);assert(second&&second->key==64&&second->value==9);
    r.receiveAck(ack(*second,0),9);
    auto parameter=r.takeDispatch(10);assert(parameter&&parameter->key==0);
    r.receiveAck(ack(*parameter,90),11);
    assert(r.intent(intent(2,64,9),12));assert(!r.pending());assert(!r.takeDispatch(12));assert(r.duplicateIntents==2);
}
static void bounded_overflow() {
    Reconciler r;ready(r);
    for(int n=0;n<16;++n) assert(r.intent(intent(n,56,1),n));
    assert(!r.intent(intent(16,56,1),17));assert(r.queuedDiscrete()==16);
    auto rejection=r.takeFeedback(18);assert(rejection&&rejection->kind==2&&rejection->a==(56|(16<<7))&&rejection->b==16383);
    for(int n=0;n<16;++n) {auto d=r.takeDispatch(20+n);assert(d&&d->key==56);r.receiveAck(ack(*d,1),20+n);}
    auto results=drain(r,40);assert(results.size()==16);assert(!r.pending());assert(r.rejectedIntents==1);
    for(int n=0;n<16;++n) assert(results[size_t(n)].a==uint32_t(56|(n<<7)));

    // Completed-but-unsent admitted results reserve their capacity. Rejection
    // remains separate and can never evict one of those admitted results.
    Reconciler pressure;ready(pressure);
    for(int n=0;n<48;++n) {assert(pressure.intent(intent(n,56,1),n));auto d=pressure.takeDispatch(n);assert(d);pressure.receiveAck(ack(*d,1),n);}
    assert(!pressure.intent(intent(48,56,1),49));assert(pressure.terminalResults()==48);
    auto all=drain(pressure,50);assert(all.size()==49);for(int n=0;n<48;++n) assert(all[size_t(n)].b==1);assert(all.back().b==16383);

    Reconciler duplicates;ready(duplicates);
    assert(duplicates.intent(intent(0,56,1),0));auto done=duplicates.takeDispatch(0);duplicates.receiveAck(ack(*done,1),1);
    for(int n=1;n<17;++n) assert(duplicates.intent(intent(n,56,1),n));
    for(int n=0;n<1000;++n) assert(duplicates.intent(intent(0,56,1),20));
    assert(duplicates.terminalResults()==48); //48results+16reservedfutureACKs
    for(int n=1;n<17;++n) {auto d=duplicates.takeDispatch(30+n);assert(d);duplicates.receiveAck(ack(*d,1),30+n);}
    auto retained=drain(duplicates,60);assert(retained.size()==64);
    for(int n=1;n<17;++n) assert(std::any_of(retained.begin(),retained.end(),[n](Feedback f){return f.kind==2&&f.a==uint32_t(56|(n<<7));}));
}
static void timeout_no_replay_and_quarantine() {
    Reconciler r;ready(r);assert(r.intent(intent(0,64,4),0));auto old=r.takeDispatch(0);assert(old);
    assert(!r.takeDispatch(2000));assert(!r.pending());assert(r.timedOutCommands==1);
    auto failed=r.takeFeedback(2000);assert(failed&&failed->kind==2&&failed->b==16383);
    assert(!r.takeDispatch(2001));assert(r.intent(intent(1,0,50),2002));auto newer=r.takeDispatch(2003);assert(newer&&newer->seq!=old->seq);
    r.receiveAck(ack(*old,0),2004);assert(r.inflightSeq()==newer->seq);r.receiveAck(ack(*newer,50),2005);
    r.reset(2,2010);r.handshake(2,2011);assert(!r.takeDispatch(2012));assert(!r.intent(intent(2,64,4,1),2013));

    Reconciler ids;ready(ids);
    for(int n=0;n<128;++n) {
        assert(ids.intent(intent(n&127,56,1),n*2001));auto d=ids.takeDispatch(n*2001);assert(d);assert(d->seq==n);
        assert(!ids.takeDispatch(n*2001+2000));drain(ids,n*2001+2000);
    }
    assert(ids.intent(intent(0,56,1),260000));assert(!ids.takeDispatch(260001));assert(!ids.liveReady);
}
static void reset_and_reseed() {
    Reconciler r;ready(r);
    r.receiveObservation({1,0,3,0,0x812345},2);drain(r,2);
    assert(r.intent(intent(0,0,70),3));auto parameter=r.takeDispatch(4);assert(parameter);
    assert(r.intent(intent(1,64,3),5));
    r.reseed(6);auto seed=r.takeFeedback(6);assert(seed&&seed->kind==0&&seed->a==1&&seed->epoch==1);
    assert(r.liveReady&&r.inflightSeq()==parameter->seq&&r.queuedDiscrete()==1);
    auto cached=r.takeFeedback(6);assert(cached&&cached->kind==3&&cached->b==0x812345);
    r.receiveAck(ack(*parameter,70),7);auto record=r.takeDispatch(8);assert(record&&record->key==64);
    assert(r.intent(intent(2,1,90),9));
    r.reset(2,10);r.handshake(1,11);assert(!r.liveReady);r.handshake(2,12);
    assert(!r.takeDispatch(13)); // neither discrete nor old-generation parameter replay
    assert(r.desired(1)==90);assert(r.confirmed(0)==70);
    assert(r.intent(intent(0,1,95,2),13));
    auto after=r.takeDispatch(13);assert(after&&after->generation==2&&after->key==1&&after->value==95);
    r.receiveAck(ack(*record,0),14);assert(r.inflightSeq()==after->seq);
    r.receiveAck(ack(*after,95),15);assert(!r.takeDispatch(16));
    auto feedback=drain(r,16);assert(feedback[0].kind==0&&feedback[0].a==0&&feedback[0].epoch==2);
}
static void observations_and_priorities() {
    Reconciler r;ready(r);
    r.receiveObservation({1,126,1,0,40},1);
    r.receiveObservation({1,127,3,1,0x123456},2);
    r.receiveObservation({1,0,4,uint32_t(8|(1024<<4)),512},3);
    r.receiveObservation({1,127,1,0,3},4);assert(r.confirmed(0)==40);
    r.receiveObservation({2,1,1,0,9},4);assert(r.confirmed(0)==40);
    r.receiveObservation({1,1,1,0,41},5);
    r.receiveObservation({1,2,1,0,42},6);
    assert(r.intent(intent(0,56,1),7));auto d=r.takeDispatch(7);assert(d);r.receiveAck(ack(*d,1),8);
    auto first=r.takeFeedback(8);assert(first&&first->kind==2);auto rest=drain(r,8);assert(rest.size()==3);
    auto param=std::find_if(rest.begin(),rest.end(),[](Feedback f){return f.kind==1;});assert(param!=rest.end()&&param->b==42);
    assert(r.lastLiveAt==8);
    r.receiveObservation({1,3,1,0,42},9);assert(drain(r,9).empty());
    r.reseed(10);auto seed=r.takeFeedback(10);assert(seed&&seed->kind==0&&seed->a==0);assert(drain(r,10).size()==3);

    int prior=-1;
    for(int n=0;n<8200;++n) {
        r.receiveObservation({1,(4+n)&127,1,0,uint32_t(n%128)},11+n);
        auto f=r.takeFeedback(11+n);assert(f);if(prior>=0) assert(((f->revision-prior)&8191)==1);prior=f->revision;
    }
}
static void acknowledged_state_updates_already_dirty_snapshots() {
    Reconciler r;ready(r);
    const uint32_t colour=0x134567;
    r.receiveObservation({1,0,3,0,colour},1); // old unmuted group is still dirty
    assert(r.intent(intent(0,56,1),2));auto mute=r.takeDispatch(2);assert(mute);
    r.receiveAck(ack(*mute,1),3);
    auto done=r.takeFeedback(3);assert(done&&done->kind==2&&done->b==1);
    auto group=r.takeFeedback(3);assert(group&&group->kind==3&&group->a==0&&group->b==(colour|(1u<<23)));
    assert(r.intent(intent(1,56,1),4));auto unmute=r.takeDispatch(4);r.receiveAck(ack(*unmute,0),5);
    done=r.takeFeedback(5);group=r.takeFeedback(5);assert(done&&done->kind==2&&group&&group->b==colour);

    r.receiveObservation({1,1,6,1,uint32_t(4|(4<<7)|(1<<14))},6);
    assert(r.intent(intent(2,113,127),7));auto click=r.takeDispatch(7);r.receiveAck(ack(*click,127),8);
    done=r.takeFeedback(8);auto meter=r.takeFeedback(8);assert(done&&done->kind==2&&meter&&meter->kind==6&&(meter->b&(1<<15)));
    r.receiveObservation({1,2,6,1,uint32_t(4|(4<<7)|(1<<14)|(1<<15))},9);
    assert(r.intent(intent(3,114,7),10));auto numerator=r.takeDispatch(10);r.receiveAck(ack(*numerator,7),11);
    done=r.takeFeedback(11);auto parameter=r.takeFeedback(11);meter=r.takeFeedback(11);
    assert(done&&done->kind==2);assert(parameter&&parameter->kind==1&&parameter->a==114&&parameter->b==7);
    assert(meter&&meter->kind==6&&(meter->b&127)==7&&((meter->b>>7)&127)==4);
}
static void prehandshake_facts_cannot_poison_a_new_session() {
    Reconciler fresh;fresh.reset(1,0);
    fresh.receiveObservation({1,90,1,0,12},1);
    fresh.receiveObservation({1,91,3,0,uint32_t(1u<<21)},1);
    assert(fresh.confirmed(0)==-1);
    auto seed=fresh.takeFeedback(1);assert(seed&&seed->kind==0);
    assert(!fresh.takeFeedback(1));
    fresh.handshake(1,2);
    fresh.receiveObservation({1,0,1,0,70},3);
    assert(fresh.confirmed(0)==70);
    auto actual=fresh.takeFeedback(3);assert(actual&&actual->kind==1&&actual->b==70);

    // Same-process reconnect may retain historical facts, but even an equal
    // generation must await Native's new-session reset before accepting facts.
    fresh.reset(1,4);
    fresh.receiveObservation({1,50,1,0,3},5);
    assert(fresh.confirmed(0)==70);
    fresh.handshake(1,6);
    fresh.receiveObservation({1,0,1,0,95},7);
    assert(fresh.confirmed(0)==95); // oldseq50 did not establish revision baseline
    assert(fresh.ignoredReplies==3);
}
static void group_availability_waits_for_field_delivery() {
    constexpr uint32_t available=1u<<21;
    Reconciler early;ready(early);
    early.receiveObservation({1,0,3,0,available|12},1);
    auto group=early.takeFeedback(1);assert(group&&group->kind==3&&!(group->b&available));
    const int order[]={5,0,4,1,3};
    for(int n=0;n<5;++n) early.receiveObservation({1,n+1,1,uint32_t(order[n]),64},2+n);
    for(auto f:drain(early,8)) assert(f.kind!=3||!(f.b&available));
    early.receiveObservation({1,6,1,2,128},9); // unavailable binding still initializes its field
    assert(early.confirmed(2)==128);
    bool sentinel=false,released=false;
    for(auto f:drain(early,10)) {
        if(f.kind==1&&f.a==2) {assert(f.b==128);sentinel=true;}
        if(f.kind==3&&(f.b&available)) {assert(sentinel);released=true;}
    }
    assert(released);

    // All values being cached is insufficient. This fresh model may publish
    // group availability only after it has actually handed off6 SDK facts.
    Reconciler cached;ready(cached);
    for(int key=0;key<6;++key) cached.receiveObservation({1,key,1,uint32_t(key),70},key);
    cached.receiveObservation({1,6,3,0,available},6);
    int delivered=0;
    for(auto f:drain(cached,7)) {if(f.kind==1) ++delivered;if(f.kind==3&&(f.b&available)) assert(delivered==6);}
    assert(delivered==6);
    cached.reseed(8);auto seed=cached.takeFeedback(8);assert(seed&&seed->kind==0);
    delivered=0;bool availableAgain=false;
    for(auto f:drain(cached,9)) {if(f.kind==1) ++delivered;if(f.kind==3&&(f.b&available)) {assert(delivered==6);availableAgain=true;}}
    assert(availableAgain&&delivered==6);

    // An SDK failure of the final field revokes its delivery bit until retry.
    Reconciler failure;ready(failure);
    for(int key=0;key<5;++key) failure.receiveObservation({1,key,1,uint32_t(key),60},key);
    failure.receiveObservation({1,5,3,0,available},5);drain(failure,6);
    failure.receiveObservation({1,6,1,5,128},7);
    auto last=failure.takeFeedback(7);assert(last&&last->kind==1&&last->a==5);
    assert(failure.feedbackFailed(*last));
    bool fieldRetried=false,releaseAfterRetry=false;
    for(auto f:drain(failure,8)) {
        if(f.kind==1&&f.a==5) {assert(f.b==128);fieldRetried=true;}
        if(f.kind==3&&(f.b&available)) {assert(fieldRetried);releaseAfterRetry=true;}
    }
    assert(fieldRetried&&releaseAfterRetry);

    Reconciler absent;ready(absent);
    absent.receiveObservation({1,0,3,4,13},1);
    auto blank=absent.takeFeedback(1);assert(blank&&blank->kind==3&&blank->a==4&&!(blank->b&available));
    assert(!absent.takeFeedback(1)); // unavailable groups are not blocked waiting for nonexistent fields

    Reconciler invalid;ready(invalid);
    invalid.receiveObservation({1,0,1,114,128},1);assert(invalid.confirmed(114)==-1);
    invalid.receiveObservation({1,1,1,0,129},2);assert(invalid.confirmed(0)==-1);
    assert(!invalid.intent(intent(0,0,128),3));
    assert(invalid.intent(intent(0,0,100),4));auto d=invalid.takeDispatch(4);
    invalid.receiveAck(ack(*d,128),5);assert(invalid.pending()); //128 is an observation sentinel, never a value ACK
}
static void failed_sdk_feedback_is_retained_without_native_replay() {
    Reconciler seed;seed.reset(1,0);seed.handshake(1,0);
    auto first=seed.takeFeedback(0);assert(first&&first->kind==0);
    assert(seed.feedbackFailed(*first));auto retry=seed.takeFeedback(1);
    assert(retry&&retry->kind==0&&retry->a==127&&retry->revision!=first->revision);
    assert(!seed.feedbackFailed(*first)); // only the issued SDK result may fail

    Reconciler parameterModel;ready(parameterModel);
    assert(parameterModel.intent(intent(0,0,20),1));auto command=parameterModel.takeDispatch(1);parameterModel.receiveAck(ack(*command,20),2);
    auto result=parameterModel.takeFeedback(2);assert(result&&result->kind==2);
    assert(parameterModel.feedbackFailed(*result));auto again=parameterModel.takeFeedback(3);
    assert(again&&again->kind==2&&again->a==0&&again->b==20);
    assert(parameterModel.dispatched==1);assert(!parameterModel.takeDispatch(3));
    assert(parameterModel.intent(intent(1,0,80),4));
    assert(!parameterModel.feedbackFailed(*again)); // a newer intent owns this field
    for(auto f:drain(parameterModel,4)) assert(f.kind!=2);

    Reconciler fact;ready(fact);
    fact.receiveObservation({1,0,4,uint32_t(8|(1024<<4)),12},1);
    auto oldClock=fact.takeFeedback(1);assert(oldClock&&oldClock->kind==4);
    fact.receiveObservation({1,1,4,uint32_t(8|(2048<<4)),123},2);
    assert(fact.feedbackFailed(*oldClock));auto newClock=fact.takeFeedback(3);
    assert(newClock&&newClock->a==uint32_t(8|(2048<<4))&&newClock->b==123);
    fact.reset(2,4);assert(!fact.feedbackFailed(*newClock));

    Reconciler discrete;ready(discrete);
    assert(discrete.intent(intent(0,56,3),0));auto mutation=discrete.takeDispatch(0);discrete.receiveAck(ack(*mutation,3),1);
    auto success=discrete.takeFeedback(1);assert(success&&success->kind==2);
    // A Native reply and duplicate traffic may fill normal slots after SDK
    // dequeue but before send failure. The dedicated retry slot preserves it.
    for(int n=1;n<49;++n) {assert(discrete.intent(intent(n,56,1),n));auto d=discrete.takeDispatch(n);discrete.receiveAck(ack(*d,1),n);}
    assert(discrete.feedbackFailed(*success));assert(discrete.terminalResults()==49);
    auto retried=discrete.takeFeedback(50);assert(retried&&retried->a==success->a&&retried->b==3);
    auto preserved=drain(discrete,50);assert(preserved.size()==48);
    assert(discrete.dispatched==49);assert(!discrete.takeDispatch(51));

    Reconciler rejection;ready(rejection);
    for(int n=0;n<16;++n) assert(rejection.intent(intent(n,56,1),n));
    assert(!rejection.intent(intent(16,56,1),17));
    auto rejectedResult=rejection.takeFeedback(17);assert(rejectedResult&&rejectedResult->b==16383);
    assert(rejection.feedbackFailed(*rejectedResult));auto rejectedAgain=rejection.takeFeedback(18);
    assert(rejectedAgain&&rejectedAgain->a==rejectedResult->a&&rejectedAgain->b==16383);
    assert(rejection.queuedDiscrete()==16);assert(rejection.dispatched==0);
}
static void patch_intents_keep_latest_and_precede_recording() {
    Reconciler r;ready(r);
    assert(valid(intent(0,48,8191)));assert(!valid(intent(0,48,8192)));
    assert(valid(intent(0,57,7)));assert(!valid(intent(0,57,8)));
    assert(r.intent(intent(0,48,1),2));auto initial=r.takeDispatch(3);assert(initial&&initial->key==48);
    assert(r.intent(intent(1,48,3),4));assert(r.intent(intent(2,48,2),5));
    assert(r.intent(intent(3,64,10),6));
    r.receiveAck(ack(*initial,1),7);
    for(auto f:drain(r,7)) assert(f.kind!=2);
    auto selected=r.takeDispatch(8);assert(selected&&selected->key==48&&selected->value==2);
    r.receiveAck(ack(*selected,2),9);
    auto begin=r.takeDispatch(10);assert(begin&&begin->key==64);
    r.receiveAck(ack(*begin,0),11);
    assert(!r.pending());assert(r.desired(48)==2);assert(r.confirmed(48)==2);

    // Appended patch/readiness/source facts must not collide with the existing
    // meter parameter, group or clock slots. Routing readiness is not an ACK.
    Reconciler facts;ready(facts);
    facts.receiveObservation({1,0,1,80,3},2);
    facts.receiveObservation({1,1,1,114,7},2);
    facts.receiveObservation({1,2,6,8,19},2);
    facts.receiveObservation({1,3,6,24,0x200123},2);
    facts.receiveObservation({1,4,6,1,516u|(255u<<16)},2);
    facts.receiveObservation({1,5,1,88,8},2); // observation-only: no monitor owner
    auto all=drain(facts,3);
    assert(all.size()==6);assert(facts.confirmed(57)==8);
    bool patch=false,meter=false,pending=false,source=false;
    for(auto f:all) {
        patch|=f.kind==1&&f.a==80&&f.b==3;
        meter|=f.kind==1&&f.a==114&&f.b==7;
        pending|=f.kind==6&&f.a==8&&f.b==19;
        source|=f.kind==6&&f.a==24&&f.b==0x200123;
    }
    assert(patch&&meter&&pending&&source);
    facts.intent(intent(0,57,6),4);auto monitor=facts.takeDispatch(5);assert(monitor&&monitor->key==57);
    facts.receiveAck(ack(*monitor,6),6);
    bool owner=false;for(auto f:drain(facts,7)) owner|=f.kind==6&&f.a==1&&(f.b>>24)==7;
    assert(owner);
}
static void seven_source_metadata_and_patch_only_names() {
    Reconciler r;ready(r);
    int sequence=0;
    for(int source=0;source<7;++source)
        r.receiveObservation({1,sequence++,6,uint32_t(24+source),uint32_t(0x200000+source)},2);
    r.receiveObservation({1,sequence++,1,88,8},2);
    r.receiveObservation({1,sequence++,6,8,65u|(1u<<25)},2);
    for(int chunk=0;chunk<56;++chunk)
        r.receiveObservation({1,sequence++,6,uint32_t(64+chunk),uint32_t(1000+chunk)},2);
    auto main=drain(r,3);
    assert(main.size()==9);assert(!r.pending());
    int colors=0;bool owner=false,readiness=false;
    for(auto f:main) {
        assert(f.kind!=6 || f.a<64 || f.a>=264);
        if(f.kind==6&&f.a>=24&&f.a<31) {++colors;assert(f.b==0x200000+f.a-24);}
        owner|=f.kind==1&&f.a==88&&f.b==8;
        readiness|=f.kind==6&&f.a==8&&f.b==(65u|(1u<<25));
    }
    assert(colors==7&&owner&&readiness);
    // Coalesce while the modal is closed. New labels must neither keep main
    // busy nor obscure performance feedback, and deliver the latest word only.
    r.receiveObservation({1,sequence++,6,119,99999},4);
    assert(drain(r,4).empty());assert(!r.pending());
    auto names=drain(r,5,true);assert(names.size()==56);
    for(auto f:names) {assert(f.kind==6&&f.a>=64&&f.a<120);assert(f.b==(f.a==119?99999u:1000u+f.a-64));}
    assert(drain(r,6,true).empty());
    r.reseed(7);auto reopened=drain(r,7,true);
    int count=0;for(auto f:reopened) if(f.kind==6&&f.a>=64&&f.a<120) ++count;
    assert(count==56);
    // A failed SDK label send is retained, but remains patch-only on retry.
    r.reseed(8);auto next=drain(r,8,true);Feedback name;
    for(auto f:next) if(f.kind==6&&f.a==119) name=f;
    assert(r.feedbackFailed(name));assert(drain(r,9).empty());
    auto retry=drain(r,9,true);assert(retry.size()==1&&retry[0].a==119&&retry[0].b==99999);
}

static void allocation_busy_admission_and_drain() {
    assert(valid(intent(0,58,1)));assert(valid(intent(0,58,16382)));
    assert(!valid(intent(0,58,0)));assert(!valid(intent(0,58,16383)));
    Reconciler r;ready(r);
    assert(r.intent(intent(0,0,20),2));auto first=r.takeDispatch(3);assert(first&&first->key==0);
    r.receiveObservation({1,0,6,3,777},4);
    assert(r.allocationRequested()&&!r.allocationAdmissionClosed(4)&&r.allocationProtectsHealth(4));
    // Work from a gesture already in progress remains admitted until the pad's
    // actual painted/no-contact receipt arrives, not merely until host send.
    assert(r.intent(intent(1,0,90),5));assert(r.intent(intent(2,56,3),6));
    assert(r.intent(intent(3,58,777),7));assert(r.allocationAdmissionClosed(7));
    assert(!r.intent(intent(4,0,110),8));assert(!r.intent(intent(5,64,22),8));
    assert(r.desired(0)==90&&r.queuedDiscrete()==1);
    assert(!r.takeDispatch(9));r.receiveAck(ack(*first,20),10);
    auto mute=r.takeDispatch(11);assert(mute&&mute->key==56);r.receiveAck(ack(*mute,3),12);
    auto value=r.takeDispatch(13);assert(value&&value->key==0&&value->value==90);r.receiveAck(ack(*value,90),14);
    auto grant=r.takeDispatch(15);assert(grant&&grant->key==58&&grant->value==777&&grant->deviceSeq==3);
    r.receiveAck(ack(*grant,777),16);assert(!r.takeDispatch(17));
    assert(r.intent(intent(3,58,777),18));assert(!r.takeDispatch(18)); // replay ACK, never another allocation
    assert(!r.intent(intent(6,58,777),19)); // another device sequence is not another grant
    auto feedback=r.takeFeedback(20);assert(feedback&&feedback->kind==6&&feedback->a==3&&feedback->b==777);
    int rejectedCount=0;
    for(auto f:drain(r,20)) if(f.kind==7&&f.b==16383) ++rejectedCount;
    assert(rejectedCount==3);
    r.receiveObservation({1,1,6,3,0},21);
    assert(!r.allocationRequested()&&!r.allocationAdmissionClosed(21));
    auto clear=r.takeFeedback(21);assert(clear&&clear->kind==6&&clear->a==3&&clear->b==0);
    assert(r.intent(intent(7,1,80),22));auto next=r.takeDispatch(23);assert(next&&next->key==1);
}
static void allocation_busy_capacity_reseed_and_stale_receipts() {
    Reconciler r;ready(r);
    r.receiveObservation({2,0,6,3,999},2);assert(!r.allocationRequested());
    r.receiveObservation({1,0,6,3,999},3);
    assert(!r.intent(intent(0,58,998),4));assert(!r.allocationAdmissionClosed(4));
    assert(!r.intent(intent(1,58,999,2),5));assert(!r.allocationAdmissionClosed(5));
    // Its dedicated slot is available even when the ordinary command queue is full.
    for(int n=1;n<=16;++n) assert(r.intent(intent(n,56,1),6+n));
    assert(r.intent(intent(17,58,999),23));assert(r.queuedDiscrete()==16);
    r.reseed(24);auto seed=r.takeFeedback(24);assert(seed&&seed->kind==0);
    auto busy=r.takeFeedback(24);assert(busy&&busy->kind==6&&busy->a==3&&busy->b==999);
    assert(r.feedbackFailed(*busy));auto retry=r.takeFeedback(25);assert(retry&&retry->kind==6&&retry->b==999);
    // A Native cancellation unlocks the pad and cancels only the internal grant.
    r.receiveObservation({1,1,6,3,0},26);
    assert(r.feedbackFailed(*retry));auto clear=r.takeFeedback(27);assert(clear&&clear->kind==6&&clear->a==3&&clear->b==0);
    for(int n=0;n<16;++n) {auto d=r.takeDispatch(28+n);assert(d&&d->key==56);r.receiveAck(ack(*d,1),28+n);}
    assert(!r.takeDispatch(50));assert(!r.allocationRequested());
    r.receiveObservation({1,2,6,3,777},51);
    r.reset(2,52);r.handshake(2,53);
    for(auto f:drain(r,54)) assert(f.kind!=6||f.a!=3); // no historical grant request in a fresh session
    assert(!r.allocationRequested());assert(!r.intent(intent(0,58,777,2),55));
}
static void allocation_busy_lease_is_bounded_and_fail_closed() {
    Reconciler r;ready(r);r.receiveObservation({1,0,6,3,22},100);
    assert(r.allocationProtectsHealth(10099));assert(!r.allocationProtectsHealth(10100));
    r.receiveObservation({1,1,6,3,22},10099); // ungranted repeated requests never extend the initial wait
    assert(!r.intent(intent(0,58,22),10100));assert(!r.takeDispatch(10101));
    assert(r.allocationAdmissionClosed(10101));assert(!r.intent(intent(1,56,1),10102));
    r.receiveObservation({1,2,6,3,0},10103);assert(!r.allocationAdmissionClosed(10104));
    assert(r.intent(intent(2,56,1),10105));auto d=r.takeDispatch(10106);assert(d&&d->key==56);
    Reconciler pending;ready(pending);pending.receiveObservation({1,0,6,3,33},100);
    assert(pending.intent(intent(0,56,1),101));assert(pending.intent(intent(1,58,33),102));
    auto command=pending.takeDispatch(103);assert(command&&command->key==56);
    pending.receiveAck(ack(*command,1),104);
    assert(!pending.takeDispatch(10100)); // never forward a late grant after expiry
    pending.receiveObservation({1,1,6,3,16383},10101);assert(pending.allocationRequested());
}


static void allocation_busy_bundle_heartbeats_preserve_one_grant() {
    Reconciler r;ready(r);r.receiveObservation({1,0,6,3,88},100);
    assert(r.intent(intent(0,58,88),101));
    auto grant=r.takeDispatch(102);assert(grant&&grant->key==58&&grant->value==88);
    r.receiveAck(ack(*grant,88),103);drain(r,104);
    assert(!r.intent(intent(1,56,1),105));drain(r,106);
    assert(r.intent(intent(0,58,88),107));drain(r,108); // duplicate receipt cannot allocate again
    for(int sequence=1;sequence<=30;++sequence) {
        const double at=100+sequence*500;
        r.receiveObservation({1,sequence,6,3,88},at);
        assert(r.allocationAdmissionClosed(at)&&r.allocationProtectsHealth(at));
        assert(!r.takeDispatch(at));
        assert(!r.takeFeedback(at)); // liveness refresh is not a repeated device overlay packet
    }
    assert(r.dispatched==1&&r.allocationRequested());
    const double last=15100;
    assert(r.allocationProtectsHealth(last+9999));
    // Neither stale session/revision nor unrelated host health can refresh it.
    r.receiveObservation({2,31,6,3,88},last+9900);
    r.receiveObservation({1,30,6,3,88},last+9901);
    r.receiveObservation({1,29,6,3,88},last+9902);
    assert(r.allocationExpired(last+10000));
    r.receiveObservation({1,31,6,3,88},last+10001); // too late: no resurrection
    assert(r.allocationExpired(last+10002)&&!r.allocationProtectsHealth(last+10002));
    assert(!r.takeDispatch(last+10002));
    assert(!r.intent(intent(2,0,70),last+10003));
    r.receiveObservation({1,32,6,3,0},last+10004);
    assert(!r.allocationRequested()&&!r.allocationAdmissionClosed(last+10004));
}


static void mixed_patch_banks_coalesce_without_erasing_each_other() {
    assert(valid(intent(0,48,8191)));assert(!valid(intent(0,48,8192)));
    assert(valid(intent(0,116,4095)));assert(!valid(intent(0,116,4096)));
    Reconciler r;ready(r);
    r.receiveObservation({1,0,1,80,3u|(5u<<13)},2);
    assert(r.confirmed(48)==3&&r.confirmed(116)==5);
    assert(r.intent(intent(0,48,7),3));auto low=r.takeDispatch(4);assert(low&&low->key==48);
    assert(r.intent(intent(1,116,9),5));assert(r.intent(intent(2,48,11),6));
    assert(r.intent(intent(3,116,13),7));assert(r.intent(intent(4,64,10),8));
    r.receiveAck(ack(*low,7),9);
    auto nextLow=r.takeDispatch(10);assert(nextLow&&nextLow->key==48&&nextLow->value==11);
    r.receiveAck(ack(*low,7),11);assert(r.inflightSeq()==nextLow->seq);
    r.receiveAck(ack(*nextLow,11),12);
    auto high=r.takeDispatch(13);assert(high&&high->key==116&&high->value==13);
    r.receiveAck(ack(*high,13),14);
    auto begin=r.takeDispatch(15);assert(begin&&begin->key==64);
    assert(r.desired(48)==11&&r.desired(116)==13&&r.confirmed(48)==11&&r.confirmed(116)==13);
    bool full=false,latestLow=false,latestHigh=false;
    for(auto f:drain(r,16)) {
        assert(!(f.kind==1&&f.a>=116&&f.a<=123));
        full|=f.kind==1&&f.a==80&&f.b==(11u|(13u<<13));
        latestLow|=f.kind==2&&f.a==uint32_t(48|(2<<7))&&f.b==11;
        latestHigh|=f.kind==2&&f.a==uint32_t(116|(3<<7))&&f.b==13;
        assert(!(f.kind==2&&f.a==48)); // superseded low ACK does not release a new retap
    }
    assert(full&&latestLow&&latestHigh);
    r.receiveAck(ack(*begin,0),17);
    assert(!r.pending());
}

static void mixed_patch_bank_rejection_and_native_reconciliation() {
    Reconciler r;ready(r);r.receiveObservation({1,0,1,80,0x1ffffffu},2);drain(r,2);
    assert(r.confirmed(48)==8191&&r.confirmed(116)==4095);
    assert(r.intent(intent(0,48,0),3));assert(r.intent(intent(1,116,0),4));
    auto low=r.takeDispatch(5);assert(low&&low->key==48);r.receiveAck(ack(*low,0),6);
    bool retainedHigh=false;
    for(auto f:drain(r,6)) retainedHigh|=f.kind==1&&f.a==80&&f.b==(4095u<<13);
    assert(retainedHigh);
    auto oldHigh=r.takeDispatch(7);assert(oldHigh&&oldHigh->key==116);
    assert(r.intent(intent(2,116,2),8));assert(r.intent(intent(3,48,5),9));
    r.receiveAck(ack(*oldHigh,0),10);
    auto nextLow=r.takeDispatch(11);assert(nextLow&&nextLow->key==48&&nextLow->value==5);
    r.receiveAck(ack(*nextLow,5),12);
    auto nextHigh=r.takeDispatch(13);assert(nextHigh&&nextHigh->key==116&&nextHigh->value==2);
    r.receiveAck(ack(*nextHigh,rejected),14);
    bool full=false,rejectedHigh=false;
    for(auto f:drain(r,15)) {
        full|=f.kind==1&&f.a==80&&f.b==5;
        rejectedHigh|=f.kind==2&&f.a==uint32_t(116|(2<<7))&&f.b==uint32_t(rejected);
        assert(!(f.kind==2&&f.a==uint32_t(116|(1<<7))));
    }
    assert(full&&rejectedHigh&&r.confirmed(48)==5&&r.confirmed(116)==0);
    r.receiveObservation({1,1,1,80,5u|(4u<<13)},16); // manual Native change remains authoritative
    r.receiveAck(ack(*oldHigh,0),17);
    auto actual=drain(r,18);assert(actual.size()==1&&actual[0].b==(5u|(4u<<13)));
    assert(r.confirmed(48)==5&&r.confirmed(116)==4&&!r.pending());
}

static void mixed_catalog_metadata_labels_and_clock_priority() {
    Reconciler r;ready(r);int revision=0;
    const uint32_t midiOwned=0x123456u|(1u<<21)|(1u<<22)|(7u<<23)|(1u<<26);
    for(int source=0;source<25;++source)
        r.receiveObservation({1,(revision++)&127,6,uint32_t(24+source),source==24?midiOwned:uint32_t(source)},2);
    for(int chunk=0;chunk<200;++chunk)
        r.receiveObservation({1,(revision++)&127,6,uint32_t(64+chunk),uint32_t(2000+chunk)},2);
    r.receiveObservation({1,(revision++)&127,6,8,0x1ffffffu|(1u<<25)|(1u<<26)},3);
    r.receiveObservation({1,(revision++)&127,1,88,8},3);
    r.receiveObservation({1,(revision++)&127,1,80,1u<<24},3);
    auto ordinary=drain(r,3);assert(ordinary.size()==28);
    bool owner=false,prepared=false,fullAssignment=false;
    for(auto f:ordinary) {
        assert(!(f.kind==6&&f.a>=64&&f.a<264));
        owner|=f.kind==6&&f.a==48&&f.b==midiOwned;
        prepared|=f.kind==6&&f.a==8&&f.b==0x7ffffffu;
        fullAssignment|=f.kind==1&&f.a==80&&f.b==(1u<<24);
    }
    assert(owner&&prepared&&fullAssignment&&!r.pending());
    auto firstName=r.takeFeedback(4,true);assert(firstName&&firstName->kind==6&&firstName->a>=64);
    r.receiveObservation({1,(revision++)&127,4,uint32_t(8|(1024<<4)),111},5);
    auto clock=r.takeFeedback(5,true);assert(clock&&clock->kind==4&&clock->b==111);
    r.receiveObservation({1,(revision++)&127,6,3,123},6);
    auto busy=r.takeFeedback(6,true);assert(busy&&busy->kind==6&&busy->a==3&&busy->b==123);
    assert(r.intent(intent(0,123,4095),7));assert(r.intent(intent(1,58,123),8));
    auto high=r.takeDispatch(9);assert(high&&high->key==123);r.receiveAck(ack(*high,4095),10);
    auto grant=r.takeDispatch(11);assert(grant&&grant->key==58);
    r.receiveAck(ack(*grant,123),12);assert(!r.intent(intent(2,123,4095),13));
    r.receiveObservation({1,(revision++)&127,6,3,0},14);drain(r,14);
    auto names=drain(r,15,true);assert(names.size()==199);
    r.reseed(16);auto seed=r.takeFeedback(16,true);assert(seed&&seed->kind==0);
    auto retained=drain(r,16,true);int count=0;
    for(auto f:retained) if(f.kind==6&&f.a>=64&&f.a<264) ++count;
    assert(count==200);
}

static void constants_bank_frames_and_isolation() {
    assert(wireChannel(0)==1&&wireChannel(128)==2&&wireKey(151)==23);
    assert(valid(intent(0,128,0)));assert(valid(intent(0,151,127)));
    assert(!valid(intent(0,128,128)));assert(!valid(intent(0,152,1)));
    assert(valid(intent(0,56,4095)));assert(!valid(intent(0,56,4096)));
    IntentFrame input;
    // Banks are independent frame contexts, never parts of one hybrid frame.
    const int low[][2]={{69,3},{70,7},{71,0},{72,17},{73,0}};
    const int high[][2]={{69,3},{70,8},{71,23},{72,99},{73,0}};
    for(int i=0;i<5;++i) {assert(!input.push(1,low[i][0],low[i][1]));assert(!input.push(2,high[i][0],high[i][1]));}
    auto original=input.push(1,74,7);assert(original&&original->key==0&&original->value==17);
    auto constant=input.push(2,74,8);assert(constant&&constant->key==151&&constant->value==99);
    // A bank2 key outside its24 fields must not become a global command.
    const int bad[][2]={{69,3},{70,9},{71,56},{72,1},{73,0},{74,9}};
    for(auto part:bad) assert(!input.push(2,part[0],part[1]));
    assert(!input.push(2,69,3));assert(!input.push(2,70,10));
    assert(!input.push(1,71,0));assert(!input.push(1,72,70));assert(!input.push(1,73,0));assert(!input.push(1,74,10));
    assert(!input.push(2,71,0));assert(!input.push(2,72,50));assert(!input.push(2,73,0));
    auto separate=input.push(2,74,10);assert(separate&&separate->key==128&&separate->value==50);

    AckFrame acks;
    const int ack1[][2]={{69,3},{75,11},{76,0},{77,20},{78,0}};
    const int ack2[][2]={{69,3},{75,12},{76,0},{77,30},{78,0}};
    for(int i=0;i<5;++i) {assert(!acks.push(1,ack1[i][0],ack1[i][1]));assert(!acks.push(2,ack2[i][0],ack2[i][1]));}
    auto first=acks.push(1,79,11);assert(first&&first->key==0&&first->value==20);
    auto second=acks.push(2,79,12);assert(second&&second->key==128&&second->value==30);
}
static void constants_coalescing_and_feedback_retries() {
    Reconciler r;ready(r);
    assert(r.intent(intent(0,0,17),2));assert(r.intent(intent(1,128,20),3));
    auto loop=r.takeDispatch(4);assert(loop&&loop->key==0);r.receiveAck(ack(*loop,17),5);drain(r,5);
    auto old=r.takeDispatch(6);assert(old&&old->key==128&&old->value==20);
    // Same low key/sequence on the wrong wire bank cannot acknowledge it.
    r.receiveAck({old->generation,old->seq,0,20},7);assert(r.inflightSeq()==old->seq);
    assert(r.intent(intent(2,128,80),8));r.receiveAck(ack(*old,20),9);
    for(auto f:drain(r,9)) assert(f.kind!=2);
    auto last=r.takeDispatch(10);assert(last&&last->key==128&&last->value==80);
    r.receiveAck(ack(*last,80),11);auto response=r.takeFeedback(11);
    assert(response&&response->kind==2&&response->a==uint32_t((2<<7)|(1<<14))&&response->b==80);
    assert(r.feedbackFailed(*response));auto retry=r.takeFeedback(12);assert(retry&&retry->a==response->a&&retry->b==80);
    assert(!r.takeDispatch(12));assert(r.confirmed(0)==17&&r.confirmed(128)==80);
    assert(r.intent(intent(3,128,81),13));auto newer=r.takeDispatch(14);assert(newer&&newer->key==128);
    assert(!r.feedbackFailed(*response));r.receiveAck(ack(*newer,81),15);drain(r,15);
    assert(r.intent(intent(4,151,127),16));auto highest=r.takeDispatch(17);assert(highest&&highest->key==151);
    r.receiveAck(ack(*highest,127),18);auto top=r.takeFeedback(18);
    assert(top&&top->a==uint32_t(23|(4<<7)|(1<<14))&&top->b==127);
    assert(!r.pending());assert(r.confirmed(151)==127);
}
static void constants_facts_delivery_and_twelve_target_mute() {
    constexpr uint32_t available=1u<<21,muted=1u<<23;
    Reconciler r;ready(r);
    r.receiveObservation({1,0,3,8,available|12},2);
    auto early=r.takeFeedback(2);assert(early&&early->kind==3&&!(early->b&available));
    for(int key=48;key<54;++key) r.receiveObservation({1,key-47,1,uint32_t(key),uint32_t(key==53?128:64)},3);
    bool complete=false;
    for(auto f:drain(r,3)) if(f.kind==3&&f.a==8) {assert(f.b&available);complete=true;}
    assert(complete);assert(r.confirmed(133)==128);
    r.receiveObservation({1,7,3,11,0x13579},4);drain(r,4);
    assert(r.intent(intent(0,56,1|(1<<8)|(1<<11)),5));auto toggle=r.takeDispatch(6);
    assert(toggle&&toggle->key==56&&toggle->value==(1|(1<<8)|(1<<11)));
    r.receiveAck(ack(*toggle,4096),7);assert(r.inflightSeq()==toggle->seq);
    r.receiveAck(ack(*toggle,1|(1<<8)|(1<<11)),7);
    bool first=false,last=false;
    for(auto f:drain(r,7)) if(f.kind==3) {
        if(f.a==8) {assert(f.b&muted);first=true;}
        if(f.a==11) {assert(f.b&muted);last=true;}
    }
    assert(first&&last);
    // Extended mixer facts cannot collide with cached patch names or assignment.
    r.receiveObservation({1,8,1,71,99},8);
    r.receiveObservation({1,9,6,128,1234},8);
    r.receiveObservation({1,10,1,80,3},8);
    auto ordinary=drain(r,9);bool param=false,patch=false;
    for(auto f:ordinary) {param|=f.kind==1&&f.a==71&&f.b==99;patch|=f.kind==1&&f.a==80&&f.b==3;}
    assert(param&&patch);auto label=r.takeFeedback(9,true);assert(label&&label->kind==6&&label->a==128&&label->b==1234);
    r.reseed(10);auto seed=r.takeFeedback(10,true);assert(seed&&seed->kind==0);
    bool availableAgain=false,field=false;
    for(auto f:drain(r,10,true)) {
        if(f.kind==1&&f.a==53) field=true;
        if(f.kind==3&&f.a==8&&(f.b&available)) {assert(field);availableAgain=true;}
    }
    assert(availableAgain);
}

static void compact_parameter_fact_namespace_is_disjoint() {
    Reconciler r;ready(r);int sequence=0;
    for(int a=0;a<72;++a) r.receiveObservation({1,sequence++,1,uint32_t(a),uint32_t(a)},1);
    for(int a=80;a<88;++a) r.receiveObservation({1,sequence++,1,uint32_t(a),0x1ffffffu},1);
    r.receiveObservation({1,sequence++,1,88,8},1);
    r.receiveObservation({1,sequence++,1,114,7},1);
    r.receiveObservation({1,sequence++,1,115,8},1);
    for(int a=0;a<72;++a) assert(r.confirmed(a<48?a:a+80)==a);
    for(int group=0;group<8;++group) assert(r.confirmed(48+group)==8191&&r.confirmed(116+group)==4095);
    assert(r.confirmed(57)==8&&r.confirmed(137)==57);
    auto facts=drain(r,2);assert(facts.size()==83);
    for(uint32_t a: {72u,79u,89u,113u,116u,127u,128u,151u,152u}) {
        const auto before=r.ignoredReplies;
        r.receiveObservation({1,sequence++&127,1,a,0},2);
        assert(r.ignoredReplies==before+1);
    }
    assert(drain(r,3).empty());
}

int main() {
    compact_parameter_fact_namespace_is_disjoint();
    constants_bank_frames_and_isolation();constants_coalescing_and_feedback_retries();constants_facts_delivery_and_twelve_target_mute();
    mixed_patch_banks_coalesce_without_erasing_each_other();mixed_patch_bank_rejection_and_native_reconciliation();mixed_catalog_metadata_labels_and_clock_priority();
    allocation_busy_bundle_heartbeats_preserve_one_grant();
    allocation_busy_admission_and_drain();allocation_busy_capacity_reseed_and_stale_receipts();allocation_busy_lease_is_bounded_and_fail_closed();
    parsers();seven_source_metadata_and_patch_only_names();superseding_parameters();discrete_order_and_deduplication();bounded_overflow();patch_intents_keep_latest_and_precede_recording();
    timeout_no_replay_and_quarantine();reset_and_reseed();observations_and_priorities();acknowledged_state_updates_already_dirty_snapshots();prehandshake_facts_cannot_poison_a_new_session();group_availability_waits_for_field_delivery();failed_sdk_feedback_is_retained_without_native_replay();
    std::cout<<"performance: parser, coalescing, ordering, timeout, reconnect and bounded-model checks passed\n";
}
