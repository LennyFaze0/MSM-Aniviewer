"""
Minimal MIDI read/write helpers for MSM timing data.
Designed to preserve meta/non-note events while allowing note segment edits.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class MidiEvent:
    tick: int
    kind: str  # "meta", "sysex", "midi"
    status: int
    data: bytes


@dataclass
class MidiNote:
    start_tick: int
    end_tick: int
    note: int
    channel: int
    velocity: int = 100
    off_velocity: int = 64


@dataclass
class MidiTrackData:
    name: str = ""
    notes: List[MidiNote] = field(default_factory=list)
    events: List[MidiEvent] = field(default_factory=list)


@dataclass
class MidiFileData:
    format: int
    ticks_per_beat: int
    tracks: List[MidiTrackData]

    def get_bpm(self) -> Optional[float]:
        tempo = _find_first_tempo_event(self)
        if tempo is None:
            return None
        micro = int.from_bytes(tempo.data, "big") if len(tempo.data) == 3 else None
        if not micro:
            return None
        return 60000000.0 / micro

    def set_bpm(self, bpm: float) -> None:
        if bpm <= 0:
            return
        tempo_micro = int(round(60000000.0 / bpm))
        tempo_micro = max(1, min(0xFFFFFF, tempo_micro))
        data = tempo_micro.to_bytes(3, "big")

        tempo = _find_first_tempo_event(self)
        if tempo is not None:
            tempo.data = data
            return

        if not self.tracks:
            self.tracks.append(MidiTrackData())
        self.tracks[0].events.insert(
            0, MidiEvent(0, "meta", 0x51, data)
        )


def read_midi_file(path: str) -> MidiFileData:
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 14 or data[0:4] != b"MThd":
        raise ValueError("Invalid MIDI header.")

    header_len = int.from_bytes(data[4:8], "big")
    if header_len < 6:
        raise ValueError("Invalid MIDI header length.")

    fmt = int.from_bytes(data[8:10], "big")
    ntrks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        # SMPTE timing is rare for MSM; fall back to 480 if encountered.
        ticks_per_beat = 480
    else:
        ticks_per_beat = division

    offset = 8 + header_len
    tracks: List[MidiTrackData] = []

    for _ in range(ntrks):
        if offset + 8 > len(data) or data[offset:offset + 4] != b"MTrk":
            break
        track_len = int.from_bytes(data[offset + 4:offset + 8], "big")
        offset += 8
        track_data = data[offset:offset + track_len]
        offset += track_len
        tracks.append(_parse_track(track_data))

    return MidiFileData(format=fmt, ticks_per_beat=ticks_per_beat, tracks=tracks)


def write_midi_file(midi: MidiFileData, path: str) -> None:
    tracks_bytes: List[bytes] = []
    for track in midi.tracks:
        events = _build_track_events(track)
        track_blob = _serialize_track(events)
        tracks_bytes.append(track_blob)

    header = b"MThd" + (6).to_bytes(4, "big")
    header += int(midi.format).to_bytes(2, "big")
    header += len(tracks_bytes).to_bytes(2, "big")
    header += int(midi.ticks_per_beat).to_bytes(2, "big")

    with open(path, "wb") as f:
        f.write(header)
        for blob in tracks_bytes:
            f.write(blob)


def _parse_track(track_data: bytes) -> MidiTrackData:
    idx = 0
    abs_tick = 0
    running_status: Optional[int] = None
    notes: List[MidiNote] = []
    events: List[MidiEvent] = []
    name = ""
    open_notes: dict[Tuple[int, int], List[Tuple[int, int]]] = {}

    while idx < len(track_data):
        delta, idx = _read_varlen(track_data, idx)
        abs_tick += delta
        if idx >= len(track_data):
            break
        status = track_data[idx]
        if status < 0x80:
            if running_status is None:
                break
            status = running_status
        else:
            idx += 1
            running_status = status

        if status == 0xFF:
            if idx >= len(track_data):
                break
            meta_type = track_data[idx]
            idx += 1
            length, idx = _read_varlen(track_data, idx)
            meta_data = track_data[idx:idx + length]
            idx += length
            if meta_type == 0x2F:
                # End of track
                continue
            if meta_type == 0x03 and not name:
                try:
                    name = meta_data.decode("latin1", errors="ignore")
                except Exception:
                    name = ""
            events.append(MidiEvent(abs_tick, "meta", meta_type, bytes(meta_data)))
            continue

        if status in (0xF0, 0xF7):
            length, idx = _read_varlen(track_data, idx)
            sysex_data = track_data[idx:idx + length]
            idx += length
            events.append(MidiEvent(abs_tick, "sysex", status, bytes(sysex_data)))
            continue

        event_type = status & 0xF0
        channel = status & 0x0F
        if event_type in (0xC0, 0xD0):
            if idx >= len(track_data):
                break
            data = bytes([track_data[idx]])
            idx += 1
            events.append(MidiEvent(abs_tick, "midi", status, data))
            continue

        if idx + 1 >= len(track_data):
            break
        param1 = track_data[idx]
        param2 = track_data[idx + 1]
        idx += 2

        if event_type == 0x90 and param2 != 0:
            key = (channel, param1)
            open_notes.setdefault(key, []).append((abs_tick, param2))
            continue

        if event_type == 0x80 or (event_type == 0x90 and param2 == 0):
            key = (channel, param1)
            if key in open_notes and open_notes[key]:
                start_tick, velocity = open_notes[key].pop(0)
                notes.append(
                    MidiNote(
                        start_tick=start_tick,
                        end_tick=abs_tick,
                        note=param1,
                        channel=channel,
                        velocity=velocity,
                        off_velocity=param2 if event_type == 0x80 else 64,
                    )
                )
            continue

        events.append(MidiEvent(abs_tick, "midi", status, bytes([param1, param2])))

    notes.sort(key=lambda n: (n.start_tick, n.end_tick))
    return MidiTrackData(name=name, notes=notes, events=events)


def _build_track_events(track: MidiTrackData) -> List[dict]:
    events: List[dict] = []
    order_counter = 0
    for ev in track.events:
        events.append(
            {
                "tick": max(0, int(ev.tick)),
                "kind": ev.kind,
                "status": ev.status,
                "data": ev.data,
                "order": order_counter,
            }
        )
        order_counter += 1

    for note in track.notes:
        start = max(0, int(note.start_tick))
        end = max(start, int(note.end_tick))
        channel = max(0, min(15, int(note.channel)))
        note_num = max(0, min(127, int(note.note)))
        vel = max(0, min(127, int(note.velocity)))
        off_vel = max(0, min(127, int(note.off_velocity)))

        events.append(
            {
                "tick": start,
                "kind": "note_on",
                "status": 0x90 | channel,
                "data": bytes([note_num, vel]),
                "order": order_counter,
            }
        )
        order_counter += 1
        events.append(
            {
                "tick": end,
                "kind": "note_off",
                "status": 0x80 | channel,
                "data": bytes([note_num, off_vel]),
                "order": order_counter,
            }
        )
        order_counter += 1

    def priority(evt: dict) -> Tuple[int, int]:
        kind = evt["kind"]
        if kind == "meta":
            rank = 0
        elif kind == "sysex":
            rank = 1
        elif kind == "note_off":
            rank = 2
        elif kind == "note_on":
            rank = 3
        else:
            rank = 4
        return (evt["tick"], rank, evt.get("order", 0))

    events.sort(key=priority)
    return events


def _serialize_track(events: List[dict]) -> bytes:
    blob = bytearray()
    last_tick = 0
    for evt in events:
        tick = max(0, int(evt["tick"]))
        delta = max(0, tick - last_tick)
        last_tick = tick
        blob.extend(_write_varlen(delta))

        kind = evt["kind"]
        status = evt["status"]
        data = evt["data"] or b""

        if kind == "meta":
            blob.append(0xFF)
            blob.append(status & 0x7F)
            blob.extend(_write_varlen(len(data)))
            blob.extend(data)
            continue
        if kind == "sysex":
            blob.append(status)
            blob.extend(_write_varlen(len(data)))
            blob.extend(data)
            continue

        blob.append(status)
        blob.extend(data)

    # End of track
    blob.extend(_write_varlen(0))
    blob.append(0xFF)
    blob.append(0x2F)
    blob.append(0x00)

    return b"MTrk" + len(blob).to_bytes(4, "big") + bytes(blob)


def _read_varlen(data: bytes, idx: int) -> Tuple[int, int]:
    value = 0
    while idx < len(data):
        byte = data[idx]
        idx += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            break
    return value, idx


def _write_varlen(value: int) -> bytes:
    value = max(0, int(value))
    buffer = value & 0x7F
    out = bytearray()
    while True:
        value >>= 7
        if value:
            buffer <<= 8
            buffer |= ((value & 0x7F) | 0x80)
        else:
            break
    while True:
        out.append(buffer & 0xFF)
        if buffer & 0x80:
            buffer >>= 8
        else:
            break
    return bytes(out)


def _find_first_tempo_event(midi: MidiFileData) -> Optional[MidiEvent]:
    for track in midi.tracks:
        for ev in track.events:
            if ev.kind == "meta" and ev.status == 0x51 and len(ev.data) == 3:
                return ev
    return None
