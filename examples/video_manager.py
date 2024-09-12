import datetime
import enum
import functools
from fractions import Fraction
from pathlib import Path
from typing import Generator

import av
import av.container
import numpy as np
from loguru import logger
from pydantic import BaseModel

SECOND_TYPE = float | Fraction
DUPLICATE_TOLERANCE_SECOND = Fraction(1, 120)


@functools.lru_cache(maxsize=128)
def get_video_container(video_path: Path, mode="r") -> av.container.InputContainer:
    return av.open(video_path)


class OnDiskVideoFrame(BaseModel):
    source: str  # assumes file path.
    timestamp: SECOND_TYPE

    # converts source from Path to str if needed
    def __init__(self, **data):
        if isinstance(data["source"], Path):
            data["source"] = str(data["source"])
        super().__init__(**data)


class PTSUnit(enum.StrEnum):
    PTS: str = "pts"
    SEC: str = "sec"


class PyAVVideoManager:
    """
    This is a video manager that uses PyAV to read and write videos.

    supports VFR(Variable Frame Rate) for writing.
        References:
        - https://stackoverflow.com/questions/65213302/how-to-write-variable-frame-rate-videos-in-python
        - https://github.com/PyAV-Org/PyAV/blob/main/examples/numpy/generate_video_with_pts.py
        Design Reference:
        - https://pytorch.org/vision/stable/generated/torchvision.io.read_video.html#torchvision.io.read_video
    """

    def __init__(self, video_path: Path, mode="r", fps: float | None = None):
        """
        Args:
            video_path (Path): The path to the video file.
            mode (str): The mode to open the video in. Either "r" for reading or "w" for writing.
            fps (int): The fps of the video. If None, you must provide pts for each frame.
        """
        self.video_path = Path(video_path)
        self.mode = mode
        if mode == "w":
            self.container = av.open(video_path, "w")
            self.stream = self.container.add_stream("h264", rate=-1)  # pyav's default rate is 24.
            self.stream.pix_fmt = "yuv420p"
            # fine-grained time_base to enable VFR.
            self._time_base = Fraction(1, 60000)
            self.stream.time_base = self._time_base
            self.stream.codec_context.time_base = self._time_base
            self.stream.codec_context.bit_rate = 20 * (2**20)  # default is 1024000
            # ensure this to reduce random-read latency. this make sure that the key frame is every 4 frames.
            self.stream.codec_context.gop_size = 6

            # set fps if provided
            self.fps = fps
            self.past_pts: int = None
        elif mode == "r":
            # may be ~3x faster if av.open is cached
            self.container = av.open(video_path, "r")
            # may be 5x faster if thread_type is AUTO! https://pyav.org/docs/develop/cookbook/basics.html#threading
            # self.container.streams.video[0].thread_type = "AUTO"

    def write_frame(
        self,
        frame: av.VideoFrame | np.ndarray,
        pts: int | SECOND_TYPE | None = None,
        pts_unit: PTSUnit = PTSUnit.PTS,
    ) -> OnDiskVideoFrame:
        """
        Write a frame to the video. If pts is None, it will be set to the next frame.
        Args:
            frame (av.VideoFrame | np.ndarray): The frame to write.
            pts (int if pts_unit='pts' | float/Fraction if pts_unit='sec' | None): The pts of the frame. If None, it will be set to the next frame.
            pts_unit (PTSUnit): The unit of the pts. Default is PTS.
        Returns:
            OnDiskVideoFrame: The frame that was written.
        """
        assert self.mode == "w", "VideoManager is not in write mode."
        assert isinstance(frame, av.VideoFrame) or isinstance(
            frame, np.ndarray
        ), "frame must be av.VideoFrame or np.ndarray."

        if pts is None:
            assert self.fps is not None, "fps must be provided if pts is not provided."
            # validate pts_unit and pts
            if pts_unit == PTSUnit.SEC:
                assert isinstance(pts, SECOND_TYPE), f"pts must be {SECOND_TYPE} if pts_unit is 'sec'."
            elif pts_unit == PTSUnit.PTS:
                assert isinstance(pts, int), "pts must be int if pts_unit is 'pts'."
            else:
                raise ValueError(f"Invalid pts_unit: {pts_unit}")

        # convert np.ndarray to av.VideoFrame
        if isinstance(frame, np.ndarray):
            frame = av.VideoFrame.from_ndarray(frame, format="rgb24")

        # set pts to the next frame if not provided
        if pts is None:
            if self.past_pts is None:
                pts = 0
            else:
                pts = self.past_pts + self.sec_to_pts(Fraction(1, self.fps))
            pts_unit = PTSUnit.PTS

        # standardize pts unit
        pts_as_pts: int = pts if pts_unit == PTSUnit.PTS else self.sec_to_pts(pts)
        pts_as_sec: SECOND_TYPE = pts if pts_unit == PTSUnit.SEC else self.pts_to_sec(pts)

        # filter duplicate frames
        if self.past_pts is not None and pts_as_pts - self.past_pts < self.sec_to_pts(DUPLICATE_TOLERANCE_SECOND):
            logger.warning(
                f"Duplicate frame detected at {float(pts_as_sec):.2f}(before: {float(self.pts_to_sec(self.past_pts)):.2f}) while processing {self.video_path}. Skipping."
            )
            return OnDiskVideoFrame(source=self.video_path, timestamp=pts_as_sec)

        frame.pts = pts_as_pts
        self.stream.width, self.stream.height = frame.width, frame.height  # only first call is valid
        for packet in self.stream.encode(frame):
            self.container.mux(packet)
        self.past_pts = pts
        return OnDiskVideoFrame(source=self.video_path, timestamp=pts_as_sec)

    def pts_to_sec(self, pts: int) -> Fraction:
        return pts * self.stream.codec_context.time_base

    def sec_to_pts(self, sec: SECOND_TYPE) -> int:
        return int(sec / self.stream.codec_context.time_base)

    def read_frame(self, *args, **kwargs) -> av.VideoFrame:
        # use self.read_frames
        for frame in self.read_frames(*args, **kwargs):
            return frame
        raise ValueError(f"Frame not found at {args[0]:.2f} at {self.video_path}")

    def read_frames(
        self,
        start_pts: int | SECOND_TYPE = 0.0,
        end_pts: int | SECOND_TYPE | None = None,
        pts_unit: PTSUnit = PTSUnit.PTS,
    ) -> Generator[av.VideoFrame, None, None]:
        assert self.mode == "r", "VideoManager is not in read mode."
        assert pts_unit == PTSUnit.SEC, "Only SEC is supported for read_frames currently."
        assert isinstance(start_pts, SECOND_TYPE), f"start_pts must be {SECOND_TYPE}."
        end_pts = float("inf") if end_pts is None else end_pts
        assert isinstance(end_pts, SECOND_TYPE), f"end_pts must be {SECOND_TYPE}."

        logger.debug(self.container.streams.video[0].average_rate)
        self.container.seek(int(av.time_base * start_pts))
        for frame in self.container.decode(video=0):
            # if frame.pts * frame.time_base > end_pts:
            if frame.time > end_pts:
                break
            yield frame

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        if self.mode == "w":
            # clear buffer
            for packet in self.stream.encode():
                self.container.mux(packet)
        self.container.close()


