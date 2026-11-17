// Throwaway, explicit MIDI injection for the disposable routing Set.
import Foundation
import CoreMIDI

func name(_ object: MIDIObjectRef) -> String {
    var value: Unmanaged<CFString>?
    MIDIObjectGetStringProperty(object, kMIDIPropertyDisplayName, &value)
    return value?.takeRetainedValue() as String? ?? ""
}
let destinations = (0..<MIDIGetNumberOfDestinations()).map { MIDIGetDestination($0) }
if CommandLine.arguments.count == 1 {
    for endpoint in destinations { print(name(endpoint)) }
    exit(0)
}
let virtual = CommandLine.arguments[1] == "--virtual"
let matches = destinations.filter { name($0) == CommandLine.arguments[1] }
guard virtual || matches.count == 1 else { fputs("Exact single destination required\n", stderr); exit(1) }
var client: MIDIClientRef = 0
var port: MIDIPortRef = 0
var source: MIDIEndpointRef = 0
guard MIDIClientCreate("Lightpad MIDI Routing Proof" as CFString, nil, nil, &client) == noErr,
      MIDIOutputPortCreate(client, "Proof notes" as CFString, &port) == noErr else { exit(2) }
if virtual && MIDISourceCreate(client,"Lightpad MIDI Routing Proof" as CFString,&source) != noErr { exit(3) }
func send(_ bytes: [UInt8]) {
    let raw = UnsafeMutableRawPointer.allocate(byteCount: 1024, alignment: 16)
    defer { raw.deallocate() }
    let list = raw.assumingMemoryBound(to: MIDIPacketList.self)
    let packet = MIDIPacketListInit(list)
    bytes.withUnsafeBufferPointer { b in
        _ = MIDIPacketListAdd(list, 1024, packet, mach_absolute_time(), b.count, b.baseAddress!)
    }
    let result = virtual ? MIDIReceived(source,list) : MIDISend(port, matches[0], list)
    print("SEND \(result) \(bytes)"); fflush(stdout)
}
print("READY \(virtual ? name(source) : name(matches[0]))"); fflush(stdout)
while let line = readLine() {
    if line == "quit" { break }
    let numbers = line.split(separator: " ").compactMap { Int($0) }
    guard numbers.count == 3, (128...239).contains(numbers[0]),
          numbers.dropFirst().allSatisfy({ (0...127).contains($0) }) else {
        print("ERR expected status data1 data2"); fflush(stdout); continue
    }
    send(numbers.map(UInt8.init))
}
// Only our test channel is used. Release its pedal and notes on clean exit.
send([0xb0, 64, 0]); send([0xb0, 123, 0])
if virtual { MIDIEndpointDispose(source) }
MIDIPortDispose(port); MIDIClientDispose(client)
