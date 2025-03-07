#!/usr/bin/env python3
"""
Linux Cloud STT Notepad - A minimalist notepad with speech-to-text capabilities
using the OpenAI API for Linux desktop environments.
"""

import sys
import os
import json
import time
import math
import tempfile
import shutil
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write
from pydub import AudioSegment
from dotenv import load_dotenv
import datetime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QComboBox, QTextEdit, QTabWidget, 
    QGroupBox, QFormLayout, QLineEdit, QFileDialog, QMessageBox,
    QCheckBox, QProgressBar, QSystemTrayIcon, QMenu, QAction,
    QGridLayout, QStatusBar, QScrollArea
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSettings, QDir, QFile, QIODevice
)
from PyQt5.QtGui import QIcon, QTextCursor, QCloseEvent
import base64
import requests
from openai import OpenAI

# Load environment variables from .env file
load_dotenv()

# OpenAI API key from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("Error: OPENAI_API_KEY not found in .env file")
    sys.exit(1)

# Constants
SAMPLE_RATE = 44100  # Sample rate for audio recording
CONFIG_DIR = os.path.join(QDir.homePath(), ".config", "linux-cloud-stt-notepad")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")


class AudioRecorder(QThread):
    """Thread for recording audio without blocking the UI"""
    update_status = pyqtSignal(str)
    update_timer = pyqtSignal(int)  # Signal to update recording time
    update_volume = pyqtSignal(float)  # Signal to update volume meter
    
    def __init__(self, sample_rate=SAMPLE_RATE):
        super().__init__()
        self.sample_rate = sample_rate
        self.recording = False
        self.paused = False
        self.audio_data = []
        self.temp_file = None
        self.start_time = 0
        self.elapsed_time = 0
        print("AudioRecorder initialized")
    
    def run(self):
        """Start recording audio"""
        self.recording = True
        self.paused = False
        self.audio_data = []
        self.start_time = time.time()
        self.elapsed_time = 0
        
        self.update_status.emit("Recording started...")
        self.update_timer.emit(0)
        print("Recording started")
        
        with sd.InputStream(samplerate=self.sample_rate, channels=1, callback=self._audio_callback):
            while self.recording:
                # Update timer every 100ms
                if not self.paused:
                    current_time = int(time.time() - self.start_time)
                    if current_time != self.elapsed_time:
                        self.elapsed_time = current_time
                        self.update_timer.emit(self.elapsed_time)
                        print(f"Timer update: {self.elapsed_time} seconds")
                time.sleep(0.1)
        
        print("Recording stopped")
        
        if self.audio_data:
            # First save as WAV (temporary)
            temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
            audio_array = np.concatenate(self.audio_data, axis=0)
            write(temp_wav.name, self.sample_rate, audio_array)
            
            # Convert WAV to MP3 using pydub (much smaller file size)
            self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            sound = AudioSegment.from_wav(temp_wav.name)
            
            # Normalize audio to improve transcription quality
            sound = sound.normalize()
            sound.export(self.temp_file.name, format="mp3", bitrate="128k")
            
            # Remove the temporary WAV file
            os.unlink(temp_wav.name)
            
            self.update_status.emit(f"Recording saved to temporary file: {self.temp_file.name}")
        else:
            self.update_status.emit("No audio data recorded")
    
    def _audio_callback(self, indata, frames, time, status):
        """Callback function for audio recording"""
        if not self.paused and self.recording:
            # Calculate volume level (RMS)
            volume_norm = np.linalg.norm(indata) / np.sqrt(frames)
            
            # Scale to a reasonable range (0-100)
            # The scaling factor may need adjustment based on your microphone sensitivity
            volume_level = min(100, volume_norm * 20)
            
            # Emit signal with volume level
            self.update_volume.emit(volume_level)
            
            self.audio_data.append(indata.copy())
    
    def stop(self):
        """Stop recording"""
        self.recording = False
        self.update_status.emit("Recording stopped")
        print("Recording stop requested")
    
    def pause(self):
        """Pause recording"""
        self.paused = not self.paused
        status = "paused" if self.paused else "resumed"
        
        if self.paused:
            # Store the current elapsed time when paused
            self.elapsed_time = int(time.time() - self.start_time)
            print(f"Recording paused at {self.elapsed_time} seconds")
        else:
            # Adjust start time to account for pause duration
            self.start_time = time.time() - self.elapsed_time
            print(f"Recording resumed from {self.elapsed_time} seconds")
            
        self.update_status.emit(f"Recording {status}")
    
    def clear(self):
        """Clear recorded audio data"""
        self.audio_data = []
        self.elapsed_time = 0
        self.update_timer.emit(0)
        print("Recording cleared, timer reset to 0")
        if self.temp_file and os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)
            self.temp_file = None
        self.update_status.emit("Recording cleared")
    
    def get_audio_file(self):
        """Return the path to the recorded audio file"""
        return self.temp_file.name if self.temp_file else None