"""
# av/video/frame.pyi
class VideoFrame(Frame):
    format: VideoFormat
    pts: int
    time: float
    planes: tuple[VideoPlane, ...]
    width: int
    height: int
    key_frame: bool
    interlaced_frame: bool
    pict_type: int
    colorspace: int
    color_range: int

[Parsed_showinfo_0 @ 000001e1307feec0] config in time_base: 1/60000, frame_rate: 60/1
[Parsed_showinfo_0 @ 000001e1307feec0] config out time_base: 0/0, frame_rate: 0/0
[Parsed_showinfo_0 @ 000001e1307feec0] n:   0 pts:      0 pts_time:0       duration:   1000 duration_time:0.0166667 fmt:yuv420p cl:left sar:1/1 s:1936x1120 i:P iskey:1 type:I checksum:1BC86BCE plane_checksum:[9690BC43 3EBE43E8 F75B6B94] mean:[152 118 138] stdev:[57.8 15.9 15.1]
[Parsed_showinfo_0 @ 000001e1307feec0] n:   1 pts:   1000 pts_time:0.0166667 duration:   1000 duration_time:0.0166667 fmt:yuv420p cl:left sar:1/1 s:1936x1120 i:P iskey:0 type:P checksum:DE3A7784 plane_checksum:[0554C822 2A36436D 68636BE6] mean:[152 118 138] stdev:[57.8 15.9 15.1]
[Parsed_showinfo_0 @ 000001e1307feec0] color_range:tv color_space:unknown color_primaries:unknown color_trc:unknown
[Parsed_showinfo_0 @ 000001e1307feec0] n:   2 pts:   2000 pts_time:0.0333333 duration:   1000 duration_time:0.0166667 fmt:yuv420p cl:left sar:1/1 s:1936x1120 i:P iskey:0 type:P checksum:B973CE89 plane_checksum:[708E0C6E 25095030 757471EB] mean:[152 118 138] stdev:[57.8 15.9 15.1]
[Parsed_showinfo_0 @ 000001e1307feec0] color_range:tv color_space:unknown color_primaries:unknown color_trc:unknown
"""

if __name__ == "__main__":
    video_path = Path("test.mp4")
    manager = PyAVVideoManager(video_path, mode="w")
    total_frames = 60
    for frame_i in range(total_frames):
        img = np.empty((48, 64, 3))
        img[:, :, 0] = 0.5 + 0.5 * np.sin(2 * np.pi * (0 / 3 + frame_i / total_frames))
        img[:, :, 1] = 0.5 + 0.5 * np.sin(2 * np.pi * (1 / 3 + frame_i / total_frames))
        img[:, :, 2] = 0.5 + 0.5 * np.sin(2 * np.pi * (2 / 3 + frame_i / total_frames))
        img = (img * 255).astype(np.uint8)
        # frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        # frame.pts = frame_i**2
        # # frame.pts = frame_i * 1000 + int(frame_i % 3 == 0)
        # # frame.pts = frame_i**2
        # # frame.pts = frame_i * 1000
        # frame.time_base = Fraction(1, 1024)
        # print(frame.time_base, frame.time, frame.dts)

        # manager.write_frame(img)
        sec = Fraction(1) / 60 * frame_i
        # sec = sec**2
        manager.write_frame(img, sec, pts_unit="sec")
    manager.close()

    print("Reading written video")
    manager = PyAVVideoManager(video_path, mode="r")
    for packet in manager.read_frames(start_pts=0.5, pts_unit="sec"):
        print(packet.pts, packet.time, packet.dts)
        print(packet.to_ndarray(format="rgb24").shape)

    manager.read_frame(pts_unit="sec")
