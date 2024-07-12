use std::{
    io::{self, Write},
    sync::{Arc, Mutex},
    thread,
    time::{Instant, SystemTime, UNIX_EPOCH},
};

use serde::Deserialize;
use windows_capture::{
    capture::GraphicsCaptureApiHandler,
    encoder::{VideoEncoder, VideoEncoderQuality, VideoEncoderType},
    frame::Frame,
    graphics_capture_api::InternalCaptureControl,
    settings::{ColorFormat, CursorCaptureSettings, DrawBorderSettings, Settings},
    window::Window
};
use zmq;

#[derive(Deserialize)]
struct Config {
    window_name: Option<String>,
    monitor_index: Option<usize>,
    cursor_capture: Option<bool>,
    draw_border: Option<bool>,
    subscribe_addr: String,
    publish_addr: String,
}

struct Flags {
    capturing: Arc<Mutex<bool>>,
    video_encoder: Arc<Mutex<Option<VideoEncoder>>>,
}

impl std::fmt::Display for Flags {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Capturing: {}, Video Encoder: {}",
            self.capturing.lock().unwrap(),
            "..."
        )
    }
}

// This struct will be used to handle the capture events.
struct Capture {
    flags: Flags,
    start: Instant,
    was_capturing: bool,
}

impl GraphicsCaptureApiHandler for Capture {
    type Flags = Flags;
    type Error = Box<dyn std::error::Error + Send + Sync>;

    fn new(message: Self::Flags) -> Result<Self, Self::Error> {
        println!("Got The Flag: {message}");

        Ok(Self {
            flags: message,
            start: Instant::now(),
            was_capturing: false,
        })
    }

    fn on_frame_arrived(
        &mut self,
        frame: &mut Frame,
        capture_control: InternalCaptureControl,
    ) -> Result<(), Self::Error> {
        let capturing = *self.flags.capturing.lock().unwrap();
        if capturing {
            if !self.was_capturing {
                self.start = Instant::now();
                self.was_capturing = true;
                println!("\nStarted capturing!");
            }
            print!(
                "\rRecording for: {} seconds",
                self.start.elapsed().as_secs()
            );
            io::stdout().flush()?;
            (*self.flags.video_encoder.lock().unwrap()).as_mut().unwrap().send_frame(frame)?;
        } else {
            if self.was_capturing {
                self.was_capturing = false;
                (*self.flags.video_encoder.lock().unwrap()).take().unwrap().finish()?;
                // capture_control.stop();
                println!("\nStopped capturing!");
                self.start = Instant::now();
            } else {
                print!(
                    "\rWaiting for: {} seconds",
                    self.start.elapsed().as_secs()
                );
                io::stdout().flush()?;
            }
        }
        // if self.start.elapsed().as_secs() >= 6 {
        //     // Finish the encoder and save the video.
        //     (*self.flags.video_encoder.lock().unwrap()).take().unwrap().finish()?;

        //     capture_control.stop();

        //     // Because there wasn't any new lines in previous prints
        //     println!();
        // }

        Ok(())
    }

