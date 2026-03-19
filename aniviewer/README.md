# My Singing Monsters Animation Viewer

A comprehensive animation viewer for My Singing Monsters with OpenGL rendering, timeline scrubbing, and export features.

## Features

- **OpenGL Rendering**: Accurate animation rendering using raw OpenGL, similar to the actual game
- **Timeline Scrubbing**: Scrub through animations frame-by-frame with a visual timeline
- **Multiple Animations**: Load and switch between different animations from the same file
- **Audio Playback**: Automatically play the correct monster vocal track pulled from the game's `audio/music` directory, synced with animation playback
- **Layer Offset Presets**: Save and load sprite drag offsets for quickly swapping poses
- **Transform Gizmos**: Rotate or scale individual sprite segments with interactive overlays, including multi-layer selection and locking
- **Layer Visibility**: Toggle individual sprite layers on/off
- **Adjustable Settings**:
  - Render scale (zoom in/out)
  - Framerate control
  - Looping toggle
  - Sprite drag speed multiplier
  - Rotation gizmo sensitivity & radius controls
  - Anchor/rotation/scale biases, parent mix, trim shift, and world offset tuning for precise placement
- **File Management**:
  - Browse and select game path
  - Automatic detection of .bin and .json files
  - Built-in bin2json converter
  - Recursive search + instant filtering for BIN/JSON selections
  - Support for both .png and .avif texture formats
- **Export Options**:
  - Export current frame as transparent PNG
  - Export as PSD with layers
  - Export as transparent MOV video
  - MOV exports can optionally embed the monster's audio track for quick previews
  - Export as GIF animation
- **Full-Resolution Output**: Enable the "Full Resolution Output" toggles under Settings > Export to render PNG/PSD/MOV exports at the raw sprite bounds, ensuring no detail loss from viewport scaling.
  - PNG, MOV, and PSD exports expose a **Full Res Scale Multiplier** so you can oversample sprites (e.g., 2x/4x) without affecting layer positioning.
- **Persistent Settings**: All settings and paths are saved between sessions

## Installation

1. **Install Python 3.10 or higher**

https://www.python.org/ftp/python/3.13.9/python-3.13.9-amd64.exe

^ at this link, once installed it should just work inside the terminal (cmd)

2. **Install dependencies**:
   - **Windows**: run `setup.bat` and it will install everything automatically.
   - **macOS / Linux**: run `./setup_macos.sh` (use `chmod +x setup_macos.sh` the first time). This mirrors the Windows setup, including automatic pytoshop/packbits installs for PSD export.

3. **Install FFmpeg for video exports**:
Open the viewer, go to **Settings > Application > FFmpeg Tools** and click **Install FFmpeg**.  
The viewer will download a verified Windows build, place it inside your AppData folder, and add it to PATH automatically so MOV exports work immediately.

## Usage

1. **Run the application**:
   - **Windows**: double-click `run_viewer.bat`.
   - **macOS / Linux**: run `./run_viewer.sh` from Terminal (after `chmod +x run_viewer.sh` on first use).

2. **Set Game Path**:
   - Click "Browse Game Path"
   - Navigate to your My Singing Monsters game folder (the one containing the "data" folder)

3. **Load Animation**:
   - Select a .bin or .json file from the dropdown
   - Use the search box above the dropdown to filter files by name or folder
   - If you select a .bin file, click "Convert BIN to JSON" first
   - Select an animation from the animation dropdown
   - The animation will load and display in the center viewport

4. **Control Playback**:
   - Click "Play" to start/pause animation
   - Use the timeline slider to scrub through the animation
   - Toggle "Loop" to enable/disable looping
   - Adjust FPS to change playback frame rate

5. **Adjust View**:
   - Use the Scale slider to zoom in/out
   - Toggle layer visibility in the right panel
   - Sprite Dragging and Dropping: Drag and drop sprites to change their positions
   - Adjust drag speed, rotation sensitivity, gizmo size, anchor/scale/rotation biases, parent mix, and world offsets from the Render Settings panel
   - Click "Reset Placement Bias Settings" to snap all advanced placement sliders back to their defaults
   - Enable "Show Rotation Gizmo Overlay" and click a sprite to select it; the rotation wheel will appear on the selected layer for quick twisting before export

