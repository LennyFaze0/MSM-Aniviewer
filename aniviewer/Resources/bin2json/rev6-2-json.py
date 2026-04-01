from os.path import splitext
from pathlib import Path
from json import load, dump
from typing import Self, List
from enum import Enum
from sys import argv
import sys

try:
	from binfile import BinFile
except ImportError:
	script_dir = Path(__file__).resolve().parent
	path_str = str(script_dir)
	if path_str not in sys.path:
		sys.path.insert(0, path_str)
	try:
		from binfile import BinFile  # type: ignore
	except ImportError as import_exc:
		raise import_exc

BLEND_VERSION: int = 2

class Immediate(Enum):
	UNSET: int = -1
	SET: int = 0
	NONE: int = 1

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(bf.readInt8())

	def write(self, bf: BinFile) -> None:
		bf.writeInt8(self.value)


class DataValue:
	def __init__(self, immediate: Immediate, value: float) -> None:
		self.immediate = immediate
		self.value = value

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			Immediate(data.get('immediate', 0)),
			data.get('value', 0.0)
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(Immediate.read(bf), bf.readFloat())

	def to_dict(self) -> dict:
		return {
			'immediate': self.immediate.value,
			'value': self.value
		}

	def write(self, bf: BinFile) -> None:
		self.immediate.write(bf)
		bf.writeFloat(self.value)


class DataXY:
	def __init__(self, immediate: Immediate, x: float, y: float) -> None:
		self.immediate = immediate
		self.x = x
		self.y = y

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			Immediate(data.get('immediate', 0)),
			data.get('x', 0.0),
			data.get('y', 0.0)
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(Immediate.read(bf), bf.readFloat(), bf.readFloat())

	def to_dict(self) -> dict:
		return {
			'immediate': self.immediate.value,
			'x': self.x,
			'y': self.y
		}

	def write(self, bf: BinFile) -> None:
		self.immediate.write(bf)
		bf.writeFloat(self.x)
		bf.writeFloat(self.y)


class DataRect:
	def __init__(self, immediate: Immediate, x: float, y: float, w: float, h: float) -> None:
		self.immediate = immediate
		self.x = x
		self.y = y
		self.w = w
		self.h = h

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			Immediate(data.get('immediate', 0)),
			data.get('x', 0.0),
			data.get('y', 0.0),
			data.get('w', 0.0),
			data.get('h', 0.0),
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(Immediate.read(bf), bf.readFloat(), bf.readFloat(), bf.readFloat(), bf.readFloat())

	def to_dict(self) -> dict:
		return {
			'immediate': self.immediate.value,
			'x': self.x,
			'y': self.y,
			'w': self.w,
			'h': self.h
		}

	def write(self, bf: BinFile) -> None:
		self.immediate.write(bf)
		bf.writeFloat(self.x)
		bf.writeFloat(self.y)
		bf.writeFloat(self.w)
		bf.writeFloat(self.h)


class DataString:
	def __init__(self, immediate: Immediate, string: str) -> None:
		self.immediate = immediate
		self.string = string

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			Immediate(data.get('immediate', 0)),
			data.get('string', '')
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(Immediate.read(bf), bf.readString())

	def to_dict(self) -> dict:
		return {
			'immediate': self.immediate.value,
			'string': self.string
		}

	def write(self, bf: BinFile) -> None:
		self.immediate.write(bf)
		bf.writeString(self.string)


class DataRGB:
	def __init__(self, immediate: Immediate, red: int, green: int, blue: int) -> None:
		self.immediate = immediate
		self.red = red
		self.green = green
		self.blue = blue

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			Immediate(data.get('immediate', 0)),
			data.get('red', 255),
			data.get('green', 255),
			data.get('blue', 255)
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(
			Immediate.read(bf),
			bf.readUInt8(),
			bf.readUInt8(),
			bf.readUInt8()
		)

	def to_dict(self) -> dict:
		return {
			'immediate': self.immediate.value,
			'red': self.red,
			'green': self.green,
			'blue': self.blue
		}

	def write(self, bf: BinFile) -> None:
		self.immediate.write(bf)
		bf.writeUInt8(self.red)
		bf.writeUInt8(self.green)
		bf.writeUInt8(self.blue)


