# lt25-mcp

An MCP server for the Fender Mustang LT25 guitar amplifier, so presets can be
read, auditioned and written programmatically.

The amp exposes two USB devices: an audio interface, and a HID interface used
for control. This project speaks the HID side.

## Status

Early. The packet framing layer is done and tested; nothing talks to hardware
yet.

- [x] HID packet framing (64-byte TLV chunking and reassembly)
- [ ] Protobuf message codec
- [ ] Connection handling (sync handshake, heartbeat)
- [ ] Read presets
- [ ] Audition presets
- [ ] Write presets
- [ ] MCP server

## Protocol

Control messages travel over USB HID as fixed 64-byte reports:

```
Host -> Device:  [tag][length][value (61 bytes)][0x00]
Device -> Host:  [0x00][tag][length][value (61 bytes)]
```

Replies are shifted one byte right behind a leading pad byte, so encoding and
decoding are not mirror images. Tags mark position in a multi-packet message:
`0x33` start, `0x34` continuation, `0x35` final or only packet. The reassembled
value is a protobuf `FenderMessageLT`; presets themselves are JSON.

The protocol was reverse engineered by [brentmaxwell/LtAmp][ltamp], which
documents the [wire format][protocol] and carries the [protobuf schemas][schema]
extracted from Fender's own desktop application. This project is an independent
Python implementation of that documented protocol.

[ltamp]: https://github.com/brentmaxwell/LtAmp
[protocol]: https://github.com/brentmaxwell/LtAmp/blob/main/Docs/Protocol.md
[schema]: https://github.com/brentmaxwell/LtAmp/tree/main/Schema/protobuf

## Safety

The amp holds 60 preset slots: 30 factory presets and 30 empty ones. Writes
target slots 31-60 only, leaving the factory presets intact. Back up all 60
slots before any write. `AuditionPreset` plays a preset through the speaker
without saving it, so iteration never has to touch amp memory.

`MENU -> RESTORE` on the amp is a factory reset if something goes wrong.

## Development

```bash
uv sync
uv run pytest
```

See [docs/troubleshooting.md](docs/troubleshooting.md) if imports fail after a
sync — there is a known macOS file-flag interaction between uv and CPython.

## Unofficial

Not affiliated with or endorsed by Fender. The protocol is undocumented by the
manufacturer and could change with any firmware update.
