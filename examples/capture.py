import threading
import time
from pathlib import Path
from queue import Queue
from typing import Optional

import av
import cv2
from pydantic import BaseModel
from tqdm import tqdm
from windows_capture import Frame, InternalCaptureControl, WindowsCapture

from video_manager import PyAVVideoManager

FPS: float = 60


class CaptureArgs(BaseModel):
    cursor_capture: Optional[bool] = True
    draw_border: Optional[bool] = None
    monitor_index: Optional[int] = None
    window_name: Optional[str] = None


class RecordArgs(BaseModel):
    video_path: str


class VideoWriterThread(threading.Thread):
    def __init__(self, video_path: str, frame_queue: Queue):
        super().__init__()
        self.video_path = Path(video_path)
        self._metadata_path = self.video_path.with_suffix(".txt")
        self._metadata_path.unlink(missing_ok=True)
        self.frame_queue = frame_queue
        self.stop_event = False
        self.pbar = tqdm()

    def run(self):
        # Open a video file container for writing using PyAV
        _start_time = None
        with PyAVVideoManager(self.video_path, "w", FPS) as video_manager:
            while not self.stop_event or not self.frame_queue.empty():
                try:
                    timestamp, frame = self.frame_queue.get(timeout=1)
                    if _start_time is None:
                        _start_time = timestamp
                    with open(self._metadata_path, "a") as f:
                        f.write(f"{timestamp}, {frame.shape}\n")
                    frame = video_manager.write_frame(frame, (timestamp - _start_time) / 1e9, pts_unit="sec")

                    # cv2.imshow("frame", frame)
                    # if cv2.waitKey(1) & 0xFF == ord("q"):
                    #     break
                    self.pbar.update()
                except Exception as e:
                    print(f"Error writing frame: {e}")
            print(f"Video writing thread stopped, {self.stop_event}")

    def stop(self):
        self.stop_event = True
        self.join()


class ScreenCapturer:
    def __init__(self, capture_args: CaptureArgs, record_args: RecordArgs):
        self.pbar = tqdm()
        self.last_called = None
        self.stop_flag = False
        self.frame_queue = Queue(maxsize=100)  # Buffer to hold frames

        self.capture = WindowsCapture(
            cursor_capture=capture_args.cursor_capture,
            draw_border=capture_args.draw_border,
            monitor_index=capture_args.monitor_index,
            window_name=capture_args.window_name,
        )
        self.capture.event(self.on_frame_arrived)
        self.capture.event(self.on_closed)

        # Initialize video writer thread
        self.video_writer = VideoWriterThread(record_args.video_path, self.frame_queue)

    def on_frame_arrived(self, frame: Frame, capture_control: InternalCaptureControl):
        # Gracefully Stop The Capture Thread
        if self.stop_flag:
            capture_control.stop()

        self.pbar.update()
        # Add frame to the queue for video writing
        if not self.frame_queue.full():
            self.frame_queue.put((time.time_ns(), frame.convert_to_bgr().frame_buffer[:, :, ::-1]))

        if self.last_called is not None:
            to_sleep = max(1 / FPS - (time.time() - self.last_called), 0)
            time.sleep(to_sleep)
        self.last_called = time.time()

    # Called When The Capture Item Closes Usually When The Window Closes, Capture
    # Session Will End After This Function Ends
    def on_closed(self):
        print("Capture Session Closed")

    def start(self):
        self.video_writer.start()
        self.capture.start_free_threaded()

    def stop(self):
        self.stop_flag = True
        self.video_writer.stop()


if __name__ == "__main__":
    capture_args = CaptureArgs(cursor_capture=True, draw_border=False, monitor_index=None, window_name=None)
    record_args = RecordArgs(video_path="output.mp4")

    capturer = ScreenCapturer(capture_args, record_args)
    try:
        capturer.start()
        time.sleep(20)  # Adjust the time based on how long you want to capture
    except KeyboardInterrupt:
        print("KeyboardInterrupt: Stopping capture...")
    finally:
        capturer.stop()
        print("Done")