class Blend(Enum):
	STANDARD: int = 0          # Default alpha blend
	PREMULT_ALPHA: int = 1     # GL_ONE, GL_ONE_MINUS_SRC_ALPHA
	ADDITIVE: int = 2
	PREMULT_ALPHA_ALT: int = 3
	PREMULT_ALPHA_ALT2: int = 4
	INHERIT_STATE: int = 5     # Leave GL state untouched
	MULTIPLY: int = 6
	SCREEN: int = 7

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		value = bf.readUInt32()
		if value in cls._value2member_map_:
			return cls(value)
		# Fallback for unknown blend types – map to additive for safety
		print(f"[WARN] Unknown blend value {value}, defaulting to ADDITIVE (2)")
		return cls(cls.ADDITIVE.value)

	def write(self, bf: BinFile) -> None:
		bf.writeUInt32(self.value)


class Frame:
	def __init__(self, time: float, pos: DataXY, scale: DataXY, rotation: DataValue,
				 opacity: DataValue, sprite: DataString, rgb: DataRGB) -> None:
		self.time = time
		self.pos = pos
		self.scale = scale
		self.rotation = rotation
		self.opacity = opacity
		self.sprite = sprite
		self.rgb = rgb

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			data.get('time', 0.0),
			DataXY.from_dict(data.get('pos', {})),
			DataXY.from_dict(data.get('scale', {})),
			DataValue.from_dict(data.get('rotation', {})),
			DataValue.from_dict(data.get('opacity', {})),
			DataString.from_dict(data.get('sprite', {})),
			DataRGB.from_dict(data.get('rgb', {}))
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(
			bf.readFloat(),
			DataXY.read(bf),
			DataXY.read(bf),
			DataValue.read(bf),
			DataValue.read(bf),
			DataString.read(bf),
			DataRGB.read(bf)
		)

	def to_dict(self) -> dict:
		return {
			'time': self.time,
			'pos': self.pos.to_dict(),
			'scale': self.scale.to_dict(),
			'rotation': self.rotation.to_dict(),
			'opacity': self.opacity.to_dict(),
			'sprite': self.sprite.to_dict(),
			'rgb': self.rgb.to_dict(),
		}

	def write(self, bf: BinFile) -> None:
		bf.writeFloat(self.time)
		self.pos.write(bf)
		self.scale.write(bf)
		self.rotation.write(bf)
		self.opacity.write(bf)
		self.sprite.write(bf)
		self.rgb.write(bf)


class Layer:
	def __init__(self, name: str, l_type: int, blend: Blend, parent: int, l_id: int, src: int, width: int,
				 height: int, anchor_x: float, anchor_y: float, unk: str, frames: list[Frame]) -> None:
		self.name = name
		self.type = l_type
		self.blend = blend
		self.parent = parent
		self.id = l_id
		self.src = src
		self.width = width
		self.height = height
		self.anchor_x = anchor_x
		self.anchor_y = anchor_y
		self.unk = unk
		self.frames = frames

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			data.get('name', ''),
			data.get('type', 1),
			Blend(data.get('blend', Blend.STANDARD)),
			data.get('parent', -1),
			data.get('id', 0),
			data.get('src', 0),
			data.get('width', 0),
			data.get('height', 0),
			data.get('anchor_x', 0.0),
			data.get('anchor_y', 0.0),
			data.get('unk', ''),
			[Frame.from_dict(frame) for frame in data.get('frames', [])]
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(
			bf.readString(),
			bf.readInt32(),
			Blend.read(bf),
			bf.readInt16(),
			bf.readInt16(),
			bf.readInt16(),
			bf.readUInt16(),
			bf.readUInt16(),
			bf.readFloat(),
			bf.readFloat(),
			bf.readString(),
			[Frame.read(bf) for _ in range(bf.readUInt32())]
		)

	def to_dict(self) -> dict:
		return {
			'name': self.name,
			'type': self.type,
			'blend': self.blend.value,
			'parent': self.parent,
			'id': self.id,
			'src': self.src,
			'width': self.width,
			'height': self.height,
			'anchor_x': self.anchor_x,
			'anchor_y': self.anchor_y,
			'unk': self.unk,
			'frames': [frame.to_dict() for frame in self.frames]
		}

	def write(self, bf: BinFile) -> None:
		bf.writeString(self.name)
		bf.writeInt32(self.type)
		self.blend.write(bf)
		bf.writeInt16(self.parent)
		bf.writeInt16(self.id)
		bf.writeInt16(self.src)
		bf.writeUInt16(self.width)
		bf.writeUInt16(self.height)
		bf.writeFloat(self.anchor_x)
		bf.writeFloat(self.anchor_y)
		bf.writeString(self.unk)
		bf.writeUInt32(len(self.frames))
		[frame.write(bf) for frame in self.frames]


