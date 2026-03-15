import os
import re
import struct
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple


@dataclass
class BuddySample:
    sample_id: int
    relative_path: str


@dataclass
class BuddyTrack:
    sample_ref: int
    color_block_size: int
    name: str
    color: Tuple[float, float, float, float]


class BuddyManifest:
    """
    Lightweight parser for the game's 001_*.bin buddy audio manifests.

    These files store tables of audio samples plus track bindings that map
    animation names to sample ids.
    """

    def __init__(
        self,
        source_path: Path,
        signature: str,
        version: int,
        labels: List[str],
        samples: List[BuddySample],
        tracks: List[BuddyTrack]
    ) -> None:
        self.source_path = source_path
        self.signature = signature
        self.version = version
        self.labels = labels
        self.samples = samples
        self.tracks = tracks

    @classmethod
    def from_file(cls, path: str) -> "BuddyManifest":
        raw_data = Path(path).read_bytes()
        if not cls._looks_like_budd(raw_data):
            raise ValueError("Not a buddy manifest")
        try:
            if cls._looks_like_v1(raw_data):
                return cls._parse_v1(path, raw_data)
        except Exception:
            pass
        try:
            # Muppets build ships a simplified manifest without colour blocks or
            # explicit versioning; fall back to a bespoke parser.
            return cls._parse_v2(path, raw_data)
        except Exception:
            pass
        # Newer numbered world manifests use a compact layout. Fall back to
        # string reconstruction so track->audio mapping still resolves.
        return cls._parse_compact(path, raw_data)

    @staticmethod
    def _looks_like_budd(data: bytes) -> bool:
        if len(data) < 8:
            return False
        try:
            name_len = struct.unpack_from("<I", data, 0)[0]
        except struct.error:
            return False
        if name_len <= 0 or name_len > 0x100:
            return False
        if 4 + name_len > len(data):
            return False
        sig = data[4:4 + max(name_len - 1, 0)].decode("ascii", errors="ignore").strip().lower()
        return sig == "budd"

    @staticmethod
    def _looks_like_v1(data: bytes) -> bool:
        if len(data) < 12:
            return False
        name_len = struct.unpack_from("<I", data, 0)[0]
        offset = (4 + name_len + 3) & ~0x03
        if offset + 4 > len(data):
            return False
        maybe_version = struct.unpack_from("<I", data, offset)[0]
        return 0 < maybe_version <= 4

    @classmethod
    def _parse_v1(cls, path: str, data: bytes) -> "BuddyManifest":
        view = memoryview(data)
        total_len = len(view)
        offset = 0

        def align() -> None:
            nonlocal offset
            offset = (offset + 3) & ~0x03

        def ensure_available(size: int) -> None:
            if offset + size > total_len:
                raise struct.error(
                    f"Unexpected EOF reading {size} bytes at offset {offset} "
                    f"(size={total_len})"
                )

        def read_u32() -> int:
            nonlocal offset
            ensure_available(4)
            value = struct.unpack_from("<I", view, offset)[0]
            offset += 4
            return value

        def read_string() -> str:
            nonlocal offset
            length = read_u32()
            if length <= 0:
                align()
                return ""
            ensure_available(length)
            raw = bytes(view[offset:offset + max(length - 1, 0)])
            offset += length
            align()
            return raw.decode("utf-8", errors="ignore")

        signature = read_string()
        version = read_u32()

        labels: List[str] = [read_string(), read_string(), read_string()]
        _header_size = read_u32()
        first_count = read_u32()

        def try_parse_samples(start_offset: int, sample_count: int) -> Optional[Tuple[List[BuddySample], int]]:
            if sample_count < 0 or sample_count > 0x100000:
                return None
            local_offset = start_offset
            parsed_samples: List[BuddySample] = []
            for _ in range(sample_count):
                if local_offset + 8 > total_len:
                    return None
                sample_id = struct.unpack_from("<I", view, local_offset)[0]
                local_offset += 4
                path_length = struct.unpack_from("<I", view, local_offset)[0]
                local_offset += 4
                if path_length <= 0 or local_offset + path_length > total_len:
                    return None
                raw = bytes(view[local_offset:local_offset + max(path_length - 1, 0)])
                local_offset += path_length
                local_offset = (local_offset + 3) & ~0x03
                rel_path = raw.decode("utf-8", errors="ignore")
                parsed_samples.append(BuddySample(sample_id, rel_path))
                if (
                    local_offset + 4 <= total_len
                    and struct.unpack_from("<I", view, local_offset)[0] == 0
                ):
                    local_offset += 4
            return parsed_samples, local_offset

        candidates: List[Tuple[int, int, int, List[BuddySample], int]] = []

        direct_candidate = try_parse_samples(offset, first_count)
        if direct_candidate is not None:
            samples_direct, end_direct = direct_candidate
            score_direct = 0
            for sample in samples_direct:
                lower_path = sample.relative_path.lower()
                if "/" in sample.relative_path:
                    score_direct += 1
                if lower_path.endswith((".ogg", ".wav", ".mp3")):
                    score_direct += 2
                if sample.sample_id >= 0x100:
                    score_direct += 1
            candidates.append((score_direct, 0, first_count, samples_direct, end_direct))

        if offset + 4 <= total_len:
            bank_count = first_count
            sample_count = struct.unpack_from("<I", view, offset)[0]
            bank_candidate = try_parse_samples(offset + 4, sample_count)
            if bank_candidate is not None:
                samples_bank, end_bank = bank_candidate
                score_bank = 0
                for sample in samples_bank:
                    lower_path = sample.relative_path.lower()
                    if "/" in sample.relative_path:
                        score_bank += 1
                    if lower_path.endswith((".ogg", ".wav", ".mp3")):
                        score_bank += 2
                    if sample.sample_id >= 0x100:
                        score_bank += 1
                candidates.append((score_bank, bank_count, sample_count, samples_bank, end_bank))

        if not candidates:
            raise struct.error("Unable to parse buddy sample table")

        candidates.sort(key=lambda item: (item[0], item[2], item[1]), reverse=True)
        _sample_score, _bank_count, _sample_count, samples, offset = candidates[0]

        # Some manifests insert padding before the track table. Skip zeros.
        def peek_u32() -> Optional[int]:
            if offset + 4 > total_len:
                return None
            return struct.unpack_from("<I", view, offset)[0]

        while True:
            peek = peek_u32()
            if peek is None or peek != 0:
                break
            offset += 4

        track_count = read_u32() if offset + 4 <= total_len else 0

        def looks_like_track_entry(start_offset: int) -> bool:
            if start_offset + 12 > total_len:
                return False
            sample_ref = struct.unpack_from("<I", view, start_offset)[0]
            name_length = struct.unpack_from("<I", view, start_offset + 8)[0]
            if name_length <= 0 or name_length > 0x400:
                return False
            if not (sample_ref <= 0x00FFFFFF or sample_ref == 0xFFFFFFFF):
                return False
            end_of_name = start_offset + 12 + name_length
            end_of_name = (end_of_name + 3) & ~0x03
            return end_of_name <= total_len

        tracks: List[BuddyTrack] = []
        for track_index in range(track_count):
            sample_ref_raw = read_u32()
            sample_ref = sample_ref_raw if sample_ref_raw != 0xFFFFFFFF else -1
            color_block_size = read_u32()
            name = read_string()
            color = (0.0, 0.0, 0.0, 0.0)
            # V1 manifests often append an RGBA float block after each track entry.
            if offset + 16 <= total_len:
                consume_color = False
                if track_index < track_count - 1:
                    consume_color = not looks_like_track_entry(offset)
                else:
                    floats = struct.unpack_from("<4f", view, offset)
                    consume_color = all(math.isfinite(v) and -0.01 <= v <= 255.01 for v in floats)
                if consume_color:
                    floats = struct.unpack_from("<4f", view, offset)
                    if all(math.isfinite(v) for v in floats):
                        color = tuple(float(v) for v in floats)  # type: ignore[assignment]
                        offset += 16
            tracks.append(BuddyTrack(sample_ref, color_block_size, name, color))

        return cls(Path(path), signature, version, labels, samples, tracks)

    @classmethod
    def _parse_v2(cls, path: str, data: bytes) -> "BuddyManifest":
        view = memoryview(data)
        total_len = len(view)
        offset = 0

        def align() -> None:
            nonlocal offset
            offset = (offset + 3) & ~0x03

        def ensure_available(size: int) -> None:
            if offset + size > total_len:
                raise struct.error(
                    f"Unexpected EOF reading {size} bytes at offset {offset} "
                    f"(size={total_len})"
                )

        def read_u32() -> int:
            nonlocal offset
            ensure_available(4)
            value = struct.unpack_from("<I", view, offset)[0]
            offset += 4
            return value

        def read_string() -> str:
            nonlocal offset
            length = read_u32()
            if length <= 0:
                align()
                return ""
            ensure_available(length)
            raw = bytes(view[offset:offset + max(length - 1, 0)])
            offset += length
            align()
            return raw.decode("utf-8", errors="ignore")

        signature = read_string()
        friendly_name = read_string()
        header_size = read_u32()
        sample_count = read_u32()

        samples: List[BuddySample] = []
        for _ in range(sample_count):
            sample_id = read_u32()
            path_length = read_u32()
            ensure_available(path_length)
            raw = bytes(view[offset:offset + max(path_length - 1, 0)])
            offset += path_length
            align()
            rel_path = raw.decode("utf-8", errors="ignore")
            samples.append(BuddySample(sample_id, rel_path))

        track_count = read_u32() if offset + 4 <= total_len else 0

        def looks_like_track_entry(start_offset: int) -> bool:
            if start_offset + 12 > total_len:
                return False
            sample_ref = struct.unpack_from("<I", view, start_offset)[0]
            name_length = struct.unpack_from("<I", view, start_offset + 8)[0]
            if name_length <= 0 or name_length > 0x400:
                return False
            if not (sample_ref <= 0x00FFFFFF or sample_ref == 0xFFFFFFFF):
                return False
            end_of_name = start_offset + 12 + name_length
            end_of_name = (end_of_name + 3) & ~0x03
            return end_of_name <= total_len

        tracks: List[BuddyTrack] = []
        for track_index in range(track_count):
            sample_ref_raw = read_u32()
            sample_ref = sample_ref_raw if sample_ref_raw != 0xFFFFFFFF else -1
            color_block_size = read_u32()
            name = read_string()
            color = (0.0, 0.0, 0.0, 0.0)
            if offset + 16 <= total_len:
                consume_color = False
                if track_index < track_count - 1:
                    consume_color = not looks_like_track_entry(offset)
                else:
                    floats = struct.unpack_from("<4f", view, offset)
                    consume_color = all(math.isfinite(v) and -0.01 <= v <= 255.01 for v in floats)
                if consume_color:
                    floats = struct.unpack_from("<4f", view, offset)
                    if all(math.isfinite(v) for v in floats):
                        color = tuple(float(v) for v in floats)  # type: ignore[assignment]
                        offset += 16
            tracks.append(
                BuddyTrack(
                    sample_ref,
                    color_block_size,
                    name,
                    color,
                )
            )

        labels = [friendly_name, "", ""]
        return cls(Path(path), signature, header_size, labels, samples, tracks)

    @classmethod
    def _parse_compact(cls, path: str, data: bytes) -> "BuddyManifest":
        # Compact manifests still expose printable strings for paths and track names.
        strings = [s.decode("utf-8", "ignore").strip() for s in re.findall(rb"[ -~]{3,}", data)]
        strings = [s for s in strings if s]
        if not strings:
            raise ValueError("No strings in compact buddy manifest")

        audio_paths: List[str] = []
        track_names: List[str] = []
        labels: List[str] = []
        track_name_re = re.compile(r"^\d{2}-[A-Za-z0-9_]+_\d{2}(?:_[A-Za-z0-9]+)*$")
        for s in strings:
            lower = s.lower()
            if "/" in s and lower.endswith((".ogg", ".wav", ".mp3")):
                audio_paths.append(s)
                continue
            if track_name_re.match(s):
                track_names.append(s)
                continue
            if lower != "budd":
                labels.append(s)

        if not track_names:
            raise ValueError("No track names in compact buddy manifest")

        samples: List[BuddySample] = []
        sample_by_track: Dict[str, int] = {}
        monster_tracks = [t for t in track_names if "_Monster_" in t]
        non_monster_tracks = [t for t in track_names if t not in monster_tracks]
        assign_order = monster_tracks + non_monster_tracks

        for idx, rel_path in enumerate(audio_paths):
            sample_id = 0xFF00 + idx
            samples.append(BuddySample(sample_id, rel_path))
            if idx < len(assign_order):
                # Keep the first occurrence for duplicated track names (common in
                # composer/011 manifests where one logical track appears repeatedly).
                sample_by_track.setdefault(assign_order[idx], sample_id)

        monster_sample_by_num: Dict[int, int] = {}
        loop_sample_by_num: Dict[int, int] = {}
        monster_re = re.compile(r"_monster_(\d+)$", re.IGNORECASE)
        loop_re = re.compile(r"_monster_loop_(\d+)$", re.IGNORECASE)
        for sample in samples:
            stem = Path(sample.relative_path).stem
            loop_match = loop_re.search(stem)
            if loop_match:
                try:
                    loop_idx = int(loop_match.group(1))
                    loop_sample_by_num.setdefault(loop_idx, sample.sample_id)
                except ValueError:
                    pass
                continue
            monster_match = monster_re.search(stem)
            if monster_match:
                try:
                    monster_idx = int(monster_match.group(1))
                    monster_sample_by_num.setdefault(monster_idx, sample.sample_id)
                except ValueError:
                    pass

        note_track_re = re.compile(
            r"^\d{2}-[A-Za-z0-9_]+_Note_(\d+)(?:_([A-Za-z0-9]+))?$",
            re.IGNORECASE,
        )

        tracks: List[BuddyTrack] = []
        for idx, name in enumerate(track_names):
            sample_ref = sample_by_track.get(name)
            if sample_ref is None:
                note_match = note_track_re.match(name)
                if note_match:
                    try:
                        note_idx = int(note_match.group(1))
                    except ValueError:
                        note_idx = -1
                    suffix = (note_match.group(2) or "").lower()
                    source = loop_sample_by_num if suffix == "sust" else monster_sample_by_num
                    if note_idx > 0 and note_idx in source:
                        sample_ref = source[note_idx]
                    elif source:
                        first_key = sorted(source.keys())[0]
                        sample_ref = source[first_key]
            if sample_ref is None:
                sample_ref = -1
            tracks.append(BuddyTrack(sample_ref, idx, name, (0.0, 0.0, 0.0, 0.0)))

        version = 1
        return cls(Path(path), "budd", version, labels, samples, tracks)

    def iter_audio_links(self) -> Iterator[Tuple[str, Optional[str]]]:
        sample_lookup: Dict[int, str] = {}
        for sample in self.samples:
            sample_lookup[sample.sample_id] = sample.relative_path
            sample_lookup[sample.sample_id & 0xFFFF] = sample.relative_path
            sample_lookup[sample.sample_id & 0xFF] = sample.relative_path
            sample_lookup[sample.sample_id | 0xFF00] = sample.relative_path

        for track in self.tracks:
            rel_path = sample_lookup.get(track.sample_ref)
            yield track.name, rel_path