class TranscriptionWorker(QThread):
    """Thread for handling OpenAI API transcription"""
    transcription_complete = pyqtSignal(str)
    transcription_progress = pyqtSignal(int, int)  # current chunk, total chunks
    transcription_error = pyqtSignal(str)
    update_status = pyqtSignal(str)
    
    def __init__(self, audio_file, api_key):
        super().__init__()
        self.audio_file = audio_file
        self.api_key = api_key
        self.max_chunk_duration = 3600  # 1 hour per chunk in seconds
        self.chunk_overlap = 5  # 5 seconds overlap between chunks
        
    def run(self):
        """Start the transcription process"""
        if not self.audio_file or not os.path.exists(self.audio_file):
            self.transcription_error.emit("No audio file available for transcription")
            return
        
        try:
            self.update_status.emit("Preparing audio for OpenAI API...")
            
            # Check audio duration
            audio = AudioSegment.from_file(self.audio_file)
            duration_seconds = len(audio) / 1000  # pydub uses milliseconds
            
            # If audio is short enough, transcribe directly
            if duration_seconds <= self.max_chunk_duration:
                self.update_status.emit("Sending request to OpenAI API...")
                transcript = self._transcribe_file(self.audio_file)
                if transcript:
                    self.transcription_complete.emit(transcript)
                    self.update_status.emit("Transcription completed successfully")
                else:
                    self.transcription_error.emit("No transcription returned from API")
            else:
                # For longer audio, split into chunks and transcribe each
                self.update_status.emit(f"Audio duration: {duration_seconds:.1f} seconds. Splitting into chunks...")
                transcripts = self._transcribe_long_audio(audio, duration_seconds)
                if transcripts:
                    # Combine all transcripts
                    full_transcript = " ".join(transcripts)
                    self.transcription_complete.emit(full_transcript)
                    self.update_status.emit("Transcription of all chunks completed successfully")
                else:
                    self.transcription_error.emit("Failed to transcribe audio chunks")
                
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Transcription error: {str(e)}")
            print(f"Error details:\n{error_details}")
            self.transcription_error.emit(f"Error during transcription: {str(e)}")
    
    def _transcribe_file(self, file_path):
        """Transcribe a single audio file using the OpenAI API"""
        try:
            # Directly use the API without the client object to avoid proxies error
            import requests
            
            url = "https://api.openai.com/v1/audio/transcriptions"
            headers = {
                "Authorization": f"Bearer {self.api_key}"
            }
            
            with open(file_path, "rb") as audio_file:
                files = {
                    "file": ("audio.mp3", audio_file, "audio/mpeg"),
                    "model": (None, "whisper-1"),
                    "response_format": (None, "text")
                }
                
                self.update_status.emit("Sending request to OpenAI API...")
                response = requests.post(url, headers=headers, files=files)
                
                if response.status_code == 200:
                    return response.text
                else:
                    error_msg = f"API Error: {response.status_code}, {response.text}"
                    print(error_msg)
                    self.update_status.emit(error_msg)
                    return None
                
        except Exception as e:
            error_msg = f"Error transcribing file: {str(e)}"
            self.update_status.emit(error_msg)
            print(f"File path for transcription: {file_path}")
            print(f"Transcription error details: {error_msg}")
            import traceback
            traceback.print_exc()
            return None
            
    def _transcribe_long_audio(self, audio, duration_seconds):
        """Split long audio into chunks and transcribe each chunk"""
        try:
            # Calculate number of chunks needed
            num_chunks = math.ceil(duration_seconds / self.max_chunk_duration)
            self.update_status.emit(f"Splitting audio into {num_chunks} chunks...")
            
            # Create temp directory for chunks
            temp_dir = tempfile.mkdtemp()
            chunk_files = []
            transcripts = []
            
            # Split audio into chunks with overlap
            for i in range(num_chunks):
                self.transcription_progress.emit(i+1, num_chunks)
                
                start_ms = i * self.max_chunk_duration * 1000 - (i > 0) * self.chunk_overlap * 1000
                start_ms = max(0, start_ms)  # Ensure we don't go negative
                
                end_ms = min((i + 1) * self.max_chunk_duration * 1000, len(audio))
                
                # Extract chunk
                chunk = audio[start_ms:end_ms]
                chunk_file = os.path.join(temp_dir, f"chunk_{i}.mp3")
                chunk.export(chunk_file, format="mp3", bitrate="128k")
                chunk_files.append(chunk_file)
                
                # Transcribe chunk
                self.update_status.emit(f"Transcribing chunk {i+1} of {num_chunks}...")
                transcript = self._transcribe_file(chunk_file)
                if transcript:
                    transcripts.append(transcript)
            
            # Clean up temp files
            for file in chunk_files:
                os.unlink(file)
            os.rmdir(temp_dir)
            
            return transcripts
        except Exception as e:
            self.update_status.emit(f"Error processing long audio: {str(e)}")
            import traceback
            traceback.print_exc()
            return []


