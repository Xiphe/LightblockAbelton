// Headless cross-runtime driver for the actual reconciler. No Native/SDK calls.
#include "../native/performance.hpp"
#include <iostream>
#include <sstream>
#include <string>

int main() {
    performance::Reconciler model;
    performance::IntentFrame device;
    std::string line;
    bool patchNames=false;
    while(std::getline(std::cin,line)) {
        std::istringstream input(line);
        std::string command;
        input>>command;
        bool ok=true;
        if(command=="reset") {
            int generation;double at;
            if(input>>generation>>at) {
                device.reset();model.reset(generation,at);
                // This fixture starts with a ready synthetic Native peer.
                model.handshake(generation,at);
            } else ok=false;
        } else if(command=="reseed") {
            double at;if(input>>at) model.reseed(at);else ok=false;
        } else if(command=="handshake") {
            int generation;double at;
            if(input>>generation>>at) model.handshake(generation,at);else ok=false;
        } else if(command=="device") {
            int channel,cc,value;double at;
            if(input>>channel>>cc>>value>>at) {
                if(channel==16 && cc==110 && value==19) model.reseed(at);
                else if(auto intent=device.push(channel,cc,value)) model.intent(*intent,at);
            } else ok=false;
        } else if(command=="ack") {
            performance::Ack ack;double at;
            if(input>>ack.generation>>ack.seq>>ack.key>>ack.value>>at) model.receiveAck(ack,at);else ok=false;
        } else if(command=="fact") {
            performance::Observation fact;double at;
            if(input>>fact.generation>>fact.seq>>fact.kind>>fact.a>>fact.b>>at) model.receiveObservation(fact,at);else ok=false;
        } else if(command=="patch-names") {
            int enabled;if(input>>enabled) patchNames=enabled!=0;else ok=false;
        } else if(command=="pump") {
            double at;
            if(input>>at) {
                if(auto d=model.takeDispatch(at))
                    std::cout<<"D "<<d->generation<<' '<<d->seq<<' '<<d->key<<' '<<d->value<<' '<<d->deviceSeq<<'\n';
                if(auto f=model.takeFeedback(at,patchNames))
                    std::cout<<"F "<<f->kind<<' '<<f->a<<' '<<f->b<<' '<<f->header()<<'\n';
            } else ok=false;
        } else ok=false;
        if(!ok) std::cout<<"ERR\n";
        std::cout<<"E\n"<<std::flush;
    }
}
