#!/usr/bin/env python3
"""
Linux Cloud STT Notepad - A minimalist notepad with speech-to-text capabilities
using the OpenAI API for Linux desktop environments.
"""

import sys
import os
import json
import time
import tempfile
import datetime
import base64
import sounddevice as sd
import numpy as np
import math
import requests
from scipy.io.wavfile import write as write_wav
from pydub import AudioSegment
import ffmpeg
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QLabel, QTextEdit, QFileDialog,
    QMessageBox, QStatusBar, QAction, QToolBar, QSplitter, QProgressBar,
    QTabWidget, QLineEdit, QGridLayout, QGroupBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings, QDir, QTimer
from PyQt5.QtGui import QIcon, QClipboard, QColor
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables from .env file
load_dotenv()

# OpenAI API key from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("Error: OPENAI_API_KEY not found in .env file")
    sys.exit(1)

# Configure OpenAI client

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
            write_wav(temp_wav.name, self.sample_rate, audio_array)
            
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
        self.max_chunk_duration = 300  # 5 minutes per chunk in seconds
        self.chunk_overlap = 5  # 5 seconds overlap between chunks
        
    def run(self):
        """Start the transcription process"""
        if not self.audio_file or not os.path.exists(self.audio_file):
            self.transcription_error.emit("No audio file available for transcription")
            return
        
        try:
            self.update_status.emit("Preparing audio for OpenAI API...")
            # Initialize OpenAI client
            client = OpenAI(api_key=self.api_key)
            
            # Check audio duration
            audio = AudioSegment.from_file(self.audio_file)  # Line 191
            duration_seconds = len(audio) / 1000  # pydub uses milliseconds
            
            # If audio is short enough, transcribe directly
            if duration_seconds <= self.max_chunk_duration:
                self.update_status.emit("Sending request to OpenAI API...")
                transcript = self._transcribe_file(client, self.audio_file)
                if transcript:
                    self.transcription_complete.emit(transcript)
                    self.update_status.emit("Transcription completed successfully")
                else:
                    self.transcription_error.emit("No transcription returned from API")
            else:
                # For longer audio, split into chunks and transcribe each
                self.update_status.emit(f"Audio duration: {duration_seconds:.1f} seconds. Splitting into chunks...")
                transcripts = self._transcribe_long_audio(client, audio, duration_seconds)
                if transcripts:
                    # Combine all transcripts
                    full_transcript = " ".join(transcripts)
                    self.transcription_complete.emit(full_transcript)
                    self.update_status.emit("Transcription of all chunks completed successfully")
                else:
                    self.transcription_error.emit("Failed to transcribe audio chunks")
                
        except Exception as e:
            self.transcription_error.emit(f"Error during transcription: {str(e)}")
    
    def _transcribe_file(self, client, file_path):
        """Transcribe a single audio file using the OpenAI API"""
        try:
            # Following the documentation example
            with open(file_path, "rb") as audio_file:
                # Use the OpenAI API to transcribe the audio
                response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"  # Explicitly request text format
                )
                
            # Check if response has text attribute
            if hasattr(response, 'text'):
                return response.text
            elif isinstance(response, str):
                return response
            elif response is not None:  # Log the raw response if it's not None and not text
                print(f"Raw API response: {response}")
            else:
                print(f"Unexpected response format: {response}")  # Line 239
                return None
                
        except Exception as e:
            error_msg = f"Error transcribing file: {str(e)}"
            self.update_status.emit(error_msg)
            print(f"File path for transcription: {file_path}")  # Print file path in case of error
            print(f"Transcription error details: {error_msg}")
            import traceback  # Line 246
            traceback.print_exc()
            return None
            
    def _transcribe_long_audio(self, client, audio, duration_seconds):
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
                transcript = self._transcribe_file(client, chunk_file)
                if transcript:
                    transcripts.append(transcript)
            
            # Clean up temp files
            for file in chunk_files:
                os.unlink(file)
            os.rmdir(temp_dir)
            
            return transcripts
        except Exception as e:
            self.update_status.emit(f"Error processing long audio: {str(e)}")
            return []