class OptimizationWorker(QThread):
    """Thread for handling OpenAI API text optimization"""
    optimization_complete = pyqtSignal(str)
    optimization_error = pyqtSignal(str)
    update_status = pyqtSignal(str)
    
    def __init__(self, text, api_key):
        super().__init__()
        self.text = text
        self.api_key = api_key
        
    def run(self):
        """Start the optimization process"""
        if not self.text:
            self.optimization_error.emit("No text available for optimization")
            return
        
        try:
            self.update_status.emit("Sending text to OpenAI API for optimization...")
            
            # Use direct API call with requests to avoid proxies error
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant that improves text transcriptions. Your task is to correct typos, improve clarity, and format the text into proper paragraphs. Preserve the original meaning and content, but make it more readable."},
                    {"role": "user", "content": f"Please optimize this transcription for readability:\n\n{self.text}"}
                ],
                "temperature": 0.3,
                "max_tokens": 4000
            }
            
            response = requests.post(url, headers=headers, json=data)
            
            if response.status_code == 200:
                response_data = response.json()
                optimized_text = response_data['choices'][0]['message']['content']
                
                if optimized_text:
                    self.optimization_complete.emit(optimized_text)
                    self.update_status.emit("Text optimization completed successfully")
                else:
                    self.optimization_error.emit("No optimized text returned from API")
            else:
                error_msg = f"API Error: {response.status_code}, {response.text}"
                print(error_msg)
                self.optimization_error.emit(error_msg)
                
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Optimization error: {str(e)}")
            print(f"Error details:\n{error_details}")
            self.optimization_error.emit(f"Error during optimization: {str(e)}")