class CloneLayer:
	def __init__(self, new_layer: str, source_layer: str, reference_layer: str, variant_index: int) -> None:
		self.new_layer = new_layer
		self.source_layer = source_layer
		self.reference_layer = reference_layer
		self.variant_index = variant_index

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		variant = data.get('variant_index')
		if variant is None:
			insert_mode = data.get('insert_mode', 1)
			variant = 0xFFFFFFFF if insert_mode and insert_mode < 0 else insert_mode
		return cls(
			data.get('new_layer') or data.get('name', ''),
			data.get('source_layer') or data.get('resource', ''),
			data.get('reference_layer') or data.get('sheet', ''),
			CloneLayer._normalize_variant(variant or 0)
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(
			bf.readString(),
			bf.readString(),
			bf.readString(),
			bf.readUInt32()
		)

	def to_dict(self) -> dict:
		insert_mode = self._signed_variant(self.variant_index)
		insert_mode = -1 if insert_mode < 0 else 1
		return {
			'name': self.new_layer,
			'resource': self.source_layer,
			'sheet': self.reference_layer,
			'variant_index': self.variant_index,
			'new_layer': self.new_layer,
			'source_layer': self.source_layer,
			'reference_layer': self.reference_layer,
			'insert_mode': insert_mode
		}

	def write(self, bf: BinFile) -> None:
		bf.writeString(self.new_layer)
		bf.writeString(self.source_layer)
		bf.writeString(self.reference_layer)
		bf.writeUInt32(CloneLayer._normalize_variant(self.variant_index))

	@staticmethod
	def _signed_variant(value: int) -> int:
		return value if value < 0x80000000 else value - 0x100000000

	@staticmethod
	def _normalize_variant(value: int) -> int:
		return value & 0xFFFFFFFF


class Animation:
	def __init__(self, name: str, width: int, height: int, loop_offset: float, centered: int,
				 layers: list[Layer], clone_layers: list["CloneLayer"] | None = None) -> None:
		self.name = name
		self.width = width
		self.height = height
		self.loop_offset = loop_offset
		self.centered = centered
		self.layers = layers
		self.clone_layers = clone_layers or []

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			data.get('name', ''),
			data.get('width', 0),
			data.get('height', 0),
			data.get('loop_offset', 0.0),
			data.get('centered', 0),
			[Layer.from_dict(layer) for layer in data.get('layers', [])],
			[CloneLayer.from_dict(entry) for entry in data.get('clone_layers', [])]
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		return cls(
			bf.readString(),
			bf.readUInt16(),
			bf.readUInt16(),
			bf.readFloat(),
			bf.readUInt32(),
			[Layer.read(bf) for _ in range(bf.readUInt32())],
			Animation._maybe_read_clone_layers(bf)
		)

	def to_dict(self) -> dict:
		return {
			'name': self.name,
			'width': self.width,
			'height': self.height,
			'loop_offset': self.loop_offset,
			'centered': self.centered,
			'layers': [layer.to_dict() for layer in self.layers],
			'clone_layers': [clone.to_dict() for clone in self.clone_layers]
		}

	def write(self, bf: BinFile) -> None:
		bf.writeString(self.name)
		bf.writeUInt16(self.width)
		bf.writeUInt16(self.height)
		bf.writeFloat(self.loop_offset)
		bf.writeUInt32(self.centered)
		bf.writeUInt32(len(self.layers))
		[layer.write(bf) for layer in self.layers]
		if self.clone_layers:
			bf.writeUInt32(len(self.clone_layers))
			[clone.write(bf) for clone in self.clone_layers]

	@staticmethod
	def _maybe_read_clone_layers(bf: BinFile) -> list["CloneLayer"]:
		start = bf.tell()
		clones: List["CloneLayer"] = []
		try:
			clone_count = bf.readUInt32()
		except Exception:
			bf.seek(start, BinFile.WHENCE_START)
			return []

		if clone_count > 512:
			bf.seek(start, BinFile.WHENCE_START)
			return []

		try:
			clones = [CloneLayer.read(bf) for _ in range(clone_count)]
		except Exception:
			bf.seek(start, BinFile.WHENCE_START)
			return []

		if clone_count and any(not clone.new_layer or not clone.source_layer for clone in clones):
			bf.seek(start, BinFile.WHENCE_START)
			return []

		return clones


class Source:
	def __init__(self, src: str, _id: int, width: int, height: int) -> None:
		self.src = src
		self.id = _id
		self.width = width
		self.height = height

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		return cls(
			data.get('src', ''),
			data.get('id', 0),
			data.get('width', 0),
			data.get('height', 0)
		)

	@classmethod
	def read(cls, bf: BinFile) -> Self:
		src: str = bf.readString()
		_id: int = bf.readUInt16()
		w: int = bf.readUInt16()
		h: int = bf.readUInt16()
		return cls(src, _id, w, h)

	def to_dict(self) -> dict:
		return {
			'src': self.src,
			'id': self.id,
			'width': self.width,
			'height': self.height
		}

	def write(self, bf: BinFile) -> None:
		bf.writeString(self.src)
		bf.writeUInt16(self.id)
		bf.writeUInt16(self.width)
		bf.writeUInt16(self.height)


class BinAnim:
	REV: int = 6

	def __init__(self, sources: list[Source], anims: list[Animation]) -> None:
		self.sources: list[Source] = sources
		self.anims: list[Animation] = anims

	@classmethod
	def from_dict(cls, data: dict) -> Self:
		rev: int = data.get('rev', 0)
		if rev != BinAnim.REV: raise Exception(f'Rev {rev} not supported!')
		return cls(
			[Source.from_dict(source) for source in data.get('sources', [])],
			[Animation.from_dict(anim) for anim in data.get('anims', [])],
		)

	@classmethod
	def from_json(cls, filename: str) -> Self:
		with open(filename, 'r') as f:
			return cls.from_dict(load(f))

	@classmethod
	def from_file(cls, filename: str) -> Self:
		bf: BinFile = BinFile(filename)
		self: BinAnim = cls(
			[Source.read(bf) for _ in range(bf.readUInt32())],
			[Animation.read(bf) for _ in range(bf.readUInt32())]
		)
		bf.close()
		return self

	def save(self, filename: str) -> None:
		bf: BinFile = BinFile(filename, True)
		bf.writeUInt32(len(self.sources))
		[source.write(bf) for source in self.sources]
		bf.writeUInt32(len(self.anims))
		[anim.write(bf) for anim in self.anims]
		bf.close()

	def to_dict(self) -> dict:
		return {
			'rev': BinAnim.REV,
			'blend_version': BLEND_VERSION,
			'sources': [source.to_dict() for source in self.sources],
			'anims': [anim.to_dict() for anim in self.anims]
		}

	def to_json(self, filename: str) -> None:
		with open(filename, 'w') as f:
			dump(self.to_dict(), f, indent=2)


def main() -> None:
	usage: str = (f'usages:\n\t'
				  f'rev{BinAnim.REV}-2-json d file.bin\n\t'
				  f'rev{BinAnim.REV}-2-json b file.json')
	if len(argv) < 3:
		return print(usage)
	mode: str = argv[1]
	filename: str = argv[2]
	file: str; ext: str
	file, ext = splitext(filename)

	match mode:
		case 'd':
			anim: BinAnim = BinAnim.from_file(filename)
			anim.to_json(f'{file}.json')
		case 'b':
			anim: BinAnim = BinAnim.from_json(filename)
			anim.save(f'{file}.bin')
		case _:
			print(usage)


if __name__ == '__main__':
	main()
