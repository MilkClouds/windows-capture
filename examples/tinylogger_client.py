import time
import uuid

import zmq


class CaptureClient:
    def __init__(self, server_address="tcp://127.0.0.1:5555", pub_address="tcp://127.0.0.1:5556"):
        self.context = zmq.Context()
        self.server_address = server_address
        self.pub_address = pub_address

        # Setup publisher to send commands to the server
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.bind(self.server_address)
        time.sleep(1)  # sleep for bind

        # Setup subscriber to receive responses from the server
        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect(self.pub_address)
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "")

    def start_recording(self, video_name, fps, width, height):
        self.publisher.send_string("start", zmq.SNDMORE)
        self.publisher.send_string(video_name, zmq.SNDMORE)
        self.publisher.send_string(str(fps), zmq.SNDMORE)
        self.publisher.send_string(str(width), zmq.SNDMORE)
        self.publisher.send_string(str(height))
        print("send all!")

        while True:
            message = self.subscriber.recv_string()
            if message == "start":
                timestamp = self.subscriber.recv_string()
                print(f"Recording started at {timestamp} seconds.")
                break

    def stop_recording(self):
        self.publisher.send_string("stop")

        while True:
            message = self.subscriber.recv_string()
            if message == "stop":
                timestamp = self.subscriber.recv_string()
                print(f"Recording stopped at {timestamp} seconds.")
                break


if __name__ == "__main__":
    client = CaptureClient()

    # Start recording with given parameters
    client.start_recording(f"test_{uuid.uuid4()}.mp4", 60, 1280, 1392)
    time.sleep(30)  # Record for 10 seconds

    # Stop recording
    client.stop_recording()
