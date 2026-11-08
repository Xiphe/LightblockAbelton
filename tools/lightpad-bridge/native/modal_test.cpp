#include "modal.hpp"
#include <cassert>
int main() {
    using modal::Click;
    modal::Clicks c;
    c.press(0); assert(!c.release(50)); assert(!c.poll(349)); assert(c.poll(350)==Click::single); assert(!c.poll(400));
    c.press(500); c.release(550); c.press(700); assert(!c.poll(900)); assert(c.release(750)==Click::doubleClick); assert(!c.poll(1100));
    c.press(1200); c.release(1250); c.press(1550); assert(!c.poll(1800)); assert(c.release(1600)==Click::doubleClick);
    c.press(2000); assert(!c.release(2005)); assert(!c.poll(2400));
    c.press(2500); assert(!c.release(3200)); assert(!c.poll(3600));
    c.press(4000); c.release(4050); c.reset(); assert(!c.poll(4400));
    modal::Touches t;
    int seq=0;
    auto frame=[&](int kind,bool opened,bool meta=true,int badEnd=-1) {
        int id=(seq++)&127;
        int a[]={id,kind|4|(opened?64:0),0,0,0,13,badEnd<0?id:badEnd}; bool result=false;
        for(int i=0;i<7;++i) result=t.push(2,20+i,a[i],meta)||result;
        return result;
    };
    assert(!frame(1,true)); assert(t.held==4); assert(frame(3,false)); assert(t.held==0);
    assert(!frame(3,false)); assert(!frame(1,true,false)); assert(!frame(3,false));
    assert(!frame(1,true)); assert(!frame(3,false,true,127)); assert(t.held==4); t.reset(); assert(t.held==0);
    // A replayed Start cannot resurrect a closed contact.
    seq=0; assert(!frame(1,true)); assert(frame(3,false)); seq=0;
    assert(!frame(1,true)); assert(t.held==4); // only the latest sequence is protected
    seq=0; assert(!frame(1,true)); assert(t.held==4); // exact duplicate ignored
    t.reset(); assert(!t.push(2,20,1,true)); assert(!t.push(2,21,1|4|32|64,true));
    for(int cc=22;cc<=26;++cc) assert(!t.push(2,cc,cc==26?1:0,true));
    assert(t.held==0); // moved-on-Start is malformed
    return 0;
}
