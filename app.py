#!/usr/bin/env python3
"""
Linux Cloud STT Notepad - A minimalist notepad with speech-to-text capabilities
using the Gladia API for Linux desktop environments.
"""

import sys
import os
import json
import time
import tempfile
import datetime
import sounddevice as sd
import numpy as np
import requests
from scipy.io.wavfile import write as write_wav
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QLabel, QTextEdit, QFileDialog,
    QMessageBox, QStatusBar, QAction, QToolBar, QSplitter
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings, QDir
from PyQt5.QtGui import QIcon, QClipboard
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Gladia API key from environment
GLADIA_API_KEY = os.getenv("GLADIA_API_KEY")
if not GLADIA_API_KEY:
    print("Error: GLADIA_API_KEY not found in .env file")
    sys.exit(1)

# Constants
SAMPLE_RATE = 44100  # Sample rate for audio recording
CONFIG_DIR = os.path.join(QDir.homePath(), ".config", "linux-cloud-stt-notepad")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")


class AudioRecorder(QThread):
    """Thread for recording audio without blocking the UI"""
    update_status = pyqtSignal(str)
    
    def __init__(self, sample_rate=SAMPLE_RATE):
        super().__init__()
        self.sample_rate = sample_rate
        self.recording = False
        self.paused = False
        self.audio_data = []
        self.temp_file = None
    
    def run(self):
        """Start recording audio"""
        self.recording = True
        self.paused = False
        self.audio_data = []
        
        self.update_status.emit("Recording started...")
        
        with sd.InputStream(samplerate=self.sample_rate, channels=1, callback=self._audio_callback):
            while self.recording:
                time.sleep(0.1)
        
        if self.audio_data:
            self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
            audio_array = np.concatenate(self.audio_data, axis=0)
            write_wav(self.temp_file.name, self.sample_rate, audio_array)
            self.update_status.emit(f"Recording saved to temporary file: {self.temp_file.name}")
        else:
            self.update_status.emit("No audio data recorded")
    
    def _audio_callback(self, indata, frames, time, status):
        """Callback function for audio recording"""
        if not self.paused and self.recording:
            self.audio_data.append(indata.copy())
    
    def stop(self):
        """Stop recording"""
        self.recording = False
        self.update_status.emit("Recording stopped")
    
    def pause(self):
        """Pause recording"""
        self.paused = not self.paused
        status = "paused" if self.paused else "resumed"
        self.update_status.emit(f"Recording {status}")
    
    def clear(self):
        """Clear recorded audio data"""
        self.audio_data = []
        if self.temp_file and os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)
            self.temp_file = None
        self.update_status.emit("Recording cleared")
    
    def get_audio_file(self):
        """Return the path to the recorded audio file"""
        return self.temp_file.name if self.temp_file else None


class TranscriptionWorker(QThread):
    """Thread for handling Gladia API transcription"""
    transcription_complete = pyqtSignal(str)
    transcription_error = pyqtSignal(str)
    update_status = pyqtSignal(str)
    
    def __init__(self, audio_file, api_key):
        super().__init__()
        self.audio_file = audio_file
        self.api_key = api_key
    
    def run(self):
        """Start the transcription process"""
        if not self.audio_file or not os.path.exists(self.audio_file):
            self.transcription_error.emit("No audio file available for transcription")
            return
        
        try:
            self.update_status.emit("Sending audio to Gladia API...")
            
            # Prepare the API request
            url = "https://api.gladia.io/v2/transcription/"
            headers = {
                "x-gladia-key": self.api_key,
                "Accept": "application/json",
            }
            
            # Send the audio file
            with open(self.audio_file, 'rb') as f:
                files = {'audio': f}
                response = requests.post(url, headers=headers, files=files)
            
            if response.status_code != 200:
                self.transcription_error.emit(f"API Error: {response.status_code} - {response.text}")
                return
            
            result_data = response.json()
            result_url = result_data.get('result_url')
            
            if not result_url:
                self.transcription_error.emit("No result URL in API response")
                return
            
            # Poll for results
            self.update_status.emit("Waiting for transcription results...")
            while True:
                poll_response = requests.get(result_url, headers=headers)
                poll_data = poll_response.json()
                
                if poll_data.get('status') == "done":
                    transcript = poll_data.get('result', {}).get('transcription', {}).get('full_transcript', "")
                    self.transcription_complete.emit(transcript)
                    break
                elif poll_data.get('status') == "error":
                    self.transcription_error.emit(f"Transcription error: {poll_data.get('error', 'Unknown error')}")
                    break
                else:
                    self.update_status.emit(f"Transcription status: {poll_data.get('status', 'processing')}")
                    time.sleep(1)
                    
        except Exception as e:
            self.transcription_error.emit(f"Error during transcription: {str(e)}")


