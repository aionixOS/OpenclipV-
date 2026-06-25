import subprocess
import os
import json
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)

async def process_clip(
  video_path: str,
  suggestion: dict[str, Any],
  output_dir: str,
  project_id: str,
  forced_layout_mode: Optional[str] = None
) -> dict[str, Any]:
  """
  Lite Pipeline for one clip:
  1. Cut raw clip at suggestion timestamps
  2. Apply FFmpeg static blur background to fill 9:16 layout
  3. Return clip metadata
  """

  raw_clip = os.path.join(
    output_dir,
    f"raw_{project_id}_{suggestion['start']}.mp4"
  )
  await _cut_raw_clip(
    video_path, suggestion['start'],
    suggestion['end'], raw_clip
  )

  width, height = _get_video_dimensions(raw_clip)
  duration = suggestion['end'] - suggestion['start']
  
  output_clip = os.path.join(
    output_dir,
    f"clip_{project_id}_{suggestion['start']}.mp4"
  )

  await _apply_blur_layout(raw_clip, output_clip, width, height)

  if os.path.exists(raw_clip):
      try:
          os.remove(raw_clip)
      except OSError:
          pass

  return {
    "file_path": output_clip,
    "start_time": suggestion['start'],
    "end_time": suggestion['end'],
    "duration": duration,
    "title": suggestion.get('title', ''),
    "reason": suggestion.get('reason', ''),
    "viral_score": suggestion.get('viral_score', 0),
    "face_count": 0,
    "layout_mode": "blur_background",
    "needs_user_confirm": False,
    "reframed": True
  }


async def _apply_blur_layout(input_path: str, output_path: str, vid_width: int, vid_height: int) -> None:
    """
    Creates a 9:16 video (1080x1920) by taking the original video, scaling and blurring it to fill the background,
    and then placing the original video (scaled to fit) in the center.
    """
    filter_complex = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:20[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )

    ffmpeg_path = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe"
    )
    if not os.path.exists(ffmpeg_path):
        ffmpeg_path = "ffmpeg"

    cmd = [
        ffmpeg_path, "-i", input_path,
        "-filter_complex", filter_complex,
        "-c:a", "copy",
        output_path, "-y"
    ]
    
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg static blur reframe failed: {result.stderr.decode()}")


def _get_video_dimensions(video_path: str) -> tuple[int, int]:
  """
  Use ffprobe to get width and height.
  """
  ffprobe_path = os.path.expandvars(
      r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin\ffprobe.exe"
  )
  if not os.path.exists(ffprobe_path):
      ffprobe_path = "ffprobe"
  cmd = [
      ffprobe_path, 
      "-v", "error", "-select_streams", "v:0", 
      "-show_entries", "stream=width,height", "-of", "json", video_path
  ]
  result = subprocess.run(cmd, capture_output=True, text=True)
  if result.returncode != 0:
      raise RuntimeError(f"FFprobe failed: {result.stderr}")
  data = json.loads(result.stdout)
  stream = data["streams"][0]
  return stream["width"], stream["height"]

async def _cut_raw_clip(
  video_path: str,
  start: float,
  end: float,
  output_path: str
) -> None:
  """
  ffmpeg -ss {start} -i {video_path} ...
  """
  duration = end - start
  ffmpeg_path = os.path.expandvars(
      r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe"
  )
  if not os.path.exists(ffmpeg_path):
      ffmpeg_path = "ffmpeg"
  cmd = [
      ffmpeg_path, "-y", "-ss", str(start), "-i", video_path,
      "-t", str(duration), "-c", "copy", output_path
  ]
  result = subprocess.run(cmd, capture_output=True, text=True)
  if result.returncode != 0:
      raise RuntimeError(f"FFmpeg failed cutting raw clip: {result.stderr}")