class MainWindow(QMainWindow):
    """Main application window"""
    def __init__(self):
        super().__init__()
        
        # Initialize settings
        self._ensure_config_dir()
        self.settings = self._load_settings()
        
        # Initialize device indices mapping
        self.device_indices = {}
        
        # Initialize UI
        self.init_ui()
        
        # Initialize audio recorder
        self.recorder = AudioRecorder()
        self.recorder.update_status.connect(self.update_status)
        self.recorder.update_volume.connect(self.update_volume_meter)
        self.recorder.update_timer.connect(self.update_timer)
        
        # Initialize transcription worker
        self.transcription_worker = None
        
        # Populate audio devices - do this after UI is initialized
        self.populate_audio_devices()
        
        # Set selected audio device if saved
        if 'audio_device' in self.settings:
            index = self.audio_device_combo.findText(self.settings['audio_device'])
            if index >= 0:
                self.audio_device_combo.setCurrentIndex(index)
                
            # Also set in the settings tab
            index = self.default_audio_device_combo.findText(self.settings['audio_device'])
            if index >= 0:
                self.default_audio_device_combo.setCurrentIndex(index)
        
        # Set API key if saved
        if 'api_key' in self.settings and self.api_key_input:
            self.api_key_input.setText(self.settings['api_key'])

    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Linux Cloud STT Notepad")
        self.setGeometry(100, 100, 800, 600)
        
        # Create central widget with tab widget
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        
        # Create tab widget
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)
        
        # Create main tab
        main_tab = QWidget()
        main_tab_layout = QVBoxLayout(main_tab)
        
        # Audio device selection
        audio_device_layout = QHBoxLayout()
        audio_device_layout.addWidget(QLabel("Audio Input Source:"))
        self.audio_device_combo = QComboBox()
        audio_device_layout.addWidget(self.audio_device_combo)
        save_device_btn = QPushButton("Save")
        save_device_btn.clicked.connect(self.save_audio_device)
        audio_device_layout.addWidget(save_device_btn)
        main_tab_layout.addLayout(audio_device_layout)
        
        # Recording controls and timer in a horizontal layout
        recording_controls_layout = QHBoxLayout()
        
        # Recording buttons in a vertical layout
        recording_layout = QVBoxLayout()
        
        # First row of buttons
        buttons_row1 = QHBoxLayout()
        self.record_btn = QPushButton()
        self.record_btn.setIcon(QIcon.fromTheme("media-record", QIcon.fromTheme("media-playback-start")))
        self.record_btn.setToolTip("Record")
        self.record_btn.clicked.connect(self.start_recording)
        self.record_btn.setMinimumHeight(40)
        self.record_btn.setMinimumWidth(40)
        buttons_row1.addWidget(self.record_btn)
        
        self.pause_btn = QPushButton()
        self.pause_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
        self.pause_btn.setToolTip("Pause")
        self.pause_btn.clicked.connect(self.pause_recording)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setMinimumHeight(40)
        self.pause_btn.setMinimumWidth(40)
        buttons_row1.addWidget(self.pause_btn)
        
        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(QIcon.fromTheme("media-playback-stop"))
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.clicked.connect(self.stop_recording)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setMinimumWidth(40)
        buttons_row1.addWidget(self.stop_btn)
        
        recording_layout.addLayout(buttons_row1)
        
        # Second row of buttons
        buttons_row2 = QHBoxLayout()
        self.clear_btn = QPushButton()
        self.clear_btn.setIcon(QIcon.fromTheme("edit-clear", QIcon.fromTheme("edit-delete")))
        self.clear_btn.setToolTip("Clear")
        self.clear_btn.clicked.connect(self.clear_recording)
        self.clear_btn.setEnabled(False)
        self.clear_btn.setMinimumHeight(40)
        self.clear_btn.setMinimumWidth(40)
        buttons_row2.addWidget(self.clear_btn)
        
        # Replace the transcribe button with a more descriptive one
        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.setIcon(QIcon.fromTheme("document-edit", QIcon.fromTheme("edit-paste")))
        self.transcribe_btn.clicked.connect(self.transcribe_audio)
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setMinimumHeight(40)
        buttons_row2.addWidget(self.transcribe_btn)
        
        # Replace Stop & Transcribe with a clearer button
        self.stop_and_transcribe_btn = QPushButton("Transcribe Now")
        self.stop_and_transcribe_btn.setIcon(QIcon.fromTheme("document-save", QIcon.fromTheme("document-send")))
        self.stop_and_transcribe_btn.clicked.connect(self.stop_and_transcribe)
        self.stop_and_transcribe_btn.setEnabled(False)
        self.stop_and_transcribe_btn.setMinimumHeight(40)
        buttons_row2.addWidget(self.stop_and_transcribe_btn)
        
        recording_layout.addLayout(buttons_row2)
        
        # Add recording controls to the left side
        recording_controls_layout.addLayout(recording_layout, 7)
        
        # Recording time display in a styled frame
        timer_frame = QGroupBox("Recording Time")
        timer_layout = QVBoxLayout()
        
        # Add volume meter
        volume_layout = QHBoxLayout()
        volume_layout.addWidget(QLabel("Volume:"))
        self.volume_meter = QProgressBar()
        self.volume_meter.setRange(0, 100)
        self.volume_meter.setValue(0)
        self.volume_meter.setTextVisible(False)
        self.volume_meter.setMinimumWidth(150)
        self.volume_meter.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 5px;
                background: #f0f0f0;
                height: 15px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1: 0, y1: 0.5, x2: 1, y2: 0.5, 
                                                 stop: 0 #0a0, stop: 0.6 #0f0, stop: 1 #f00);
                border-radius: 5px;
            }
        """)
        volume_layout.addWidget(self.volume_meter)
        timer_layout.addLayout(volume_layout)
        
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
        recording_controls_layout.addWidget(timer_frame, 3)
        
        main_tab_layout.addLayout(recording_controls_layout)
        
        # Text area
        self.text_edit = QTextEdit()
        main_tab_layout.addWidget(self.text_edit)
        
        # Text controls
        text_controls_layout = QHBoxLayout()
        
        self.copy_btn = QPushButton("Copy to Clipboard")
        self.copy_btn.setIcon(QIcon.fromTheme("edit-copy"))
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        text_controls_layout.addWidget(self.copy_btn)
        
        # Add clear text button
        self.clear_text_btn = QPushButton("Clear Text")
        self.clear_text_btn.setIcon(QIcon.fromTheme("edit-clear"))
        self.clear_text_btn.clicked.connect(self.clear_text)
        text_controls_layout.addWidget(self.clear_text_btn)
        
        self.download_btn = QPushButton("Download as Markdown")
        self.download_btn.setIcon(QIcon.fromTheme("document-save"))
        self.download_btn.clicked.connect(self.download_as_markdown)
        text_controls_layout.addWidget(self.download_btn)
        
        main_tab_layout.addLayout(text_controls_layout)
        
        # Add main tab to tab widget
        self.tab_widget.addTab(main_tab, "Notepad")
        
        # Create settings tab
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        
        # API Key settings
        api_key_group = QGroupBox("API Key Settings")
        api_key_layout = QGridLayout()
        
        api_key_layout.addWidget(QLabel("OpenAI API Key:"), 0, 0)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("Enter your OpenAI API key")
        api_key_layout.addWidget(self.api_key_input, 0, 1)
        
        self.save_api_key_btn = QPushButton("Save API Key")
        self.save_api_key_btn.setIcon(QIcon.fromTheme("document-save"))
        self.save_api_key_btn.clicked.connect(self.save_api_key)
        api_key_layout.addWidget(self.save_api_key_btn, 0, 2)
        
        api_key_group.setLayout(api_key_layout)
        settings_layout.addWidget(api_key_group)
        
        # Default audio device settings
        audio_device_group = QGroupBox("Default Audio Device")
        audio_device_settings_layout = QGridLayout()
        
        audio_device_settings_layout.addWidget(QLabel("Default Audio Source:"), 0, 0)
        self.default_audio_device_combo = QComboBox()
        audio_device_settings_layout.addWidget(self.default_audio_device_combo, 0, 1)
        
        self.save_default_audio_btn = QPushButton("Save as Default")
        self.save_default_audio_btn.setIcon(QIcon.fromTheme("document-save"))
        self.save_default_audio_btn.clicked.connect(self.save_default_audio_device)
        audio_device_settings_layout.addWidget(self.save_default_audio_btn, 0, 2)
        
        audio_device_group.setLayout(audio_device_settings_layout)
        settings_layout.addWidget(audio_device_group)
        
        # Config file location
        config_group = QGroupBox("Configuration Information")
        config_layout = QVBoxLayout()
        
        config_layout.addWidget(QLabel(f"Configuration Directory: {CONFIG_DIR}"))
        config_layout.addWidget(QLabel(f"Settings File: {CONFIG_FILE}"))
        
        config_group.setLayout(config_layout)
        settings_layout.addWidget(config_group)
        
        # Add spacer to push everything to the top
        settings_layout.addStretch()
        
        # Add settings tab to tab widget
        self.tab_widget.addTab(settings_tab, "Settings")
        
        # Create About tab
        about_tab = QWidget()
        about_layout = QVBoxLayout(about_tab)
        
        # Title and version
        title_label = QLabel("V1 Linux Desktop STT")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        title_label.setAlignment(Qt.AlignCenter)
        about_layout.addWidget(title_label)
        
        # Description
        description_label = QLabel(
            "This application uses the OpenAI Whisper AI to transcribe audio. "
            "It was created because most STT apps for Linux focus on locally hosted "
            "Whisper models, and some people much prefer using them via API."
        )
        description_label.setWordWrap(True)
        description_label.setStyleSheet("font-size: 14px; margin: 20px;")
        description_label.setAlignment(Qt.AlignCenter)
        about_layout.addWidget(description_label)
        
        # Credits
        credits_group = QGroupBox("Credits")
        credits_layout = QVBoxLayout()
        
        credits_text = QLabel(
            "<b>Development by:</b> Sonnet 3.7<br>"
            "<b>Idea and Human In The Loop:</b> Daniel Rosehill<br><br>"
            "<b>Website:</b> <a href='https://danielrosehill.com'>danielrosehill.com</a>"
        )
        credits_text.setTextFormat(Qt.RichText)
        credits_text.setOpenExternalLinks(True)
        credits_text.setAlignment(Qt.AlignCenter)
        credits_text.setStyleSheet("font-size: 14px;")
        credits_layout.addWidget(credits_text)
        
        credits_group.setLayout(credits_layout)
        about_layout.addWidget(credits_group)
        
        # Add spacer to push content to the top
        about_layout.addStretch()
        
        # Add About tab to tab widget
        self.tab_widget.addTab(about_tab, "About")
        
        # Set central widget
        self.setCentralWidget(central_widget)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

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
        try:
            device_list = sd.query_devices()
            # Print device list for debugging
            print("Available audio devices:")
            for i, device in enumerate(device_list):
                print(f"{i}: {device['name']} (inputs: {device['max_input_channels']})")
            
            # Get input devices with their indices
            input_devices = []
            for i, d in enumerate(device_list):
                if d['max_input_channels'] > 0:
                    input_devices.append((i, d['name']))
            
            # Store device indices for later use
            self.device_indices = {name: idx for idx, name in input_devices}
            
            # Populate both combo boxes with device names only
            device_names = [name for _, name in input_devices]
            
            # Check if combo boxes exist before populating
            if hasattr(self, 'audio_device_combo') and self.audio_device_combo is not None:
                self.audio_device_combo.clear()
                for name in device_names:
                    self.audio_device_combo.addItem(name)
                
            if hasattr(self, 'default_audio_device_combo') and self.default_audio_device_combo is not None:
                self.default_audio_device_combo.clear()
                for name in device_names:
                    self.default_audio_device_combo.addItem(name)
                    
            # Print selected devices for debugging
            print(f"Device indices: {self.device_indices}")
            print(f"Added {len(device_names)} devices to combo boxes")
            
            # Force update of combo box display
            if hasattr(self, 'audio_device_combo') and self.audio_device_combo is not None:
                self.audio_device_combo.update()
                
            if hasattr(self, 'default_audio_device_combo') and self.default_audio_device_combo is not None:
                self.default_audio_device_combo.update()
                
        except Exception as e:
            self.update_status(f"Error populating audio devices: {e}")
            print(f"Error details: {str(e)}")
            import traceback
            traceback.print_exc()

    def save_audio_device(self):
        """Save the selected audio device"""
        device = self.audio_device_combo.currentText()
        if device:
            self.settings['audio_device'] = device
            self._save_settings()
            self.update_status(f"Audio device '{device}' saved as current device")
    
    def save_default_audio_device(self):
        """Save the default audio device"""
        device = self.default_audio_device_combo.currentText()
        if device:
            self.settings['audio_device'] = device
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
                    self.stop_and_transcribe_btn.setEnabled(True)
                    self.clear_btn.setEnabled(False)
                    self.transcribe_btn.setEnabled(False)
                else:
                    self.update_status(f"Audio device '{device}' not found in device mapping")
                    print(f"Device '{device}' not found in mapping: {self.device_indices}")
            except Exception as e:
                self.update_status(f"Error starting recording: {e}")
                print(f"Recording error details: {str(e)}")
        else:
            self.update_status("No audio device selected")

    def pause_recording(self):
        """Pause or resume audio recording"""
        if self.recorder.isRunning():
            self.recorder.pause()
            if self.recorder.paused:
                self.pause_btn.setIcon(QIcon.fromTheme("media-playback-start"))
                self.pause_btn.setToolTip("Resume")
            else:
                self.pause_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
                self.pause_btn.setToolTip("Pause")
    
    def stop_recording(self):
        """Stop audio recording"""
        if self.recorder.isRunning():
            # Reset volume meter
            self.volume_meter.setValue(0)
            self.recorder.stop()
            self.recorder.wait()
            self.record_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.pause_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
            self.pause_btn.setToolTip("Pause")
            self.stop_btn.setEnabled(False)
            self.stop_and_transcribe_btn.setEnabled(False)
            self.clear_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(True)
    
    def stop_and_transcribe(self):
        """Stop recording and immediately transcribe the audio"""
        if self.recorder.isRunning():
            # Reset volume meter
            self.volume_meter.setValue(0)
            self.recorder.stop()
            self.recorder.wait()
            self.record_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.pause_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
            self.pause_btn.setToolTip("Pause")
            self.stop_btn.setEnabled(False)
            self.stop_and_transcribe_btn.setEnabled(False)
            self.clear_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(True)
            
            # Immediately start transcription
            self.transcribe_audio()
    
    def clear_recording(self):
        """Clear the current recording"""
        self.recorder.clear()
        self.volume_meter.setValue(0)  # Reset volume meter
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
            # Append to existing text with a separator if there's already text
            if not self.text_edit.toPlainText().strip():
                self.text_edit.setText(transcript)
            else:
                current_text = self.text_edit.toPlainText()
                self.text_edit.setText(f"{current_text}\n\n--- New Transcription ---\n\n{transcript}")
            
            self.update_status("Transcription completed successfully")
        else:
            self.update_status("Transcription completed but no text was returned")
        
        self.transcribe_btn.setEnabled(True)
    
    def handle_transcription_error(self, error):
        """Handle transcription error"""
        QMessageBox.critical(self, "Transcription Error", error)
        self.update_status(f"Transcription error: {error}")
        self.transcribe_btn.setEnabled(True)
    
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
        self.status_bar.showMessage(message)
        print(message)  # Also print to console for debugging
    
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
        self.volume_meter.setValue(int(level))
        
        # Change color based on volume level
        if level > 80:  # High volume
            self.volume_meter.setStyleSheet("""
                QProgressBar {
                    border: 1px solid #ccc;
                    border-radius: 5px;
                    background: #f0f0f0;
                    height: 15px;
                }
                QProgressBar::chunk {
                    background-color: #f00;
                    border-radius: 5px;
                }
            """)
        elif level > 40:  # Medium volume
            self.volume_meter.setStyleSheet("""
                QProgressBar {
                    border: 1px solid #ccc;
                    border-radius: 5px;
                    background: #f0f0f0;
                    height: 15px;
                }
                QProgressBar::chunk {
                    background-color: #ff0;
                    border-radius: 5px;
                }
            """)
        else:  # Low volume
            self.volume_meter.setStyleSheet("""
                QProgressBar {
                    border: 1px solid #ccc;
                    border-radius: 5px;
                    background: #f0f0f0;
                    height: 15px;
                }
                QProgressBar::chunk {
                    background-color: #0a0;
                    border-radius: 5px;
                }
            """)

    def closeEvent(self, event):
        """Handle window close event"""
        # Stop recording if active
        if self.recorder.isRunning():
            self.recorder.stop()
            self.volume_meter.setValue(0)  # Reset volume meter
            self.recorder.wait()
        
        # Clean up temporary files
        if self.recorder.temp_file and os.path.exists(self.recorder.temp_file.name):
            try:
                os.unlink(self.recorder.temp_file.name)
            except:
                pass
                
        # Clean up any other temporary files that might exist
        try:
            for file in os.listdir(tempfile.gettempdir()):
                if file.endswith('.wav') or file.endswith('.mp3'):
                    if os.path.isfile(os.path.join(tempfile.gettempdir(), file)):
                        os.unlink(os.path.join(tempfile.gettempdir(), file))
        except Exception as e:
            print(f"Error cleaning up temporary files: {e}")
            pass
        
        # Accept the close event
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