class MainWindow(QMainWindow):
    """Main application window"""
    def __init__(self):
        super().__init__()
        
        # Initialize variables
        self.recorder = AudioRecorder()
        self.transcription_worker = None
        self.optimization_worker = None
        self.recording_timer = None
        self.recording_seconds = 0
        self.device_indices = {}
        self.last_transcript = ""
        self.continue_with_optimization = False
        self.append_transcriptions = True
        
        # Initialize settings
        self._ensure_config_dir()
        self.settings = self._load_settings()
        
        # Create and set up the status bar before initializing the UI
        self.myStatusBar = QStatusBar()
        self.setStatusBar(self.myStatusBar)
        
        # Initialize UI
        self.init_ui()
        
        # Create system tray icon
        self.create_tray_icon()
        
        # Connect recorder signals
        self.recorder.update_volume.connect(self.update_volume_meter)
        self.recorder.update_timer.connect(self.update_timer)
        self.recorder.update_status.connect(self.update_status)
        
        # Populate audio devices
        self.populate_audio_devices()
        
        # Load settings
        self.settings = self._load_settings()
        self.update_status("Ready")


    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle('Linux Cloud STT Notepad')
        self.setGeometry(100, 100, 800, 600)

        # Create main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout()
        main_widget.setLayout(layout)

        # Create tab widget
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)

        # Create main tab
        main_tab = QWidget()
        main_layout = QVBoxLayout()
        main_tab.setLayout(main_layout)
        self.tab_widget.addTab(main_tab, "Main")

        # Audio device selection
        audio_device_layout = QHBoxLayout()
        audio_device_layout.addWidget(QLabel("Audio Input Source:"))
        self.audio_device_combo = QComboBox()
        audio_device_layout.addWidget(self.audio_device_combo)
        save_device_btn = QPushButton("Save")
        save_device_btn.setToolTip("Save selected audio device")
        save_device_btn.clicked.connect(self.save_audio_device)
        audio_device_layout.addWidget(save_device_btn)
        main_layout.addLayout(audio_device_layout)
        
        # Main controls section
        controls_section = QHBoxLayout()
        
        # === AUDIO CONTROLS GROUP ===
        audio_controls_group = QGroupBox("Audio Controls")
        audio_controls_layout = QHBoxLayout()
        
        # Record button
        self.record_btn = QPushButton("Record")
        self.record_btn.setIcon(QIcon.fromTheme("media-record", QIcon.fromTheme("media-playback-start")))
        self.record_btn.setToolTip("Start recording audio")
        self.record_btn.setStyleSheet("background-color: red; color: white;")
        self.record_btn.clicked.connect(self.start_recording)
        self.record_btn.setMinimumHeight(40)
        self.record_btn.setMinimumWidth(80)
        audio_controls_layout.addWidget(self.record_btn)
        
        # Pause button
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
        self.pause_btn.setToolTip("Pause recording")
        self.pause_btn.setStyleSheet("background-color: yellow; color: black;")
        self.pause_btn.clicked.connect(self.pause_recording)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setMinimumHeight(40)
        self.pause_btn.setMinimumWidth(80)
        audio_controls_layout.addWidget(self.pause_btn)
        
        # Stop button
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setIcon(QIcon.fromTheme("media-playback-stop"))
        self.stop_btn.setToolTip("Stop recording")
        self.stop_btn.setStyleSheet("background-color: gray; color: white;")
        self.stop_btn.clicked.connect(self.stop_recording)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setMinimumWidth(80)
        audio_controls_layout.addWidget(self.stop_btn)
        
        # Clear button
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setIcon(QIcon.fromTheme("edit-clear", QIcon.fromTheme("edit-delete")))
        self.clear_btn.setToolTip("Clear recording")
        self.clear_btn.setStyleSheet("background-color: lightgray; color: black;")
        self.clear_btn.clicked.connect(self.clear_recording)
        self.clear_btn.setEnabled(False)
        self.clear_btn.setMinimumHeight(40)
        self.clear_btn.setMinimumWidth(80)
        audio_controls_layout.addWidget(self.clear_btn)
        
        audio_controls_group.setLayout(audio_controls_layout)
        controls_section.addWidget(audio_controls_group, 7)
        
        # Recording time display in a styled frame
        timer_frame = QGroupBox("Recording Time")
        timer_layout = QVBoxLayout()
        
        # Add recording time label
        self.recording_time_label = QLabel("00:00")
        self.recording_time_label.setAlignment(Qt.AlignCenter)
        self.recording_time_label.setStyleSheet("""
            font-size: 36px;
            font-weight: bold;
            color: #333;
            padding: 10px;
        """)
        timer_layout.addWidget(self.recording_time_label)
        timer_frame.setLayout(timer_layout)
        
        # Add timer to the right side
        controls_section.addWidget(timer_frame, 3)
        
        main_layout.addLayout(controls_section)
        
        # === ACTION BUTTONS GROUP ===
        action_group = QGroupBox("Actions")
        action_layout = QHBoxLayout()
        
        # Transcribe button
        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.setIcon(QIcon.fromTheme("document-edit", QIcon.fromTheme("edit-paste")))
        self.transcribe_btn.setToolTip("Transcribe recorded audio")
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setMinimumHeight(40)
        self.transcribe_btn.setMinimumWidth(120)
        self.transcribe_btn.clicked.connect(self.transcribe_audio)
        action_layout.addWidget(self.transcribe_btn)
        
        # AI Optimize button
        self.optimize_btn = QPushButton("AI Optimize")
        self.optimize_btn.setIcon(QIcon.fromTheme("edit-find-replace", QIcon.fromTheme("system-run")))
        self.optimize_btn.setToolTip("Optimize transcription with AI")
        self.optimize_btn.setMinimumHeight(40)
        self.optimize_btn.setMinimumWidth(120)
        self.optimize_btn.clicked.connect(self.optimize_text)
        action_layout.addWidget(self.optimize_btn)
        
        # Transcribe & Optimize button
        self.all_in_one_btn = QPushButton("Transcribe And Optimise")
        self.all_in_one_btn.setIcon(QIcon.fromTheme("system-run", QIcon.fromTheme("emblem-default")))
        self.all_in_one_btn.setToolTip("Transcribe and optimize audio in one step")
        self.all_in_one_btn.setEnabled(True)
        self.all_in_one_btn.setMinimumHeight(40)
        self.all_in_one_btn.setMinimumWidth(160)
        self.all_in_one_btn.clicked.connect(self.stop_transcribe_and_optimize)
        action_layout.addWidget(self.all_in_one_btn)
        
        action_group.setLayout(action_layout)
        main_layout.addWidget(action_group)
        
        # Text area
        self.text_edit = QTextEdit()
        main_layout.addWidget(self.text_edit)
        
        # Create settings tab
        settings_tab = self._create_settings_tab()
        self.tab_widget.addTab(settings_tab, "Settings")

        # Create about tab
        about_tab = self._create_about_tab()
        self.tab_widget.addTab(about_tab, "About")

    def _ensure_config_dir(self):
        """Ensure the config directory exists"""
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR, exist_ok=True)
    
    def _load_settings(self):
        """Load settings from config file"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading settings: {e}")
        return {}
    
    def _save_settings(self):
        """Save settings to config file"""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.settings, f)
        except Exception as e:
            print(f"Error saving settings: {e}")
    
    def populate_audio_devices(self):
        """Populate the audio devices dropdown"""
        # Clear existing items
        self.audio_device_combo.clear()
        if hasattr(self, 'audio_device_combo_settings'):
            self.audio_device_combo_settings.clear()

        print("Available audio devices:")
        devices = sd.query_devices()
        self.device_indices = {}
        
        for i, device in enumerate(devices):
            print(f"{i}: {device['name']} (inputs: {device['max_input_channels']})")
            # Only add devices that have input channels
            if device['max_input_channels'] > 0:
                self.device_indices[device['name']] = i
                self.audio_device_combo.addItem(device['name'])
                if hasattr(self, 'audio_device_combo_settings'):
                    self.audio_device_combo_settings.addItem(device['name'])
        
        print("Device indices:", self.device_indices)
        print(f"Added {len(self.device_indices)} devices to combo boxes")

        # Set the default device if it exists in settings
        if self.settings.get('default_device'):
            default_device = self.settings['default_device']
            if default_device in self.device_indices:
                self.audio_device_combo.setCurrentText(default_device)
                if hasattr(self, 'audio_device_combo_settings'):
                    self.audio_device_combo_settings.setCurrentText(default_device)

    def save_audio_device(self):
        """Save the selected audio device"""
        device = self.audio_device_combo.currentText()
        if device:
            self.settings['audio_device'] = device
            self._save_settings()
            self.update_status(f"Audio device '{device}' saved as current device")
    
    def save_default_audio_device(self):
        """Save the default audio device"""
        device = self.audio_device_combo_settings.currentText()
        if device:
            self.settings['default_device'] = device
            self._save_settings()
            self.update_status(f"Audio device '{device}' saved as default device")
            
            # Also update the main combo box
            index = self.audio_device_combo.findText(device)
            if index >= 0:
                self.audio_device_combo.setCurrentIndex(index)
    
    def save_api_key(self):
        """Save the API key"""
        api_key = self.api_key_input.text().strip()
        if api_key:
            self.settings['api_key'] = api_key
            self._save_settings()
            
            # Update the global API key
            global OPENAI_API_KEY
            OPENAI_API_KEY = api_key
            
            self.update_status("API key saved")
        else:
            self.update_status("API key cannot be empty")

    def _save_app_settings(self):
        """Save application settings"""
        self.settings["minimize_to_tray"] = self.minimize_to_tray_checkbox.isChecked()
        self._save_settings()
        self.statusBar.showMessage("Application settings saved", 3000)

    def start_recording(self):
        """Start audio recording"""
        device = self.audio_device_combo.currentText()
        if device:
            try:
                # Get device index from our stored mapping
                if device in self.device_indices:
                    device_id = self.device_indices[device]
                    print(f"Selected device: {device} (ID: {device_id})")
                    
                    # Set the device and start recording
                    sd.default.device = device_id
                    self.recorder.start()
                    self.record_btn.setEnabled(False)
                    self.pause_btn.setEnabled(True)
                    self.stop_btn.setEnabled(True)
                    self.clear_btn.setEnabled(False)
                    self.transcribe_btn.setEnabled(False)
                    self.all_in_one_btn.setEnabled(True)  # Enable the all-in-one button
                else:
                    self.update_status(f"Audio device '{device}' not found in device mapping")
                    print(f"Device '{device}' not found in mapping: {self.device_indices}")
            except Exception as e:
                self.update_status(f"Error starting recording: {e}")
                print(f"Recording error details: {str(e)}")
        else:
            self.update_status("No audio device selected")

    def stop_recording(self):
        """Stop audio recording"""
        if self.recorder.isRunning():
            self.recorder.stop()
            self.recorder.wait()
            self.record_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.clear_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(True)
            self.all_in_one_btn.setEnabled(True)
            self.update_status("Recording stopped")
            print("Recording stopped")

    def pause_recording(self):
        """Pause or resume audio recording"""
        if self.recorder.isRunning():
            # Toggle pause state directly in the recorder
            self.recorder.pause()
            
            # Update UI based on the new pause state
            if self.recorder.paused:
                self.pause_btn.setIcon(QIcon.fromTheme("media-playback-start"))
                self.pause_btn.setToolTip("Resume")
                self.update_status("Recording paused...")
            else:
                self.pause_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
                self.pause_btn.setToolTip("Pause")
                self.update_status("Recording resumed...")

    def clear_recording(self):
        """Clear the current recording"""
        self.recorder.clear()
        self.clear_btn.setEnabled(False)
        self.transcribe_btn.setEnabled(False)
    
    def transcribe_audio(self):
        """Transcribe the recorded audio"""
        audio_file = self.recorder.get_audio_file()
        if not audio_file:
            self.update_status("No audio file available for transcription")
            return
        
        self.transcription_worker = TranscriptionWorker(audio_file, OPENAI_API_KEY)
        self.transcription_worker.transcription_complete.connect(self.handle_transcription_complete)
        self.transcription_worker.transcription_error.connect(self.handle_transcription_error)
        self.transcription_worker.transcription_progress.connect(self.handle_transcription_progress)
        self.transcription_worker.update_status.connect(self.update_status)
        self.transcription_worker.start()
        
        self.transcribe_btn.setEnabled(False)
        self.update_status("Transcription started...")
    
    def handle_transcription_progress(self, current, total):
        """Handle transcription progress updates"""
        self.update_status(f"Transcribing chunk {current} of {total}...")
    
    def handle_transcription_complete(self, transcript):
        """Handle completed transcription"""
        if transcript:
            # Update the text editor with the transcribed text
            if not self.append_transcriptions:
                self.text_edit.setText(transcript)
            else:
                current_text = self.text_edit.toPlainText()
                self.text_edit.setText(f"{current_text}\n\n--- New Transcription ---\n\n{transcript}")
            
            self.update_status("Transcription completed successfully")
            
            # Store the last transcript for potential optimization
            self.last_transcript = transcript
        else:
            self.update_status("Transcription completed but no text was returned")
        
        self.transcribe_btn.setEnabled(True)
        
        # If this was called from stop_transcribe_and_optimize, continue with optimization
        if self.continue_with_optimization:
            self.continue_with_optimization = False
            self.optimize_text()
    
    def handle_transcription_error(self, error):
        """Handle transcription error"""
        QMessageBox.critical(self, "Transcription Error", error)
        self.update_status(f"Transcription error: {error}")
        self.transcribe_btn.setEnabled(True)
    
    def optimize_text(self):
        """Optimize the text using OpenAI API"""
        text = self.text_edit.toPlainText()
        if not text:
            self.update_status("No text to optimize")
            return
        
        # Create and start the optimization worker
        self.optimization_worker = OptimizationWorker(text, OPENAI_API_KEY)
        self.optimization_worker.optimization_complete.connect(self.handle_optimization_complete)
        self.optimization_worker.optimization_error.connect(self.handle_optimization_error)
        self.optimization_worker.update_status.connect(self.update_status)
        self.optimization_worker.start()
        
        self.optimize_btn.setEnabled(False)
        self.update_status("Text optimization started...")
    
    def handle_optimization_complete(self, optimized_text):
        """Handle completed text optimization"""
        if optimized_text:
            # Replace the text with the optimized version
            self.text_edit.setText(optimized_text)
            self.update_status("Text optimization completed successfully")
        else:
            self.update_status("Optimization completed but no text was returned")
        
        self.optimize_btn.setEnabled(True)
    
    def handle_optimization_error(self, error):
        """Handle optimization error"""
        QMessageBox.critical(self, "Optimization Error", error)
        self.update_status(f"Optimization error: {error}")
        self.optimize_btn.setEnabled(True)
    
    def stop_transcribe_and_optimize(self):
        """Stop recording, transcribe, and optimize the text"""
        if self.recorder.isRunning():
            # First stop the recording
            self.recorder.stop()
            self.recorder.wait()
            self.record_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.clear_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(True)
            self.all_in_one_btn.setEnabled(True)
            
            # Then transcribe and optimize
            self.update_status("Transcribing and optimizing...")
            self.continue_with_optimization = True
            self.transcribe_audio()
        else:
            # Check if there's a recording available
            audio_file = self.recorder.get_audio_file()
            if audio_file and os.path.exists(audio_file):
                # If there's a recording, transcribe and optimize it
                self.update_status("Transcribing and optimizing existing recording...")
                self.continue_with_optimization = True
                self.transcribe_audio()
            elif self.text_edit.toPlainText():
                # If there's text in the editor but no recording, just optimize the text
                self.optimize_text()
            else:
                self.update_status("No recording or text available to process")

    def copy_to_clipboard(self):
        """Copy text to clipboard"""
        text = self.text_edit.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            self.update_status("Text copied to clipboard")
        else:
            self.update_status("No text to copy")
    
    def clear_text(self):
        """Clear the text in the text editor"""
        if self.text_edit.toPlainText():
            reply = QMessageBox.question(
                self, 
                "Clear Text", 
                "Are you sure you want to clear all text?",
                QMessageBox.Yes | QMessageBox.No, 
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.text_edit.clear()
                self.update_status("Text cleared")
        else:
            self.update_status("No text to clear")
    
    def download_as_markdown(self):
        """Save text as markdown file"""
        text = self.text_edit.toPlainText()
        if not text:
            self.update_status("No text to save")
            return
        
        try:
            # Generate filename with timestamp
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            default_filename = f"transcription_{timestamp}.md"
            
            # Get save location from user
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save as Markdown", default_filename, "Markdown Files (*.md)"
            )
            
            if file_path:
                # Ensure file has .md extension
                if not file_path.lower().endswith('.md'):
                    file_path += '.md'
                
                # Write the file with explicit encoding
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                
                self.update_status(f"Text saved to {file_path}")
                print(f"Successfully saved markdown to: {file_path}")
        except Exception as e:
            error_msg = f"Error saving file: {str(e)}"
            QMessageBox.critical(self, "Save Error", error_msg)
            self.update_status(error_msg)
            print(f"Error details: {str(e)}")

    def update_status(self, message):
        """Update status bar with message"""
        if self.myStatusBar:
            self.myStatusBar.showMessage(message)
        else:
            print("Attempted to update status bar after it was deleted")
        print(message)  

    def update_timer(self, seconds):
        """Update recording time display"""
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            time_str = f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            time_str = f"{minutes:02d}:{secs:02d}"
            
        self.recording_time_label.setText(time_str)
        print(f"UI timer updated: {time_str}")
        
    def update_volume_meter(self, level):
        """Update volume meter with current audio level"""
        # Function kept for compatibility, but volume meter UI element has been removed
        pass

    def closeEvent(self, event: QCloseEvent):
        """Override close event to minimize to tray instead of closing"""
        if self.settings.get("minimize_to_tray", True):
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Linux Cloud STT Notepad",
                "Application minimized to system tray. Double-click the icon to restore.",
                QSystemTrayIcon.Information,
                2000
            )
        else:
            # Save any pending changes
            self._save_settings()
            
            # Accept the close event
            event.accept()

    def create_tray_icon(self):
        """Create the system tray icon and menu"""
        # Create tray icon
        self.tray_icon = QSystemTrayIcon(self)
        
        # Create a simple programmatic icon
        from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QBrush
        from PyQt5.QtCore import Qt, QSize
        
        # Create a pixmap
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        
        # Create a painter to draw on the pixmap
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw a simple microphone icon
        # Draw the microphone body (a rectangle with rounded corners)
        painter.setPen(QPen(QColor(0, 0, 0)))
        painter.setBrush(QBrush(QColor(80, 80, 80)))
        painter.drawRoundedRect(5, 2, 6, 8, 2, 2)
        
        # Draw the microphone base
        painter.drawRect(7, 10, 2, 2)
        painter.drawRoundedRect(4, 12, 8, 2, 1, 1)
        
        painter.end()
        
        # Set the icon
        self.tray_icon.setIcon(QIcon(pixmap))
        
        # Create tray menu
        tray_menu = QMenu()
        
        # Add actions to the menu
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show)
        
        hide_action = QAction("Hide", self)
        hide_action.triggered.connect(self.hide)
        
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.quit_application)
        
        # Add actions to menu
        tray_menu.addAction(show_action)
        tray_menu.addAction(hide_action)
        tray_menu.addSeparator()
        tray_menu.addAction(exit_action)
        
        # Set the menu for the tray icon
        self.tray_icon.setContextMenu(tray_menu)
        
        # Show the tray icon
        self.tray_icon.show()
        
        # Connect activated signal (for double-click)
        self.tray_icon.activated.connect(self.tray_icon_activated)
    
    def tray_icon_activated(self, reason):
        """Handle tray icon activation (click, double-click)"""
        # Respond to both single clicks and double clicks
        if reason == QSystemTrayIcon.Trigger or reason == QSystemTrayIcon.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.activateWindow()
    
    def quit_application(self):
        """Quit the application completely"""
        self.tray_icon.hide()
        QApplication.quit()
    
    def _create_settings_tab(self):
        """Create the Settings tab with audio device and API key settings"""
        settings_tab = QWidget()
        settings_layout = QVBoxLayout()
        settings_tab.setLayout(settings_layout)

        # Audio Device Settings
        audio_group = QGroupBox("Audio Device Settings")
        audio_layout = QFormLayout()
        audio_group.setLayout(audio_layout)

        # Device selection
        self.audio_device_combo_settings = QComboBox()
        audio_layout.addRow("Select Audio Device:", self.audio_device_combo_settings)

        # Save device button
        save_device_btn = QPushButton("Save Selected Device")
        save_device_btn.clicked.connect(self.save_audio_device)
        audio_layout.addRow("", save_device_btn)

        # Set as default checkbox and button
        default_layout = QHBoxLayout()
        save_default_btn = QPushButton("Set as Default Device")
        save_default_btn.clicked.connect(self.save_default_audio_device)
        default_layout.addWidget(save_default_btn)
        audio_layout.addRow("", default_layout)

        settings_layout.addWidget(audio_group)

        # API Key Settings
        api_group = QGroupBox("API Key Settings")
        api_layout = QFormLayout()
        api_group.setLayout(api_layout)

        # API Key input
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        if OPENAI_API_KEY:
            self.api_key_input.setText(OPENAI_API_KEY)
        api_layout.addRow("OpenAI API Key:", self.api_key_input)

        # Save API key button
        save_api_btn = QPushButton("Save API Key")
        save_api_btn.clicked.connect(self.save_api_key)
        api_layout.addRow("", save_api_btn)

        settings_layout.addWidget(api_group)

        # Add stretch to push everything to the top
        settings_layout.addStretch()

        return settings_tab

    def _create_about_tab(self):
        """Create the About tab with information about the application"""
        about_tab = QWidget()
        about_layout = QVBoxLayout(about_tab)
        
        # Application title
        title_label = QLabel("Linux Cloud STT Notepad")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        title_label.setAlignment(Qt.AlignCenter)
        about_layout.addWidget(title_label)
        
        # Version info
        version_label = QLabel("Version 1.0.0")
        version_label.setAlignment(Qt.AlignCenter)
        about_layout.addWidget(version_label)
        
        # Description
        description_text = """
        <p>A minimalist notepad with speech-to-text capabilities using the OpenAI API for Linux desktop environments.</p>
        
        <h3>Features:</h3>
        <ul>
            <li><b>Audio Recording:</b> Record audio from any connected input device</li>
            <li><b>Speech-to-Text:</b> Transcribe recorded audio using OpenAI's Whisper API</li>
            <li><b>AI Optimization:</b> Clean and improve transcribed text by correcting typos, improving clarity, and formatting into proper paragraphs</li>
            <li><b>Text Editing:</b> Edit transcribed text directly in the application</li>
            <li><b>Export:</b> Save your notes as Markdown files</li>
        </ul>
        
        <h3>Button Functions:</h3>
        <ul>
            <li><b>Audio Controls:</b>
                <ul>
                    <li><b>Record:</b> Start audio recording</li>
                    <li><b>Pause/Resume:</b> Pause or resume recording</li>
                    <li><b>Stop:</b> Stop recording</li>
                    <li><b>Clear:</b> Clear the current recording</li>
                </ul>
            </li>
            <li><b>Actions:</b>
                <ul>
                    <li><b>Transcribe:</b> Convert recorded audio to text</li>
                    <li><b>AI Optimize:</b> Improve the quality and formatting of transcribed text using AI</li>
                    <li><b>Transcribe & Optimize:</b> Stop recording, transcribe, and optimize in one sequence</li>
                </ul>
            </li>
        </ul>
        
        <p>This application requires an OpenAI API key to function properly.</p>
        <p>&copy; 2023-2025 Daniel Rosehill</p>
        """
        
        description_label = QLabel(description_text)
        description_label.setWordWrap(True)
        description_label.setOpenExternalLinks(True)
        description_label.setTextFormat(Qt.RichText)
        
        # Add description to a scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(description_label)
        about_layout.addWidget(scroll_area)
        
        return about_tab


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