class MainWindow(QMainWindow):
    """Main application window"""
    def __init__(self):
        super().__init__()
        
        # Initialize settings
        self._ensure_config_dir()
        self.settings = self._load_settings()
        
        # Initialize UI
        self.init_ui()
        
        # Initialize audio recorder
        self.recorder = AudioRecorder()
        self.recorder.update_status.connect(self.update_status)
        
        # Initialize transcription worker
        self.transcription_worker = None
        
        # Populate audio devices
        self.populate_audio_devices()
        
        # Set selected audio device if saved
        if 'audio_device' in self.settings:
            index = self.audio_device_combo.findText(self.settings['audio_device'])
            if index >= 0:
                self.audio_device_combo.setCurrentIndex(index)
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Linux Cloud STT Notepad")
        self.setGeometry(100, 100, 800, 600)
        
        # Create central widget and main layout
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        
        # Audio device selection
        audio_device_layout = QHBoxLayout()
        audio_device_layout.addWidget(QLabel("Audio Input Source:"))
        self.audio_device_combo = QComboBox()
        audio_device_layout.addWidget(self.audio_device_combo)
        save_device_btn = QPushButton("Save")
        save_device_btn.clicked.connect(self.save_audio_device)
        audio_device_layout.addWidget(save_device_btn)
        main_layout.addLayout(audio_device_layout)
        
        # Recording controls
        recording_layout = QHBoxLayout()
        self.record_btn = QPushButton("Record")
        self.record_btn.clicked.connect(self.start_recording)
        recording_layout.addWidget(self.record_btn)
        
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.pause_recording)
        self.pause_btn.setEnabled(False)
        recording_layout.addWidget(self.pause_btn)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_recording)
        self.stop_btn.setEnabled(False)
        recording_layout.addWidget(self.stop_btn)
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_recording)
        self.clear_btn.setEnabled(False)
        recording_layout.addWidget(self.clear_btn)
        
        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.clicked.connect(self.transcribe_audio)
        self.transcribe_btn.setEnabled(False)
        recording_layout.addWidget(self.transcribe_btn)
        
        main_layout.addLayout(recording_layout)
        
        # Text area
        self.text_edit = QTextEdit()
        main_layout.addWidget(self.text_edit)
        
        # Text controls
        text_controls_layout = QHBoxLayout()
        
        self.copy_btn = QPushButton("Copy to Clipboard")
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        text_controls_layout.addWidget(self.copy_btn)
        
        self.download_btn = QPushButton("Download as Markdown")
        self.download_btn.clicked.connect(self.download_as_markdown)
        text_controls_layout.addWidget(self.download_btn)
        
        main_layout.addLayout(text_controls_layout)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        
        # Set central widget
        self.setCentralWidget(central_widget)
    
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
            devices = sd.query_devices()
            input_devices = [d['name'] for d in devices if d['max_input_channels'] > 0]
            self.audio_device_combo.clear()
            self.audio_device_combo.addItems(input_devices)
        except Exception as e:
            self.update_status(f"Error listing audio devices: {e}")
    
    def save_audio_device(self):
        """Save the selected audio device"""
        device = self.audio_device_combo.currentText()
        if device:
            self.settings['audio_device'] = device
            self._save_settings()
            self.update_status(f"Audio device '{device}' saved")
    
    def start_recording(self):
        """Start audio recording"""
        # Set the audio device
        device = self.audio_device_combo.currentText()
        if device:
            try:
                device_list = sd.query_devices()
                device_id = None
                for i, d in enumerate(device_list):
                    if d['name'] == device and d['max_input_channels'] > 0:
                        device_id = i
                        break
                
                if device_id is not None:
                    sd.default.device = device_id
                    self.recorder.start()
                    self.record_btn.setEnabled(False)
                    self.pause_btn.setEnabled(True)
                    self.stop_btn.setEnabled(True)
                    self.clear_btn.setEnabled(False)
                    self.transcribe_btn.setEnabled(False)
                else:
                    self.update_status(f"Audio device '{device}' not found")
            except Exception as e:
                self.update_status(f"Error starting recording: {e}")
        else:
            self.update_status("No audio device selected")
    
    def pause_recording(self):
        """Pause or resume audio recording"""
        if self.recorder.isRunning():
            self.recorder.pause()
            self.pause_btn.setText("Resume" if self.recorder.paused else "Pause")
    
    def stop_recording(self):
        """Stop audio recording"""
        if self.recorder.isRunning():
            self.recorder.stop()
            self.recorder.wait()
            self.record_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.pause_btn.setText("Pause")
            self.stop_btn.setEnabled(False)
            self.clear_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(True)
    
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
        
        self.transcription_worker = TranscriptionWorker(audio_file, GLADIA_API_KEY)
        self.transcription_worker.transcription_complete.connect(self.handle_transcription_complete)
        self.transcription_worker.transcription_error.connect(self.handle_transcription_error)
        self.transcription_worker.update_status.connect(self.update_status)
        self.transcription_worker.start()
        
        self.transcribe_btn.setEnabled(False)
        self.update_status("Transcription started...")
    
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
    
    def download_as_markdown(self):
        """Save text as markdown file"""
        text = self.text_edit.toPlainText()
        if not text:
            self.update_status("No text to save")
            return
        
        # Generate filename with timestamp
        timestamp = datetime.datetime.now().strftime("%d%m%Y_%H%M")
        default_filename = f"{timestamp}_transcribed.md"
        
        # Get save location from user
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save as Markdown", default_filename, "Markdown Files (*.md)"
        )
        
        if file_path:
            try:
                with open(file_path, 'w') as f:
                    f.write(text)
                self.update_status(f"Text saved to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Save Error", f"Error saving file: {e}")
                self.update_status(f"Error saving file: {e}")
    
    def update_status(self, message):
        """Update status bar with message"""
        self.status_bar.showMessage(message)
        print(message)  # Also print to console for debugging
    
    def closeEvent(self, event):
        """Handle window close event"""
        # Stop recording if active
        if self.recorder.isRunning():
            self.recorder.stop()
            self.recorder.wait()
        
        # Clean up temporary files
        if self.recorder.temp_file and os.path.exists(self.recorder.temp_file.name):
            try:
                os.unlink(self.recorder.temp_file.name)
            except:
                pass
        
        # Accept the close event
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