6. **Export**:
   - Click "Export Current Frame (PNG)" or "Export as PSD" " to save the current frame as a PNG or Adobe Photoshop project file
     - Use **Full Resolution Output** + the **Full Res Scale Multiplier** slider under Settings > Export if you need higher-resolution PNGs without losing detail
   - Click "Export as MOV" to save the full currently loaded animation as a transparent .MOV file (Will not play in windows' default video player)
     - Enable "Include audio track" in Settings if you want MOV exports to contain the monster's voice line
     - If MOV export is disabled, open **Settings > Application > FFmpeg Tools** and click **Install FFmpeg** to set everything up automatically
     - For maximum fidelity, turn on the **Full Resolution Output** checkbox for the desired format inside **Settings > Export**
     - When exporting PSDs you can bump the **Full Res Scale Multiplier** (e.g. 2.0) to get a higher-resolution document while keeping all layers aligned
     - MOV exports also have a **Full Res Scale Multiplier** that controls the render resolution of the intermediate frames before FFmpeg encoding
   - Click "Export as GIF" to save the animation as a GIF animation
7. **Layer Presets**:
   - Use the "Layer Offset Presets" panel to save current drag offsets/rotations to a `.txt` file
   - Load a preset to instantly reapply those offsets to the active animation
8. **Transform Gizmos & Multi-Select**:
   - Enable the rotation or scale gizmos from Render Settings to fine-tune individual layers (scale supports uniform or per-axis modes)
   - Use the Layer Visibility panel to toggle one or multiple draggable layers; lock them to move as a group or leave unlocked to move individually

### Audio

- The viewer automatically indexes the game's `data/audio/music` directory after you pick your game path.
- Whenever you load a monster JSON, the viewer resolves the proper audio clip using the same naming logic the game uses (`audio/music/<key>.ogg`) and loads it into the built-in player.
- Audio playback stays in sync with the animation timeline: scrubbing, pausing, or looping the animation updates the audio as well.
- Use the Audio panel on the left to mute/unmute playback, tweak the volume, or view which clip is currently loaded.


## How It Works

### Animation Data Format

The animation data is stored in binary .bin files that can be converted to JSON using the bin2json tool. Each animation file contains:

- **Sources**: References to texture atlas XML files
- **Animations**: Multiple named animations
- **Layers**: Sprite layers with parent-child relationships
- **Keyframes**: Time-based transformation data including:
  - Position (x, y)
  - Scale (x, y)
  - Rotation
  - Opacity
  - Sprite name
  - RGB color tint

### Texture Atlases

Texture atlases are defined in XML files that specify:
- Image path (can be .png or .avif)
- Sprite regions (x, y, width, height)
- Pivot points
- Rotation flags

## Keyboard Shortcuts

- **Space**: Play/Pause
- **Left/Right Arrow**: Step backward/forward one frame, make sure to click on the timeline first
- **Home**: Jump to start
- **End**: Jump to end

## Troubleshooting

### "bin2json script not found"
- Make sure the Resources/bin2json folder exists with rev6-2-json.py and binfile.py

### "Failed to load texture atlas"
- Check that the game path is set correctly
- Verify that texture files exist (try both .png and .avif extensions)
- Check the log for specific error messages

### Animation not displaying
- Ensure the JSON file was converted successfully
- Check that all referenced texture atlases are found (.xmls)
- Verify layer visibility is enabled

### Performance issues
- Reduce render scale
- Lower FPS setting
- Disable unused layers

## Technical Details

- **Language**: Python 3.10+
- **GUI Framework**: PyQt6
- **Rendering**: OpenGL 2.1+
- **Image Processing**: Pillow (PIL)
- **Data Format**: JSON (converted from binary)

## Credits

- bin2json converter script included in Resources/bin2json

## License

This tool is for educational and personal use only. My Singing Monsters and all related assets are property of Big Blue Bubble Inc.
