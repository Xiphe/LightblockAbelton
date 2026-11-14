// Approved short/long Mode-button recognition; host helper only.
#pragma once
#include <optional>
#include "health.hpp"

namespace button_hold {
enum class Action { shortPress, longPress };
inline health::Program destination(health::Program current, Action action) {
    if(action==Action::shortPress)
        return current==health::Program::main?health::Program::patch:health::Program::main;
    return current==health::Program::metronome?health::Program::patch:health::Program::metronome;
}
struct Recognizer {
    bool down=false, fired=false;
    double pressedAt=0;
    void reset() { down=false; fired=false; }
    void press(double now) {
        if(down) return;
        down=true; fired=false; pressedAt=now;
    }
    std::optional<Action> poll(double now) {
        if(down && !fired && now-pressedAt>=400) {
            fired=true; return Action::longPress;
        }
        return {};
    }
    std::optional<Action> release(double now) {
        if(!down) return {};
        auto action=poll(now);
        if(!fired && now-pressedAt>=20) action=Action::shortPress;
        down=false;
        return action;
    }
};
}