    fn on_closed(&mut self) -> Result<(), Self::Error> {
        println!("Capture Session Closed");
        Ok(())
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Read configuration from config.yaml
    let config: Config = serde_yaml::from_reader(std::fs::File::open("config.yaml")?)?;
    
    let cursor_capture = match config.cursor_capture {
        Some(true) => CursorCaptureSettings::WithCursor,
        Some(false) => CursorCaptureSettings::WithoutCursor,
        None => CursorCaptureSettings::Default,
    };

    let draw_border = match config.draw_border {
        Some(true) => DrawBorderSettings::WithBorder,
        Some(false) => DrawBorderSettings::WithoutBorder,
        None => DrawBorderSettings::Default,
    };

    let capturing = Arc::new(Mutex::new(false));
    let video_encoder = Arc::new(Mutex::new(None));
    // let capturing_clone = capturing.clone();
    // let video_encoder_clone = video_encoder.clone();
    let flags = Flags {
        capturing: capturing.clone(),
        video_encoder: video_encoder.clone(),
    };

    let settings = {
        let window = Window::from_contains_name(config.window_name.as_ref().unwrap()).expect("Window not found!");
        println!("Window title: {}", window.title()?);
        Settings::new(
            window,
            cursor_capture,
            draw_border,
            ColorFormat::Rgba8,
            flags,
        )
    };

    // let settings = if config.window_name.is_some() {
    //     let window = Window::from_contains_name(config.window_name.as_ref().unwrap())?;
    //     Settings::new(
    //         window,
    //         cursor_capture,
    //         draw_border,
    //         ColorFormat::Rgba8,
    //         "Yea This Works".to_string(),
    //     )
    // } else {
    //     let monitor = Monitor::from_index(config.monitor_index.unwrap())?;
    //     Settings::new(
    //         monitor,
    //         cursor_capture,
    //         draw_border,
    //         ColorFormat::Rgba8,
    //         "Yea This Works".to_string(),
    //     )
    // };
    let context = zmq::Context::new();
    let subscriber = context.socket(zmq::SUB)?;
    let publisher = context.socket(zmq::PUB)?;

    subscriber.connect(&config.subscribe_addr)?;
    subscriber.set_subscribe(b"")?;
    publisher.bind(&config.publish_addr)?;

    thread::spawn(move || {
        loop {
            println!("Waiting for message...");
            let message = subscriber.recv_string(0).unwrap().unwrap();
            println!("Received message: {}", message);
            let timestamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();

            match message.as_str() {
                "start" => {
                    // Assuming the next messages will be video_name and fps
                    let video_name = subscriber.recv_string(0).unwrap().unwrap();
                    println!("Received video_name: {}", video_name);
                    let fps: u32 = subscriber.recv_string(0).unwrap().unwrap().parse().unwrap();
                    println!("Received fps: {}", fps);
                    let width: u32 = subscriber.recv_string(0).unwrap().unwrap().parse().unwrap();
                    println!("Received width: {}", width);
                    let height: u32 = subscriber.recv_string(0).unwrap().unwrap().parse().unwrap();
                    println!("Received height: {}", height);

                    let encoder = VideoEncoder::new(
                        VideoEncoderType::Hevc,
                        VideoEncoderQuality::HD1080p,
                        width,
                        height,
                        video_name.clone(),
                        Some(fps),
                    ).unwrap();
                    let mut shared_video_encoder = video_encoder.lock().unwrap();
                    *shared_video_encoder = Some(encoder);

                    let mut capturing = capturing.lock().unwrap();
                    *capturing = true;

                    println!("Received start signal with video_name: {}, fps: {}, width: {}, height: {}", video_name, fps, width, height);
                    publisher.send("start", 0).unwrap();
                    publisher.send(&timestamp.to_string(), 0).unwrap();
                }
                "stop" => {
                    let mut capturing = capturing.lock().unwrap();
                    *capturing = false;
                    println!("Received stop signal");
                    publisher.send("stop", 0).unwrap();
                    publisher.send(&timestamp.to_string(), 0).unwrap();
                }
                _ => {
                    println!("Received unknown signal: {}", message);
                }
            }
        }
    });

    // Handling Ctrl+C signal to stop capturing gracefully
    // ctrlc::set_handler(move || {
    //     let mut capturing = capturing_clone.lock().unwrap();
    //     *capturing = false;

    //     if let Some(encoder) = video_encoder_clone.lock().unwrap().take() {
    //         encoder.finish().expect("Failed to finish encoder");
    //     }

    //     println!("\nReceived Ctrl+C! Stopping capture...");
    //     std::process::exit(0);
    // })?;

    Capture::start(settings).expect("Screen Capture Failed");
    Ok(())
}
