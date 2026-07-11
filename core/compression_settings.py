"""
core/compression_settings.py
-----------------------------
Codec and format options that flow from the UI through the worker into
the individual compressors.
"""
from dataclasses import dataclass
from enum import Enum


class VideoCodec(Enum):
    H264 = "h264"
    AV1  = "av1"


class ImageFormat(Enum):
    ORIGINAL = "original"   # preserve input format
    JPEG     = "jpeg"       # convert to JPEG  (most compatible, lossy)
    WEBP     = "webp"       # convert to WebP  (smaller, iOS 14+ / Android 4+)
    AVIF     = "avif"       # convert to AVIF  (smallest, iOS 16+ / Android 12+)


@dataclass
class CompressionSettings:
    video_codec:  VideoCodec  = VideoCodec.H264
    image_format: ImageFormat = ImageFormat.ORIGINAL
