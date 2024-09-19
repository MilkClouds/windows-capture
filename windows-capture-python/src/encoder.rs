use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::path::Path;
use std::sync::{Arc, Mutex};

use windows_capture::encoder::{
    AudioSettingsBuilder, AudioSettingsSubType, ContainerSettingsBuilder,
    ContainerSettingsSubType, VideoEncoder as RustVideoEncoder, VideoSettingsBuilder,
    VideoSettingsSubType,
};
use windows_capture::frame::Frame;
use windows_capture::settings::ColorFormat;

#[pyclass]
struct VideoEncoder {
    encoder: Arc<Mutex<Option<RustVideoEncoder>>>,
}

#[pymethods]
impl VideoEncoder {
    #[new]
    fn new(
        video_settings: &PyAny,
        audio_settings: &PyAny,
        container_settings: &PyAny,
        path: &str,
    ) -> PyResult<Self> {
        // Parse settings from Python objects
        let video_settings = parse_video_settings(video_settings)?;
        let audio_settings = parse_audio_settings(audio_settings)?;
        let container_settings = parse_container_settings(container_settings)?;

        // Create the VideoEncoder
        let encoder = RustVideoEncoder::new(video_settings, audio_settings, container_settings, path)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        Ok(Self {
            encoder: Arc::new(Mutex::new(Some(encoder))),
        })
    }

    fn send_frame(&self, frame_data: &PyBytes, width: u32, height: u32) -> PyResult<()> {
        let mut encoder_lock = self.encoder.lock().unwrap();

        if let Some(encoder) = encoder_lock.as_mut() {
            // Convert frame_data to Frame
            let buffer = frame_data.as_bytes();

            let mut frame = Frame::from_buffer(buffer, width, height, ColorFormat::Bgra8)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            // Send the frame to the encoder
            encoder
                .send_frame(&mut frame)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(())
        } else {
            Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "VideoEncoder has already been finalized.",
            ))
        }
    }

    fn finish(&self) -> PyResult<()> {
        let mut encoder_lock = self.encoder.lock().unwrap();

        if let Some(encoder) = encoder_lock.take() {
            encoder
                .finish()
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
            Ok(())
        } else {
            Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "VideoEncoder has already been finalized.",
            ))
        }
    }
}

#[pymodule]
fn videoencoder(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<VideoEncoder>()?;
    Ok(())
}

// Parsing functions
fn parse_video_settings(py_video_settings: &PyAny) -> PyResult<VideoSettingsBuilder> {
    let bitrate: u32 = py_video_settings.get_item("bitrate")?.extract()?;
    let width: u32 = py_video_settings.get_item("width")?.extract()?;
    let height: u32 = py_video_settings.get_item("height")?.extract()?;
    let frame_rate: u32 = py_video_settings.get_item("frame_rate")?.extract()?;
    let sub_type_str: &str = py_video_settings.get_item("sub_type")?.extract()?;

    let sub_type = match sub_type_str {
        "HEVC" => VideoSettingsSubType::HEVC,
        "H264" => VideoSettingsSubType::H264,
        _ => {
            return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Invalid video sub_type",
            ))
        }
    };

    Ok(VideoSettingsBuilder::new(width, height)
        .bitrate(bitrate)
        .frame_rate(frame_rate)
        .sub_type(sub_type))
}

fn parse_audio_settings(py_audio_settings: &PyAny) -> PyResult<AudioSettingsBuilder> {
    let bitrate: u32 = py_audio_settings.get_item("bitrate")?.extract()?;
    let channel_count: u32 = py_audio_settings.get_item("channel_count")?.extract()?;
    let sample_rate: u32 = py_audio_settings.get_item("sample_rate")?.extract()?;
    let bits_per_sample: u32 = py_audio_settings.get_item("bit_per_sample")?.extract()?;
    let sub_type_str: &str = py_audio_settings.get_item("sub_type")?.extract()?;

    let sub_type = match sub_type_str {
        "AAC" => AudioSettingsSubType::AAC,
        _ => {
            return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Invalid audio sub_type",
            ))
        }
    };

    Ok(AudioSettingsBuilder::new()
        .bitrate(bitrate)
        .channel_count(channel_count)
        .sample_rate(sample_rate)
        .bit_per_sample(bits_per_sample)
        .sub_type(sub_type))
}

fn parse_container_settings(py_container_settings: &PyAny) -> PyResult<ContainerSettingsBuilder> {
    let sub_type_str: &str = py_container_settings.get_item("sub_type")?.extract()?;

    let sub_type = match sub_type_str {
        "MPEG4" => ContainerSettingsSubType::MPEG4,
        _ => {
            return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Invalid container sub_type",
            ))
        }
    };

    Ok(ContainerSettingsBuilder::new().sub_type(sub_type))
}
