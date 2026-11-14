#include "short_long.hpp"
#include <cassert>
int main() {
    using button_hold::Action;
    using health::Program;
    assert(button_hold::destination(Program::main,Action::shortPress)==Program::patch);
    assert(button_hold::destination(Program::main,Action::longPress)==Program::metronome);
    assert(button_hold::destination(Program::patch,Action::shortPress)==Program::main);
    assert(button_hold::destination(Program::patch,Action::longPress)==Program::metronome);
    assert(button_hold::destination(Program::metronome,Action::shortPress)==Program::main);
    assert(button_hold::destination(Program::metronome,Action::longPress)==Program::patch);
    button_hold::Recognizer r;
    r.press(1000); assert(!r.poll(1399));
    assert(r.release(1399)==Action::shortPress); // no double-click wait
    r.press(2000); assert(!r.poll(2399)); assert(r.poll(2400)==Action::longPress);
    assert(!r.poll(2500)); assert(!r.release(2600)); // never both actions
    r.press(3000); assert(r.release(3401)==Action::longPress); // missed timer tick
    r.press(4000); r.press(4100); assert(r.poll(4400)==Action::longPress); // duplicate Down
    r.reset(); assert(!r.release(4500)); assert(!r.poll(5000)); // reconnect cancellation
    r.press(6000); assert(!r.release(6005)); // debounce
    r.press(7000); assert(!r.poll(7399)); r.reset();
    assert(!r.poll(7400)); assert(!r.release(7450)); // Hello before threshold
    r.press(8000); assert(r.release(8050)==Action::shortPress);
    r.press(8100); assert(r.release(8150)==Action::shortPress); // no double-click window
}
