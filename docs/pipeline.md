# Processing pipeline

1. **Inspect:** validate the extension and use OpenCV for dimensions, FPS, frame count, duration, and FOURCC codec. Audio is ignored.
2. **Select:** validate non-negative start and positive duration/count. Requests beyond the end are clamped with a warning.
3. **Sample:** `numpy.linspace` selects uniform frame indices over the effective interval. OpenCV seeks and reads only those frames.
4. **Crop:** use the full image by default; an explicit rectangle must fit entirely inside it.
5. **Resize:** `contain` letterboxes with black, `cover` center-crops, and `stretch` ignores aspect ratio.
6. **Palette:** nearest-color Euclidean distance maps RGB pixels deterministically to grayscale or the central Calendar-inspired palette.
7. **Background:** before quantization, pixels within the configured Euclidean RGB tolerance are empty. Without a background option, none are removed.
8. **Blocks:** each row is scanned left-to-right and adjacent non-empty pixels of one color become a width-N, height-1 block. The algorithm can later be replaced by rectangular merging.
9. **Preview:** processed grids are enlarged with nearest-neighbor and saved as PNG plus an animated GIF. Transparent PNG pixels represent ignored background.
10. **Manifest:** schema `1.0` records safe relative paths, timestamps, parameters, blocks, and statistics. `source-info.json` preserves inspected metadata.

Preview FPS defaults to selected frame count divided by effective clip duration, with a minimum of 1 FPS. The preview enables approval before any future Calendar operation.
